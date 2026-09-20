from itertools import pairwise

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from clipforge.curate.format import chunk_lines, fmt_time, format_line
from clipforge.curate.postprocess import (
    Params,
    Resolved,
    dedupe,
    extend_to_sentence_boundaries,
    hook_check,
    overlap_fraction,
    rank_and_select,
    resolve,
    snap_end,
    snap_start,
)
from clipforge.curate.schema import ClipProposal, Scores
from clipforge.models import Sentence, Word

HOP = 0.1


def words_with_gaps(gaps: list[float], dur: float = 0.3) -> list[Word]:
    out, t = [], 1.0
    for i, g in enumerate(gaps):
        out.append(Word(i=i, w=f"w{i}", start=t, end=t + dur, prob=1))
        t += dur + g
    return out


def rms_for(words, total, quiet_between=True):
    frames = int(total / HOP) + 5
    rms = np.full(frames, -20.0)
    if quiet_between:
        rms[:] = -60.0
        for w in words:
            rms[int(w.start / HOP) : int(w.end / HOP) + 1] = -20.0
    return rms.tolist()


@given(st.lists(st.floats(0.0, 2.0), min_size=6, max_size=30), st.data())
@settings(max_examples=150, deadline=None)
def test_snapped_cuts_never_land_inside_a_word(gaps, data):
    words = words_with_gaps(gaps)
    total = words[-1].end + 3
    rms = rms_for(words, total)
    a = data.draw(st.integers(1, len(words) - 3))
    b = data.draw(st.integers(a, len(words) - 2))
    s, e = snap_start(words, a, rms), snap_end(words, b, rms, total)
    for w in words:
        assert not (w.start < s < w.end), (s, w)
        assert not (w.start < e < w.end), (e, w)
    assert words[a - 1].end <= s <= words[a].start
    assert words[b].end <= e <= words[b + 1].start


def test_preroll_and_postroll_ranges_when_gaps_are_wide():
    words = words_with_gaps([1.0] * 8)
    rms = rms_for(words, 30)
    s, e = snap_start(words, 3, rms), snap_end(words, 5, rms, 30)
    assert 0.10 <= words[3].start - s <= 0.35
    assert 0.25 <= e - words[5].end <= 0.45


def test_snaps_to_the_quietest_frame():
    words = words_with_gaps([1.0] * 8)
    rms = [-40.0] * 400
    for w in words:
        a, b = int(w.start / HOP), int(w.end / HOP) + 1
        rms[a:b] = [-20.0] * (b - a)
    k = int((words[3].start - 0.3) / HOP)
    rms[k] = -80.0  # a clear minimum in the pause before word 3
    assert abs(snap_start(words, 3, rms) - (k + 0.5) * HOP) < 1e-6


def sents(texts, start=0.0, step=5.0):
    out, w = [], 0
    for k, t in enumerate(texts):
        n = len(t.split())
        out.append(
            Sentence(
                id=f"S{k + 1:04d}",
                word_lo=w,
                word_hi=w + n - 1,
                start=start + k * step,
                end=start + k * step + 3,
                text=t,
            )
        )
        w += n
    return out


def test_extend_to_full_sentences():
    ss = sents(["Great start.", "and then it", "continues here.", "Next."], step=3.4)
    assert extend_to_sentence_boundaries(ss, 2, 2) == (1, 2)  # started mid-sentence
    ss2 = sents(["One.", "this one runs on", "and finishes."], step=3.4)
    assert extend_to_sentence_boundaries(ss2, 1, 1) == (1, 2)  # ended mid-sentence
    assert extend_to_sentence_boundaries(sents(["A.", "B."]), 1, 1) == (1, 1)  # clean boundaries


def proposal(overall=80, start="S0001", end="S0002", sc=80, payoff=80):
    return ClipProposal(
        start_sentence=start, end_sentence=end, title="t", hook="A bold claim", hook_evidence_start=start,
        hook_evidence_end=end, summary="s", why_it_works="w",
        scores=Scores(hook=80, self_contained=sc, single_idea=80, payoff=payoff,
                      emotion_novelty_utility=80, shareability=80),
        overall=overall, emphasis=[], description="d", hashtags=["a"], risk_flags=[],
    )  # fmt: skip


def make_transcript(n_sent=12, words_per=8):
    words, ss, t = [], [], 0.0
    for k in range(n_sent):
        lo = len(words)
        for j in range(words_per):
            words.append(
                Word(
                    i=len(words),
                    w=f"w{k}_{j}" + ("." if j == words_per - 1 else ""),
                    start=t,
                    end=t + 0.4,
                    prob=1,
                )
            )
            t += 0.5
        ss.append(
            Sentence(
                id=f"S{k + 1:04d}",
                word_lo=lo,
                word_hi=len(words) - 1,
                start=words[lo].start,
                end=words[-1].end,
                text=" ".join(w.w for w in words[lo:]),
            )
        )
        t += 0.8
    return words, ss, t


def test_resolve_enforces_duration_and_quality_rules():
    words, ss, total = make_transcript(40, 8)  # sentences ~4.8 s each
    rms = rms_for(words, total)
    p = Params()
    ok, why = resolve(proposal(start="S0001", end="S0008"), ss, words, rms, total, p)
    assert ok and not why and 20 <= ok.end - ok.start <= 90
    assert resolve(proposal(start="S0001", end="S0002"), ss, words, rms, total, p)[1] == "too_short"
    assert resolve(proposal(start="S0001", end="S0030"), ss, words, rms, total, p)[1] == "too_long"
    assert (
        resolve(proposal(start="S0001", end="S0009", sc=20), ss, words, rms, total, p)[1]
        == "needs_prior_context"
    )
    assert (
        resolve(proposal(start="S0001", end="S0009", payoff=10), ss, words, rms, total, p)[1]
        == "ends_abruptly"
    )
    assert (
        resolve(proposal(start="S0001", end="S9999"), ss, words, rms, total, p)[1]
        == "unknown_sentence_id"
    )
    assert (
        resolve(proposal(start="S0009", end="S0003"), ss, words, rms, total, p)[1]
        == "end_before_start"
    )


def test_dedupe_drops_lower_scored_overlap_only():
    mk = lambda o, a, b: Resolved(proposal(overall=o), a, b, 0, 0)  # noqa: E731
    kept = dedupe([mk(60, 0, 40), mk(90, 20, 60), mk(70, 100, 140), mk(50, 55, 95)], 0.30)
    # 60 overlaps 90 by 50%: dropped. 50 overlaps 90 by only 12.5%: kept.
    assert sorted((r.proposal.overall, r.start) for r in kept) == [(50, 55), (70, 100), (90, 20)]
    assert overlap_fraction((0, 10), (5, 30)) == 0.5


def test_rank_blend_and_diversity_spread_clips_over_the_timeline():
    items = [
        Resolved(proposal(overall=o), s, s + 40, 0, 0)
        for o, s in [(90, 0), (89, 30), (88, 60), (86, 900)]
    ]
    picked = rank_and_select(items, [0.5, 0.5, 0.5, 0.5], 2, 1200, Params())
    starts = sorted(r.start for r, _, _ in picked)
    assert starts[0] == 0 and starts[1] == 900  # the far clip beats two clustered near-equals
    assert rank_and_select([], [], 3, 100, Params()) == []
    one = rank_and_select(items[:1], [0.1], 1, 100, Params())
    assert one[0][1] == 0.5 * 0.75 + 0.5 * 0.25  # a lone clip normalises to 0.5


def test_hook_truthfulness_flags_invented_claims():
    evidence = "We grew revenue by 40 percent after moving to weekly releases."
    assert hook_check("Weekly releases grew revenue 40 percent", evidence).passed
    bad = hook_check("Revenue tripled in ninety days", evidence)
    assert not bad.passed and "tripled" in bad.missing
    assert hook_check("Wow", evidence).passed  # no checkable content words


def test_format_and_chunking():
    s = Sentence(
        id="S0142",
        word_lo=0,
        word_hi=1,
        start=751.4,
        end=758.9,
        text="Hello there.",
        speaker="Speaker 2",
    )
    assert (
        format_line(s, "{laughter}")
        == "S0142 [12:31.4-12:38.9] (Speaker 2) Hello there. {laughter}"
    )
    assert fmt_time(3725.5) == "1:02:05.5"
    lines = [f"S{k:04d} [00:00.0-00:01.0] " + "word " * 60 for k in range(400)]
    ch = chunk_lines(lines, target_tokens=2000, overlap=0.15)
    assert ch[0].lo == 0 and ch[-1].hi == 399
    for a, b in pairwise(ch):
        assert b.lo <= a.hi and b.lo > a.lo  # overlaps, always moves forward
