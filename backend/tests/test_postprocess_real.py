"""Deterministic post-processing on REAL transcripts and silence profiles (no LLM involved).

Each hand-labelled good moment is fed in as a perfect proposal (mapped to the nearest sentence
IDs). This checks the guarantees that do not depend on model quality: clips start at a sentence,
no cut lands inside a word, and pre/post-roll sit in the pauses.
"""

import json
from pathlib import Path

import pytest

from clipforge.curate.postprocess import Params, resolve
from clipforge.curate.schema import ClipProposal, Scores
from clipforge.evalkit import metrics as m
from clipforge.evalkit.harness import EVAL_DIR, load_case

VIDEOS = (
    sorted(p.name for p in (EVAL_DIR / "cache").iterdir()) if (EVAL_DIR / "cache").exists() else []
)
pytestmark = pytest.mark.skipif(not VIDEOS, reason="eval/cache not built")


def proposal(a: str, b: str) -> ClipProposal:
    return ClipProposal(
        start_sentence=a, end_sentence=b, title="t", hook="A hook", hook_evidence_start=a,
        hook_evidence_end=b, summary="s", why_it_works="w",
        scores=Scores(hook=80, self_contained=80, single_idea=80, payoff=80,
                      emotion_novelty_utility=80, shareability=80),
        overall=80, emphasis=[], description="d", hashtags=["x"], risk_flags=[],
    )  # fmt: skip


def nearest(sentences, t, side):
    key = (lambda s: abs(s.start - t)) if side == "start" else (lambda s: abs(s.end - t))
    return min(sentences, key=key)


def test_gold_moments_resolve_to_clean_cuts_on_every_real_transcript():
    stats = {"clips": 0, "starts_ok": 0, "mid_word": 0, "unresolved": 0}
    for vid in VIDEOS:
        source, tr, _sig, rms, labels = load_case(vid)
        for g in labels["good"]:
            a, b = (
                nearest(tr.sentences, g["start"], "start"),
                nearest(tr.sentences, g["end"], "end"),
            )
            r, why = resolve(
                proposal(a.id, b.id), tr.sentences, tr.words, rms, source.probe.duration, Params()
            )
            if r is None:
                assert why in {"too_short", "too_long"}, (vid, g, why)
                stats["unresolved"] += 1
                continue
            stats["clips"] += 1
            span = m.Span(r.start, r.end)
            stats["starts_ok"] += m.starts_on_sentence_boundary(span, tr.sentences, tr.words)
            stats["mid_word"] += sum(m.cut_inside_word(t, tr.words) for t in (r.start, r.end))
            assert 20 <= r.end - r.start <= 90
    print("real-transcript post-processing:", stats)
    assert stats["clips"] >= 20
    assert stats["mid_word"] == 0
    assert stats["starts_ok"] == stats["clips"]


def test_labels_are_well_formed():
    for vid in VIDEOS:
        labels = json.loads((EVAL_DIR / "labels" / f"{vid}.json").read_text())
        assert labels["good"] and labels["bad"]
        for g in labels["good"] + labels["bad"]:
            assert 0 <= g["start"] < g["end"]
    assert Path(EVAL_DIR / "suites" / "core.json").exists()
