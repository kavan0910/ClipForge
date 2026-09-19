import random
from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from clipforge.asr.chunking import merge_chunks, plan_chunks
from clipforge.asr.guards import (
    apply_guards,
    collapse_repeats,
    drop_bad_segments,
    drop_outside_speech,
    drop_over_silence,
    drop_stock_phrases,
)
from clipforge.asr.sentences import segment_sentences
from clipforge.asr.types import RawChunk, RawWord
from clipforge.models import Word


def rw(w, start, end, **kw):
    return RawWord(w=w, start=start, end=end, **kw)


def make_words(n, step=0.5, t0=0.0):
    return [rw(f"w{k}", t0 + k * step, t0 + k * step + 0.4) for k in range(n)]


def test_plan_chunks_short_and_long():
    assert len(plan_chunks(300)) == 1
    specs = plan_chunks(3600 * 3)
    assert specs[0].start == 0 and specs[-1].end == 3600 * 3
    for a, b in pairwise(specs):
        assert b.start < a.end  # overlap
    assert all(s.end - s.start >= 45 for s in specs)


@given(st.floats(min_value=1, max_value=20000))
def test_plan_chunks_covers_everything(duration):
    specs = plan_chunks(duration)
    assert specs[0].start == 0 and abs(specs[-1].end - duration) < 1e-6
    for a, b in pairwise(specs):
        assert b.start <= a.end


def test_merge_has_no_duplicates_and_is_ordered():
    truth = make_words(400)  # 0..200 s
    chunks = []
    for i, (lo, hi) in enumerate([(0, 110), (95, 200)]):
        ws = [w for w in truth if lo <= w.start and w.end <= hi]
        chunks.append(RawChunk(index=i, start=lo, end=hi, language="en", words=ws))
    merged = merge_chunks(chunks)
    assert [w.w for w in merged] == [w.w for w in truth]


def test_merge_survives_slightly_different_overlap_words():
    a = make_words(300)  # 0..150
    b = [w.model_copy() for w in make_words(200, t0=100)]  # 100..200, words w0.. (differ by name)
    b = [rw(f"b{k}", w.start + random.uniform(-0.02, 0.02), w.end, prob=1) for k, w in enumerate(b)]
    merged = merge_chunks(
        [
            RawChunk(index=0, start=0, end=150, language="en", words=a),
            RawChunk(index=1, start=100, end=200, language="en", words=b),
        ]
    )
    starts = [w.start for w in merged]
    assert starts == sorted(starts)
    # no region is covered by both chunks' words
    from_a = [w for w in merged if w.w.startswith("w")]
    from_b = [w for w in merged if w.w.startswith("b")]
    assert max(w.start for w in from_a) < min(w.start for w in from_b) + 1e-9


def test_guards_drop_loops_silence_and_stock_phrases():
    loop = [rw("you", k * 0.3, k * 0.3 + 0.2) for k in range(9)]
    assert len(collapse_repeats(loop)) == 2
    normal = [rw(w, i, i + 0.5) for i, w in enumerate("no no no no okay".split())]
    assert len(collapse_repeats(normal)) == 5  # natural emphasis is kept
    phrase = [rw(w, i, i + 0.5, prob=0.2) for i, w in enumerate("Thanks for watching".split())]
    assert drop_stock_phrases(phrase) == []
    real = [rw(w, i, i + 0.5, prob=0.95) for i, w in enumerate("Thanks for watching".split())]
    assert len(drop_stock_phrases(real)) == 3  # a confident real sentence stays
    quiet = [rw("hi", 1.0, 1.4)]
    assert drop_over_silence(quiet, [-90.0] * 30, 0.1) == []
    assert drop_over_silence(quiet, [-90.0] * 10 + [-20.0] * 20, 0.1) == quiet
    bad = [
        rw("x", 0, 1, no_speech=0.9, logprob=-1.5),
        rw("y", 1, 2, compression=3.0),
        rw("z", 2, 3),
    ]
    assert [w.w for w in drop_bad_segments(bad)] == ["z"]
    assert apply_guards(bad) == [bad[2]]


def words_from(text, gap=0.1, speaker=None):
    out, t = [], 0.0
    for i, tok in enumerate(text.split()):
        out.append(Word(i=i, w=tok, start=t, end=t + 0.3, prob=1, speaker=speaker))
        t += 0.3 + gap
    return out


def test_sentences_split_on_punctuation_and_keep_abbreviations():
    ws = words_from("Hello there. I met Dr. Smith today! Really? Yes")
    s = segment_sentences(ws)
    assert [x.text for x in s] == ["Hello there.", "I met Dr. Smith today!", "Really?", "Yes"]
    assert [x.id for x in s] == ["S0001", "S0002", "S0003", "S0004"]
    assert s[0].word_lo == 0 and s[1].word_lo == 2 and s[-1].word_hi == len(ws) - 1


def test_sentences_split_on_pauses_speakers_and_runons():
    ws = words_from("one two three", gap=0.1)
    ws[2] = ws[2].model_copy(update={"start": ws[1].end + 2.0, "end": ws[1].end + 2.3})
    assert len(segment_sentences(ws)) == 2
    a = words_from("alpha beta", speaker="A") + [
        w.model_copy(update={"i": w.i + 2, "start": w.start + 1, "end": w.end + 1, "speaker": "B"})
        for w in words_from("gamma delta")
    ]
    assert [s.speaker for s in segment_sentences(a)] == ["A", "B"]
    long = words_from(" ".join(f"w{k}" for k in range(120)), gap=0.4)
    assert all(s.word_hi - s.word_lo + 1 <= 46 for s in segment_sentences(long))


@given(st.lists(st.sampled_from(["a", "b.", "c?", "d", "e!"]), min_size=1, max_size=60))
def test_sentences_partition_all_words_exactly(tokens):
    ws = words_from(" ".join(tokens))
    ss = segment_sentences(ws)
    covered = [i for s in ss for i in range(s.word_lo, s.word_hi + 1)]
    assert covered == list(range(len(ws)))


def test_worker_sanitises_non_finite_scores():
    from clipforge.asr.worker import _words_from_segments

    seg = {
        "no_speech_prob": float("nan"), "avg_logprob": float("-inf"), "compression_ratio": None,
        "words": [{"word": " hi", "start": 1.0, "end": 1.2, "probability": float("nan")}],
    }  # fmt: skip
    (w,) = _words_from_segments([seg], offset=10.0)
    assert (w.w, w.start, w.end) == ("hi", 11.0, 11.2)
    assert (w.prob, w.no_speech, w.logprob, w.compression) == (1.0, 0.0, 0.0, 0.0)


def test_words_outside_vad_speech_are_dropped_music_hallucination():
    words = [rw("ghost", 5.0, 5.4, prob=0.2), rw("real", 10.0, 10.4), rw("edge", 12.9, 13.3)]
    speech = [(9.5, 11.0), (13.0, 14.0)]  # "edge" overlaps 0.3 of 0.4 s
    kept = drop_outside_speech(words, speech)
    assert [w.w for w in kept] == ["real", "edge"]
    assert drop_outside_speech(words, []) == []  # no speech at all: everything is a hallucination
