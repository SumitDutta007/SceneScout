"""Final selection (diversity) and posting order."""
from __future__ import annotations

import re

from .candidates import Promo, iou
from . import signals as sg

HOOK_TYPES = [
    ("violence", re.compile(r"\b(slap|strik|struck|whip|crop|chain|iron|fire|flame|blaze|sword|blade|"
                            r"knife|blood|drag|seiz|wrench|throat|explo|shatter|gallows|noose|arrest|"
                            r"thug|stomp|trampl|smash)", re.I)),
    ("humiliation", re.compile(r"\b(altar|marry|marrying|plain|nobody|beggar|disown|discarded|"
                               r"worthless|jewel|disgrace|ashamed|penniless|weeds|mock)", re.I)),
    ("reveal", re.compile(r"\b(duke|duchess|alive|truth|who are you|ring|crest|identity|name|"
                          r"son|traitor|framed|invalid|poison|arsenic)", re.I)),
    ("romance", re.compile(r"\b(kiss|embrace|husband|wife|love|heart|hold me)", re.I)),
]


def hook_type(p: Promo) -> str:
    blob = p.hook + " " + " ".join(p.reasons.get("hook", []))
    for name, rx in HOOK_TYPES:
        if rx.search(blob):
            return name
    return "confrontation"


def final_score(p: Promo) -> float:
    rules = p.scores["total"]
    llm = p.llm.get("scores") if p.llm else None
    if not llm:
        return rules
    l = (0.35 * llm.get("hook", 3) + 0.30 * llm.get("cliffhanger", 3) + 0.25 * llm.get("standalone", 3)
         + 0.10 * (6 - llm.get("spoiler_risk", 3))) / 5.0
    return 0.6 * l + 0.4 * rules


def _hook_anchor(p: Promo) -> tuple[int, float]:
    s = p.segments[0]
    return s.episode, s.start


def select(cands: list[Promo], top: int = 15, max_per_episode: int = 2,
           max_stitched_frac: float = 0.34) -> list[Promo]:
    """Greedy pick by score with diversity rules.

    Stitched promos are capped at about 1/3 of the list. A stitch picks its hook piece and
    its cliffhanger piece independently, so it structurally outscores continuous clips, but
    continuous clips are cheaper to cut and more coherent for a cold viewer.
    """
    for p in cands:
        p.scores["final"] = final_score(p)
    pool = sorted(cands, key=lambda p: -p.scores["final"])
    chosen: list[Promo] = []
    ep_count: dict[int, int] = {}
    max_stitched = max(1, round(top * max_stitched_frac))
    for p in pool:
        if p.kind == "stitched" and sum(q.kind == "stitched" for q in chosen) >= max_stitched:
            continue
        eps = {s.episode for s in p.segments}
        if any(ep_count.get(e, 0) >= max_per_episode for e in eps):
            continue
        clash = False
        for q in chosen:
            if max(iou(a, b) for a in p.segments for b in q.segments) >= 0.3:
                clash = True; break
            ea, ta = _hook_anchor(p); eb, tb = _hook_anchor(q)
            if ea == eb and abs(ta - tb) < 6.0:
                clash = True; break   # same opening moment = same promo to a viewer
        if clash:
            continue
        chosen.append(p)
        for e in eps:
            ep_count[e] = ep_count.get(e, 0) + 1
        if len(chosen) >= top:
            break
    return chosen


def posting_order(promos: list[Promo]) -> list[Promo]:
    """Order for posting: strongest *entry point* first, then spread hook types.

    Post-first value = final score
      + entry bonus   (early episodes send a new viewer straight to ep 1-5)
      + premise bonus (cold viewer understands the show from the clip alone)
      - spoiler cost  (late-season clips give away payoffs the title doesn't)
    Then greedy re-order so two consecutive posts don't share a hook type.
    """
    def post_value(p: Promo) -> float:
        max_ep = max(s.episode for s in p.segments)
        v = p.scores["final"]
        v += 0.08 if max_ep <= 5 else (0.04 if max_ep <= 14 else 0.0)
        v += 0.05 * p.scores.get("premise", 0)
        v -= 0.10 * max(0, max_ep - 30) / 20.0
        if p.llm.get("post_priority") is not None:
            v += 0.02 * (5 - p.llm["post_priority"])
        return v

    for p in promos:
        p.scores["post_value"] = post_value(p)
    rest = sorted(promos, key=lambda p: -p.scores["post_value"])
    ordered: list[Promo] = []
    last_type = None
    while rest:
        pick = next((p for p in rest[:3] if hook_type(p) != last_type), rest[0])
        rest.remove(pick)
        ordered.append(pick)
        last_type = hook_type(pick)
    for i, p in enumerate(ordered, 1):
        p.rank = i
        p.id = f"P{i:02d}"
        if not p.post_reason:
            p.post_reason = _post_reason(p, i)
    return ordered


def _post_reason(p: Promo, i: int) -> str:
    max_ep = max(s.episode for s in p.segments)
    ht = hook_type(p)
    bits = [f"{ht} hook"]
    if max_ep <= 5:
        bits.append("entry point: the viewer lands on ep 1–5 and binges forward")
    elif max_ep >= 31:
        bits.append("late-season payoff: hold for retargeting viewers who already know the premise")
    if p.scores.get("premise", 0) >= 0.5:
        bits.append("states the premise on screen")
    if p.kind == "stitched":
        bits.append("stitched: compresses the setup→twist arc into one clip")
    if p.scores.get("cliff", 0) >= 0.6:
        bits.append("hard cliffhanger ending")
    return "; ".join(bits).capitalize() + "."
