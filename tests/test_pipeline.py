"""Run: python -m pytest tests/ -q"""
from pathlib import Path

import pytest

from promo_finder.parse import load_show, parse_episode, ts_to_sec, normalize_speaker, RE_TIME
from promo_finder.candidates import (all_candidates, legal_starts, legal_ends, single_candidates,
                                     MIN_LEN, MAX_LEN)
from promo_finder import rank, quality, llm

SHOW = Path(__file__).resolve().parents[1] / "data" / "I Married a Broken Soldier. He was a Duke"


@pytest.fixture(scope="module")
def eps():
    return load_show(SHOW)


def test_timestamp_parsing():
    m = RE_TIME.match("Start: (01:07.50) | End: (02:18.00)")
    g = m.groups()
    assert ts_to_sec(g, 0) == pytest.approx(67.5)
    assert ts_to_sec(g, 3) == pytest.approx(138.0)


def test_speaker_normalisation():
    assert normalize_speaker("LADY JULIANA") == "JULIANA"
    assert normalize_speaker("THE FORMER GROOM") == "GERALD"
    assert normalize_speaker("MYSTERIOUS BEARDED MAN") == "ALEXANDER"
    assert normalize_speaker("LORD") == "LORD"


def test_all_episodes_parse(eps):
    assert len(eps) == 50
    assert [e.num for e in eps] == list(range(1, 51))
    for e in eps:
        assert e.cues, e.num
        assert e.logline
        assert all(c.t1 >= c.t0 for c in e.cues)


def test_episode6_missing_scene_end(eps):
    e6 = eps[5]
    assert e6.num == 6
    assert all(s.t1 is not None for s in e6.scenes)
    assert e6.duration == pytest.approx(115.0, abs=0.5)


def test_cut_points_never_split_dialogue(eps):
    for e in eps:
        for t in legal_starts(e) + legal_ends(e):
            for c in e.cues:
                if c.kind == "DIALOGUE":
                    assert not (c.t0 < t - 0.05 and c.t1 > t + 0.05), (e.num, t, c.cue_id)


def test_candidates_respect_constraints(eps):
    cands = all_candidates(eps)
    assert cands
    for p in cands:
        assert MIN_LEN - 0.01 <= p.duration <= MAX_LEN + 0.01
        for a, b in zip(p.segments, p.segments[1:]):
            assert (b.episode, b.start) >= (a.episode, a.end)
        for s in p.segments:
            assert s.cue_ids


def test_known_big_moment_is_found(eps):
    # ep 3 ends on the stranger kneeling to propose: the rules must find a promo ending there
    e3 = eps[2]
    ends = [p.segments[-1].end for p in single_candidates(e3)]
    assert any(abs(t - e3.duration) < 0.5 for t in ends)


def test_select_and_order(eps):
    chosen = rank.posting_order(rank.select(all_candidates(eps), top=12))
    assert len(chosen) == 12
    assert [p.rank for p in chosen] == list(range(1, 13))
    for p in chosen:
        p.flags = quality.flag_promo(p, {e.num: e for e in eps})
        assert p.post_reason


def test_llm_output_is_validated_against_cue_ids(eps, monkeypatch):
    """The LLM path runs with a fake API: cue IDs map back to script times, bad items are dropped."""
    e1 = eps[0]
    start = next(c for c in e1.cues if c.kind == "CAMERA" and c.t0 >= 39.0)
    end = next(c for c in e1.cues if c.kind == "CAMERA" and c.t1 >= start.t0 + 30)
    good = {"based_on_candidate": "C0", "segments": [{"start_cue": start.cue_id, "end_cue": end.cue_id}],
            "hook": "h", "cliffhanger": "c", "why": "w", "cold_viewer_test": "clear",
            "scores": {"hook": 5, "cliffhanger": 4, "standalone": 5, "spoiler_risk": 1}}
    bad_id = dict(good, segments=[{"start_cue": "E01-C999", "end_cue": end.cue_id}])
    too_short = dict(good, segments=[{"start_cue": start.cue_id, "end_cue": start.cue_id}])
    monkeypatch.setattr(llm, "_call", lambda *a, **k: {"promos": [good, bad_id, too_short],
                                                       "rejected_candidates": []})
    out = llm.judge_episodes([e1], all_candidates([e1]), workers=1, log=lambda m: None)
    assert len(out) == 1
    p = out[0]
    assert p.source == "llm" and p.segments[0].start <= start.t0 + 0.01
    assert rank.final_score(p) != p.scores["total"]  # LLM scores are blended in
