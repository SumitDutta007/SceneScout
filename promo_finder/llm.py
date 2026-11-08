"""Claude passes: per-episode judge/refine, cross-episode stitching, final ranking.

Claude never writes timestamps. It references cue IDs (e.g. "E03-C042") from the transcript
we send. We map those IDs back to times and snap them to legal cut points, so every boundary
in the output exists in the script.

Responses are cached on disk by content hash, so re-running a show costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .parse import Episode, fmt_ts
from .candidates import (Promo, Segment, make_segment, legal_starts, legal_ends, describe_hook,
                         describe_cliff, score_window, MIN_LEN, MAX_LEN)
from . import signals as sg

MODEL = "claude-opus-5-5"
CACHE_DIR = Path(".cache")
# $ per million tokens (input, output, cache read, cache write) for cost reporting
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0), "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}
USAGE = {"calls": 0, "cached_calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def usage_cost() -> dict:
    pi, po, pr, pw = PRICES.get(MODEL, PRICES["claude-opus-5-5"])
    usd = (USAGE["input"] * pi + USAGE["output"] * po + USAGE["cache_read"] * pr
           + USAGE["cache_write"] * pw) / 1e6
    return dict(USAGE, model=MODEL, usd=round(usd, 3))

GUIDE = """You are a senior short-form promo editor for vertical microdrama apps (ReelShort / DramaBox style).
A promo is a 20-60 second clip cut from the show itself and posted on TikTok / Instagram Reels / YouTube Shorts
to pull NEW viewers in. A great promo:
1. HOOK: something in the first 3 seconds that stops a scrolling thumb: a slap, a strike, a humiliating or
   shocking line, a reveal, a visceral image. Establishing shots, atmosphere and slow build-ups are NOT hooks.
2. CLIFFHANGER: the clip cuts at an unresolved moment (a threat hangs, a question goes unanswered, a reveal
   lands without the reaction), so the viewer must open the app to see what happens next. Never end on comfort
   or resolution.
3. STANDALONE: makes sense to someone who has never seen the show. The premise (who is wronged, by whom, and
   what the twist is) should be legible from the clip alone. Penalise clips that depend on names, objects or
   events the viewer hasn't been shown.
4. Usually one continuous stretch, but it may join 2-3 moments from one or several episodes, in story order.

Script conventions: each cue has an ID, a [start-end] time in mm:ss.xx, and a type: CAMERA (shot),
ACTION, SFX, or a SPEAKER (emotion) line. Segment boundaries are given as cue IDs: a segment starts at the
START of `start_cue` and ends at the END of `end_cue`. Prefer starting on a CAMERA cue (a cut) and ending
after a line has finished. Never cut a line in half.
Scores are integers 1-5 (5 = best, except spoiler_risk where 5 = gives away a major payoff)."""

SEG_SCHEMA = {
    "type": "object",
    "properties": {"start_cue": {"type": "string"}, "end_cue": {"type": "string"}},
    "required": ["start_cue", "end_cue"], "additionalProperties": False,
}
SCORES_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "integer"} for k in ("hook", "cliffhanger", "standalone", "spoiler_risk")},
    "required": ["hook", "cliffhanger", "standalone", "spoiler_risk"], "additionalProperties": False,
}
PROMO_SCHEMA = {
    "type": "object",
    "properties": {
        "based_on_candidate": {"type": "string", "description": "candidate id it refines, or 'new'"},
        "segments": {"type": "array", "items": SEG_SCHEMA},
        "hook": {"type": "string"},
        "cliffhanger": {"type": "string"},
        "why": {"type": "string", "description": "one line on why it works"},
        "cold_viewer_test": {"type": "string", "description": "what a first-time viewer understands / what confuses them"},
        "scores": SCORES_SCHEMA,
    },
    "required": ["based_on_candidate", "segments", "hook", "cliffhanger", "why", "cold_viewer_test", "scores"],
    "additionalProperties": False,
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"promos": {"type": "array", "items": PROMO_SCHEMA},
                   "rejected_candidates": {"type": "array", "items": {
                       "type": "object",
                       "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                       "required": ["id", "reason"], "additionalProperties": False}}},
    "required": ["promos", "rejected_candidates"], "additionalProperties": False,
}
RANK_SCHEMA = {
    "type": "object",
    "properties": {"order": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "post_priority": {"type": "integer"},
                       "reason": {"type": "string"}},
        "required": ["id", "post_priority", "reason"], "additionalProperties": False}},
        "strategy": {"type": "string"}},
    "required": ["order", "strategy"], "additionalProperties": False,
}


def available() -> tuple[bool, str]:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic package not installed (pip install anthropic)"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_PROFILE")):
        # the SDK can also resolve an `ant auth login` profile; try constructing a client
        try:
            import anthropic
            anthropic.Anthropic()
        except Exception as e:  # noqa: BLE001
            return False, f"no Anthropic credentials ({e.__class__.__name__})"
    return True, "ok"


# ---------------------------------------------------------------- prompt material

def series_bible(eps: list[Episode]) -> str:
    lines = ["SERIES BIBLE (episode loglines, in order):"]
    for e in eps:
        lines.append(f"Ep {e.num} ({fmt_ts(e.duration)}): {e.logline}")
    return "\n".join(lines)


def transcript(ep: Episode, t0: float = 0.0, t1: float | None = None) -> str:
    t1 = ep.duration if t1 is None else t1
    out = [f"=== EPISODE {ep.num} | duration {fmt_ts(ep.duration)} ===", f"Logline: {ep.logline}"]
    scene = None
    for c in ep.cues:
        if c.t1 <= t0 or c.t0 >= t1:
            continue
        if c.scene != scene:
            scene = c.scene
            sc = next((s for s in ep.scenes if s.num == c.scene), None)
            out.append(f"-- SCENE {c.scene}: {sc.heading if sc else ''}")
        lab = f"{c.speaker} ({c.emotion})" if c.kind == "DIALOGUE" else c.kind
        out.append(f"{c.cue_id} [{fmt_ts(c.t0)}-{fmt_ts(c.t1)}] {lab}: {c.text}")
    return "\n".join(out)


def _cand_block(cands: list[Promo]) -> str:
    rows = []
    for i, p in enumerate(cands):
        segs = "; ".join(f"ep{s.episode} {s.cue_ids[0] if s.cue_ids else '?'}→{s.cue_ids[-1] if s.cue_ids else '?'} "
                         f"({fmt_ts(s.start)}-{fmt_ts(s.end)})" for s in p.segments)
        rows.append(f"C{i}: {segs} | rule scores hook={p.scores['hook']:.2f} cliff={p.scores['cliff']:.2f} "
                    f"standalone={p.scores['standalone']:.2f}")
    return "\n".join(rows) or "(none)"


# ---------------------------------------------------------------- API call + cache

def _call(system_static: str, user: str, schema: dict, effort: str = "high") -> dict:
    key = hashlib.sha256(json.dumps([MODEL, effort, system_static, user, schema], sort_keys=True).encode()).hexdigest()[:24]
    CACHE_DIR.mkdir(exist_ok=True)
    cpath = CACHE_DIR / f"{key}.json"
    if cpath.exists():
        USAGE["cached_calls"] += 1
        return json.loads(cpath.read_text(encoding="utf-8"))["data"]
    import anthropic
    client = anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            # the stable prefix (guide + bible) is cached and reused across all calls for this show
            system=[{"type": "text", "text": system_static, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.RateLimitError as e:
        raise RuntimeError(f"rate limited: {e}") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"connection error: {e}") from e
    if msg.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("response truncated (max_tokens)")
    u = msg.usage
    rec = {"input": u.input_tokens, "output": u.output_tokens,
           "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
           "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}
    USAGE["calls"] += 1
    for k, v in rec.items():
        USAGE[k] += v
    text = next(b.text for b in msg.content if b.type == "text")
    data = json.loads(text)
    cpath.write_text(json.dumps({"usage": rec, "data": data}, indent=1), encoding="utf-8")
    return data


# ---------------------------------------------------------------- validation

def _snap(ep: Episode, t0: float, t1: float) -> tuple[float, float]:
    starts = [t for t in legal_starts(ep) if t <= t0 + 0.01] or [0.0]
    ends = [t for t in legal_ends(ep) if t >= t1 - 0.01] or [ep.duration]
    return max(starts), min(ends)


def to_promo(item: dict, cue_index: dict, eps: dict[int, Episode]) -> tuple[Promo | None, str]:
    segs: list[Segment] = []
    for s in item["segments"]:
        a, b = cue_index.get(s["start_cue"]), cue_index.get(s["end_cue"])
        if not a or not b:
            return None, f"unknown cue id {s['start_cue']}/{s['end_cue']}"
        if a.episode != b.episode:
            return None, "segment spans two episodes"
        ep = eps[a.episode]
        t0, t1 = _snap(ep, a.t0, b.t1)
        if t1 <= t0:
            return None, "end before start"
        segs.append(make_segment(ep, t0, t1))
    if not segs:
        return None, "no segments"
    for x, y in zip(segs, segs[1:]):
        if (y.episode, y.start) < (x.episode, x.end):
            return None, "segments out of story order or overlapping"
    dur = sum(s.duration for s in segs)
    if not (MIN_LEN - 0.5 <= dur <= MAX_LEN + 0.5):
        return None, f"duration {dur:.1f}s outside 20-60s"
    first, last = segs[0], segs[-1]
    e0, e1 = eps[first.episode], eps[last.episode]
    h, hr = sg.hook_score(e0, first.start)
    c, cr = sg.cliff_score(e1, last.end)
    sts = [sg.standalone_score(eps[s.episode], s.start, s.end) for s in segs]
    prs = [sg.premise_score(eps[s.episode], s.start, s.end) for s in segs]
    st = sum(x[0] for x in sts) / len(sts) - 0.05 * (len(segs) - 1)
    pr = min(1.0, sum(x[0] for x in prs))
    total = 0.35 * h + 0.30 * c + 0.20 * st + 0.15 * pr
    p = Promo("stitched" if len(segs) > 1 else "single", segs,
              {"hook": h, "cliff": c, "standalone": st, "premise": pr, "total": total},
              {"hook": hr, "cliff": cr, "standalone": [r for x in sts for r in x[1]],
               "premise": [r for x in prs for r in x[1]]},
              item["hook"], item["cliffhanger"], item["why"], source="llm",
              llm={"scores": {k: max(1, min(5, int(v))) for k, v in item["scores"].items()},
                   "cold_viewer_test": item.get("cold_viewer_test", ""),
                   "based_on": item.get("based_on_candidate", "new")})
    return p, "ok"


# ---------------------------------------------------------------- passes

def judge_episodes(eps: list[Episode], rule_cands: list[Promo], workers: int = 6, log=print) -> list[Promo]:
    by_ep = {e.num: e for e in eps}
    cue_index = {c.cue_id: c for e in eps for c in e.cues}
    system = GUIDE + "\n\n" + series_bible(eps)

    def one(ep: Episode) -> list[Promo]:
        cands = [p for p in rule_cands if p.kind == "single" and p.segments[0].episode == ep.num][:6]
        user = (f"{transcript(ep)}\n\nRULE-BASED CANDIDATES for this episode (may be wrong):\n{_cand_block(cands)}\n\n"
                "Task: propose the best 1-3 promos that can be cut from THIS episode (single continuous stretch, or "
                "2-3 segments from this episode in order). Refine candidate boundaries where a better hook or a "
                "harder cliffhanger is a few cues away; reject weak candidates with a reason. Each promo must total "
                "20-60s and its first 3s must contain the hook. If the episode has nothing promo-worthy, return an "
                "empty list; don't force it.")
        try:
            data = _call(system, user, JUDGE_SCHEMA)
        except Exception as e:  # noqa: BLE001
            log(f"  ! ep {ep.num}: {e}")
            return []
        out = []
        for item in data.get("promos", []):
            p, why = to_promo(item, cue_index, by_ep)
            if p:
                out.append(p)
            else:
                log(f"  - ep {ep.num}: dropped LLM promo ({why})")
        return out

    results: list[Promo] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ps in ex.map(one, eps):
            results.extend(ps)
    return results


def stitch_pass(eps: list[Episode], moments: list[Promo], n: int = 8, log=print) -> list[Promo]:
    by_ep = {e.num: e for e in eps}
    cue_index = {c.cue_id: c for e in eps for c in e.cues}
    system = GUIDE + "\n\n" + series_bible(eps)
    blocks = []
    for p in moments:
        for s in p.segments:
            ep = by_ep[s.episode]
            blocks.append(transcript(ep, max(0.0, s.start - 5), min(ep.duration, s.end + 5)))
    user = ("Below are the strongest moments found in each episode (script excerpts with cue IDs).\n\n"
            + "\n\n".join(dict.fromkeys(blocks)) +
            f"\n\nTask: build up to {n} MULTI-SEGMENT promos (2-3 segments, can cross episodes, story order, total "
            "20-60s) that do something no single stretch can, e.g. compress setup→twist (humiliation → the "
            "stranger's proposal), abuse → protector arrives, accusation → reveal. Use only cue IDs shown above. "
            "Avoid joins that would visibly break continuity (wedding gown in one shot, peasant clothes the next) "
            "unless the jump IS the story. Put an empty list in rejected_candidates.")
    try:
        data = _call(system, user, JUDGE_SCHEMA)
    except Exception as e:  # noqa: BLE001
        log(f"  ! stitch pass: {e}")
        return []
    out = []
    for item in data.get("promos", []):
        p, why = to_promo(item, cue_index, by_ep)
        if p:
            out.append(p)
        else:
            log(f"  - stitch: dropped LLM promo ({why})")
    return out


def rank_pass(eps: list[Episode], promos: list[Promo], log=print) -> dict:
    system = GUIDE + "\n\n" + series_bible(eps)
    rows = []
    for i, p in enumerate(promos):
        segs = " + ".join(f"ep{s.episode} {fmt_ts(s.start)}-{fmt_ts(s.end)}" for s in p.segments)
        rows.append(f"[{i}] {segs} ({p.duration:.0f}s) | HOOK: {p.hook} | CLIFF: {p.cliffhanger} | WHY: {p.why}")
    user = ("Candidate promos for this show:\n" + "\n".join(rows) +
            "\n\nTask: decide the POSTING ORDER for a brand-new show launch. Return every id (the number in "
            "brackets, as a string) in the order you would post them, with post_priority 1 (post first) to 5 "
            "(hold / don't post) and one line of reasoning. Consider: which clip best sells the premise to a cold "
            "viewer; sending viewers to early episodes; varying hook types day to day; holding late-season "
            "payoffs that spoil the arc. Then give the overall strategy in 2-3 sentences.")
    try:
        return _call(system, user, RANK_SCHEMA)
    except Exception as e:  # noqa: BLE001
        log(f"  ! rank pass: {e}")
        return {}


def apply_rank(promos: list[Promo], data: dict) -> str:
    for row in data.get("order", []):
        try:
            p = promos[int(str(row["id"]).strip("[]P "))]
        except (ValueError, IndexError):
            continue
        p.llm["post_priority"] = row["post_priority"]
        p.post_reason = row["reason"]
    return data.get("strategy", "")
