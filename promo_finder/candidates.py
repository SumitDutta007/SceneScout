"""Candidate promo generation: legal cut points, single-stretch windows, stitched promos."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .parse import Episode, Cue, fmt_ts
from . import signals as sg

MIN_LEN, MAX_LEN = 20.0, 60.0
W_HOOK, W_CLIFF, W_STAND, W_PREM = 0.35, 0.30, 0.20, 0.15
# Main cast is derived per show from dialogue counts (configure_cast), so nothing is show-specific.
MAIN_CAST: list[str] = []
LEADS: set[str] = set()
GENERIC_ROLES = {"LORD", "LADY", "GUARD", "MAID", "PRIEST", "JUDGE", "NOBLEMAN", "NOBLEWOMAN", "SERVANT",
                 "CONSTABLE", "MATRIARCH", "INFORMANT", "VILLAGER", "OFF-SCREEN VOICE", "NARRATOR", "CROWD"}


def configure_cast(eps: list[Episode], n: int = 10) -> list[str]:
    from collections import Counter
    counts = Counter(c.speaker for e in eps for c in e.cues if c.kind == "DIALOGUE"
                     and c.speaker not in GENERIC_ROLES)
    MAIN_CAST[:] = [name for name, _ in counts.most_common(n)]
    LEADS.clear(); LEADS.update(MAIN_CAST[:2])
    return MAIN_CAST


@dataclass
class Segment:
    episode: int
    start: float
    end: float
    cue_ids: list[str] = field(default_factory=list)
    scene_heading: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_ts"], d["end_ts"] = fmt_ts(self.start), fmt_ts(self.end)
        d["duration"] = round(self.duration, 2)
        return d


@dataclass
class Promo:
    kind: str                     # single | stitched
    segments: list[Segment]
    scores: dict
    reasons: dict                 # hook/cliff/standalone/premise -> [str]
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    flags: list[str] = field(default_factory=list)
    id: str = ""
    rank: int = 0
    source: str = "rules"         # rules | llm | llm-refined
    post_reason: str = ""
    llm: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def total(self) -> float:
        return self.scores.get("total", 0.0)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "rank": self.rank, "kind": self.kind, "source": self.source,
            "duration": round(self.duration, 2),
            "segments": [s.to_dict() for s in self.segments],
            "hook": self.hook, "cliffhanger": self.cliffhanger, "why": self.why,
            "post_reason": self.post_reason,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "reasons": self.reasons, "flags": self.flags, "llm": self.llm,
        }


# ---------------------------------------------------------------- cut points

def _mid_line(ep: Episode, t: float) -> bool:
    return any(c.kind == "DIALOGUE" and c.t0 < t - 0.05 and c.t1 > t + 0.05 for c in ep.cues)


def legal_starts(ep: Episode) -> list[float]:
    pts = {0.0} | {round(c.t0, 2) for c in ep.cues if c.kind == "CAMERA"}
    return sorted(t for t in pts if not _mid_line(ep, t) and t < ep.duration - MIN_LEN + 0.01)


def legal_ends(ep: Episode) -> list[float]:
    pts = {round(c.t1, 2) for c in ep.cues if c.kind == "CAMERA"} | {round(ep.duration, 2)}
    return sorted(t for t in pts if not _mid_line(ep, t))


def make_segment(ep: Episode, t0: float, t1: float) -> Segment:
    sc = ep.scene_of(t0)
    return Segment(ep.num, round(t0, 2), round(t1, 2),
                   [c.cue_id for c in ep.cues_between(t0, t1)], sc.heading if sc else "")


# ---------------------------------------------------------------- descriptions

def describe_hook(ep: Episode, t0: float) -> str:
    cues = [c for c in ep.cues if c.t0 < t0 + sg.HOOK_SECONDS and c.t1 > t0 + 1e-6]
    line = next((c for c in cues if c.kind == "DIALOGUE" and c.t0 >= t0 - 0.2), None)
    act = next((c for c in cues if c.kind == "ACTION" and sg.SHOCK_ACTION.search(c.text)), None) \
        or next((c for c in cues if c.kind == "ACTION"), None) \
        or next((c for c in cues if c.kind == "CAMERA"), None)
    parts = []
    if act: parts.append(act.text.rstrip("."))
    if line: parts.append(f'{line.speaker.title()}: "{line.text}"')
    return " | ".join(parts)


def describe_cliff(ep: Episode, t1: float) -> str:
    lines = [c for c in ep.cues if c.kind == "DIALOGUE" and t1 - 8.0 <= c.t1 <= t1 + 0.05]
    tail = [c for c in ep.cues if c.kind in ("ACTION", "SFX", "CAMERA") and t1 - 4 < c.t1 <= t1 + 0.05
            and not re.search(r"cut to black", c.text, re.I)]
    parts = []
    if lines: parts.append(f'{lines[-1].speaker.title()}: "{lines[-1].text}"')
    vis = next((c for c in reversed(tail) if c.kind == "ACTION"), None) or (tail[-1] if tail else None)
    if vis: parts.append(vis.text.rstrip("."))
    return " | ".join(parts)


def _why(reasons: dict) -> str:
    h = reasons.get("hook", [])[:1]
    c = reasons.get("cliff", [])[:1]
    p = reasons.get("premise", [])[:1]
    bits = []
    if h: bits.append("opens on " + h[0])
    if c: bits.append("cuts on " + c[0])
    if p: bits.append(p[0])
    s = "; ".join(bits) or "balanced tension arc"
    return s[0].upper() + s[1:] + "."


# ---------------------------------------------------------------- single stretch

def score_window(ep: Episode, t0: float, t1: float) -> tuple[dict, dict]:
    h, hr = sg.hook_score(ep, t0)
    c, cr = sg.cliff_score(ep, t1)
    st, sr = sg.standalone_score(ep, t0, t1)
    pr, pp = sg.premise_score(ep, t0, t1)
    d = t1 - t0
    dur_bonus = 0.03 if 25 <= d <= 45 else 0.0
    total = W_HOOK * h + W_CLIFF * c + W_STAND * st + W_PREM * pr + dur_bonus
    return ({"hook": h, "cliff": c, "standalone": st, "premise": pr, "total": total},
            {"hook": hr, "cliff": cr, "standalone": sr, "premise": pp})


def iou(a: Segment, b: Segment) -> float:
    if a.episode != b.episode:
        return 0.0
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    union = max(a.end, b.end) - min(a.start, b.start)
    return inter / union if union > 0 else 0.0


def single_candidates(ep: Episode, per_episode: int = 3, min_hook: float = 0.3) -> list[Promo]:
    ends = legal_ends(ep)
    found: list[Promo] = []
    for t0 in legal_starts(ep):
        h, _ = sg.hook_score(ep, t0)
        if h < min_hook:
            continue
        best = None
        for t1 in ends:
            if not (t0 + MIN_LEN <= t1 <= t0 + MAX_LEN):
                continue
            scores, reasons = score_window(ep, t0, t1)
            if best is None or scores["total"] > best[0]["total"]:
                best = (scores, reasons, t1)
        if best:
            scores, reasons, t1 = best
            found.append(Promo("single", [make_segment(ep, t0, t1)], scores, reasons,
                               describe_hook(ep, t0), describe_cliff(ep, t1), _why(reasons)))
    found.sort(key=lambda p: -p.total)
    kept: list[Promo] = []
    for p in found:
        if all(iou(p.segments[0], k.segments[0]) < 0.3 for k in kept):
            kept.append(p)
        if len(kept) >= per_episode:
            break
    return kept


# ---------------------------------------------------------------- stitched

def _cast(ep: Episode, t0: float, t1: float) -> set[str]:
    """Main characters physically present: they speak, or ACTION/CAMERA puts them in frame.
    (A name merely *mentioned* in dialogue doesn't count: that is how unrelated plot threads got joined.)"""
    cues = ep.cues_between(t0, t1)
    names = {c.speaker for c in cues if c.kind == "DIALOGUE"}
    blob = " ".join(c.text for c in cues if c.kind in ("ACTION", "CAMERA")).upper()
    names |= {m for m in MAIN_CAST if m in blob}
    return {n for n in names if n in MAIN_CAST}


def _pieces(eps: list[Episode], kind: str, n: int) -> list[tuple[float, Episode, float, float]]:
    """kind='hook': short openers (4-20s) that hit hard in the first 3s.
       kind='payoff': 10-35s closers that end on a cliffhanger."""
    lo, hi = (4.0, 20.0) if kind == "hook" else (10.0, 35.0)
    out = []
    for ep in eps:
        starts, ends = legal_starts(ep) + [], legal_ends(ep)
        best_local = []
        if kind == "hook":
            for t0 in starts:
                h, _ = sg.hook_score(ep, t0)
                if h < 0.5:
                    continue
                cands = [t1 for t1 in ends if t0 + lo <= t1 <= t0 + hi]
                if not cands:
                    continue
                # end the hook piece at the most intense beat that still leaves it short
                t1 = max(cands, key=lambda t: sum(sg.intensity(c) for c in ep.cues_between(t0, t)) / (t - t0) ** 0.3)
                pr, _ = sg.premise_score(ep, t0, t1)
                best_local.append((h + 0.3 * pr, ep, t0, t1))
        else:
            all_starts = sorted(set(starts) | {0.0})
            for t1 in ends:
                c, _ = sg.cliff_score(ep, t1)
                if c < 0.4:
                    continue
                cands = [t0 for t0 in all_starts if t1 - hi <= t0 <= t1 - lo]
                if not cands:
                    continue
                t0 = max(cands, key=lambda t: sg.premise_score(ep, t, t1)[0] + sg.standalone_score(ep, t, t1)[0])
                pr, _ = sg.premise_score(ep, t0, t1)
                best_local.append((c + 0.2 * pr, ep, t0, t1))
        best_local.sort(key=lambda x: -x[0])
        kept = []
        for item in best_local:
            seg = Segment(item[1].num, item[2], item[3])
            if all(iou(seg, Segment(k[1].num, k[2], k[3])) < 0.2 for k in kept):
                kept.append(item)
            if len(kept) >= 2:
                break
        out.extend(kept)
    out.sort(key=lambda x: -x[0])
    return out[:n]


def stitched_candidates(eps: list[Episode], max_out: int = 12, max_gap_eps: int = 12) -> list[Promo]:
    by_num = {e.num: e for e in eps}
    hooks, pays = _pieces(eps, "hook", 60), _pieces(eps, "payoff", 45)
    out: list[Promo] = []
    for _, ea, a0, a1 in hooks:
        for _, eb, b0, b1 in pays:
            if (eb.num, b0) <= (ea.num, a1):
                continue  # chronological, non-overlapping
            if eb.num == ea.num and b0 - a1 < 8.0:
                continue  # would just be a continuous stretch
            if eb.num - ea.num > max_gap_eps:
                continue
            total_len = (a1 - a0) + (b1 - b0)
            if not (MIN_LEN <= total_len <= MAX_LEN):
                continue
            shared = _cast(ea, a0, a1) & _cast(eb, b0, b1)
            if not shared:
                continue
            h, hr = sg.hook_score(ea, a0)
            c, cr = sg.cliff_score(eb, b1)
            s1, sr1 = sg.standalone_score(ea, a0, a1)
            s2, sr2 = sg.standalone_score(eb, b0, b1)
            p1, pr1 = sg.premise_score(ea, a0, a1)
            p2, pr2 = sg.premise_score(eb, b0, b1)
            st = (s1 + s2) / 2 - 0.05          # every join costs a little coherence
            pr = min(1.0, p1 + p2)
            gap_pen = 0.01 * (eb.num - ea.num)
            total = W_HOOK * h + W_CLIFF * c + W_STAND * st + W_PREM * pr - gap_pen
            segs = [make_segment(ea, a0, a1), make_segment(eb, b0, b1)]
            flags = []
            newcomers = _cast(eb, b0, b1) - _cast(ea, a0, a1) - LEADS
            if newcomers:
                flags.append("payoff introduces a character the hook never set up: "
                             + ", ".join(sorted(n.title() for n in newcomers)))
            if segs[0].scene_heading.split("–")[0].strip() != segs[1].scene_heading.split("–")[0].strip():
                flags.append(f"location jump: '{segs[0].scene_heading}' → '{segs[1].scene_heading}'")
            reasons = {"hook": hr, "cliff": cr, "standalone": sr1 + sr2,
                       "premise": pr1 + pr2, "stitch": [f"shared characters: {', '.join(sorted(shared)).title()}"]}
            out.append(Promo("stitched", segs,
                             {"hook": h, "cliff": c, "standalone": st, "premise": pr, "total": total},
                             reasons, describe_hook(ea, a0), describe_cliff(eb, b1),
                             _why(reasons), flags))
    out.sort(key=lambda p: -p.total)
    kept: list[Promo] = []
    for p in out:
        if all(max(iou(s, t) for s in p.segments for t in k.segments) < 0.3 for k in kept):
            kept.append(p)
        if len(kept) >= max_out:
            break
    return kept


def all_candidates(eps: list[Episode], per_episode: int = 3) -> list[Promo]:
    configure_cast(eps)
    cands = []
    for ep in eps:
        cands += single_candidates(ep, per_episode)
    cands += stitched_candidates(eps)
    return cands
