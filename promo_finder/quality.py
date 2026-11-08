"""Quality checks: per-promo flags, run-level diagnostics, optional gold-set evaluation."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .candidates import Promo, Segment, iou, MIN_LEN, MAX_LEN, MAIN_CAST  # filled per show
from .parse import Episode
from . import signals as sg


def flag_promo(p: Promo, eps: dict[int, Episode]) -> list[str]:
    flags = list(p.flags)
    if not (MIN_LEN - 0.01 <= p.duration <= MAX_LEN + 0.01):
        flags.append(f"duration {p.duration:.1f}s outside 20–60s")
    first = p.segments[0]
    ep0 = eps[first.episode]
    open_cues = [c for c in ep0.cues if first.start <= c.t0 < first.start + sg.HOOK_SECONDS]
    if not any(c.kind == "DIALOGUE" for c in open_cues):
        flags.append("visual-only hook: the first 3s has no line, so it only works if the shot lands; check the cut")
    if p.scores.get("hook", 0) < 0.45:
        flags.append("weak hook (rule score < 0.45)")
    if p.scores.get("cliff", 0) < 0.35:
        flags.append("weak cliffhanger: the cut may feel like a scene ending, not a question")
    if any("resolves" in r for r in p.reasons.get("cliff", [])):
        flags.append("tension partly resolves at the cut")
    if p.scores.get("standalone", 1) < 0.7:
        flags.append("context risk: may confuse a viewer who hasn't seen the show")
    # names spoken but never on screen in the promo
    spoken, said = set(), set()
    for s in p.segments:
        ep = eps[s.episode]
        for c in ep.cues_between(s.start, s.end):
            if c.kind == "DIALOGUE":
                spoken.add(c.speaker)
                said |= {n for n in MAIN_CAST if re.search(r"\b" + n.split()[0].title() + r"\b", c.text)}
    unseen = said - spoken
    if unseen:
        flags.append("names an off-screen character: " + ", ".join(sorted(n.title() for n in unseen)))
    max_ep = max(s.episode for s in p.segments)
    if max_ep >= 39:
        flags.append(f"late-season spoiler risk (ep {max_ep}: post-reveal arc)")
    for s in p.segments:
        ep = eps[s.episode]
        cut_sfx = [c for c in ep.cues if c.kind == "SFX" and c.t0 < s.end - 0.3 and c.t1 > s.end + 1.0
                   and sg.CLIFF_SFX.search(c.text)]
        if cut_sfx:
            flags.append(f"ep {s.episode}: cut lands inside a music sting (ends {cut_sfx[0].t1 - s.end:.1f}s later); the editor may want to ride it out")
    if len(p.segments) > 1:
        gaps = [b.episode - a.episode for a, b in zip(p.segments, p.segments[1:])]
        if max(gaps) >= 5:
            flags.append(f"stitch spans {max(gaps)} episodes: check costume/injury continuity")
    if p.llm.get("scores"):
        l = p.llm["scores"]
        lnorm = (l.get("hook", 3) + l.get("cliffhanger", 3) + l.get("standalone", 3)) / 15
        rnorm = (p.scores["hook"] + p.scores["cliff"] + p.scores["standalone"]) / 3
        if abs(lnorm - rnorm) > 0.3:
            flags.append(f"rules vs LLM disagree (rules {rnorm:.2f} vs LLM {lnorm:.2f}): needs a human look")
    # de-dup while preserving order
    seen, out = set(), []
    for f in flags:
        if f not in seen:
            seen.add(f); out.append(f)
    return out


def run_diagnostics(eps: list[Episode], cands: list[Promo], chosen: list[Promo]) -> dict:
    covered = {s.episode for p in chosen for s in p.segments}
    cand_eps = {s.episode for p in cands for s in p.segments}
    best_by_ep = {}
    for p in cands:
        for s in p.segments:
            best_by_ep[s.episode] = max(best_by_ep.get(s.episode, 0), p.scores["total"])
    weak = sorted(e for e, v in best_by_ep.items() if v < 0.55)
    trig = {}
    for p in chosen:
        for r in p.reasons.get("hook", [])[:1]:
            m = re.search(r"'(.+?)'", r)
            if m:
                trig[m.group(1)] = trig.get(m.group(1), 0) + 1
    visual_only = sum(1 for p in chosen if any(f.startswith("visual-only") for f in p.flags))
    return {
        "episodes": len(eps),
        "candidates": len(cands),
        "selected": len(chosen),
        "episodes_with_candidates": len(cand_eps),
        "episodes_used_in_selection": sorted(covered),
        "episodes_never_used": sorted(set(e.num for e in eps) - covered),
        "weak_episodes": weak,
        "hook_trigger_words": dict(sorted(trig.items(), key=lambda kv: -kv[1])),
        "visual_only_hooks": visual_only,
        "stitched_selected": sum(1 for p in chosen if p.kind == "stitched"),
        "late_season_selected": sum(1 for p in chosen if max(s.episode for s in p.segments) >= 39),
    }


def _parse_ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    mm, ss = v.split(":")
    return int(mm) * 60 + float(ss)


def evaluate_gold(chosen: list[Promo], gold_path: str | Path, k: int = 10) -> dict:
    """gold.json: [{"episode": 1, "start": "00:39.00", "end": "01:14.00"}, ...]
    (one entry per editor-picked segment; multi-segment promos may repeat with a "promo" key)."""
    gold = [Segment(int(g["episode"]), _parse_ts(g["start"]), _parse_ts(g["end"]))
            for g in json.loads(Path(gold_path).read_text(encoding="utf-8"))]
    topk = chosen[:k]
    hits = [p for p in topk if any(iou(s, g) >= 0.3 for s in p.segments for g in gold)]
    best = [max((iou(s, g) for p in chosen for s in p.segments), default=0.0) for g in gold]
    return {
        "k": k,
        "precision_at_k": round(len(hits) / max(1, len(topk)), 3),
        "gold_recall_iou0.3": round(sum(b >= 0.3 for b in best) / max(1, len(gold)), 3),
        "mean_best_tiou": round(sum(best) / max(1, len(best)), 3),
        "missed_gold": [{"episode": g.episode, "start": g.start, "end": g.end}
                        for g, b in zip(gold, best) if b < 0.3],
    }
