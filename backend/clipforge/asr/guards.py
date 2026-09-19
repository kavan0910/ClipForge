"""Hallucination guards: drop text over silence, low-confidence segments and repetition loops."""

from __future__ import annotations

import re
from collections.abc import Sequence

from clipforge.asr.types import RawWord

STOCK_HALLUCINATIONS = (
    "thanks for watching",
    "thank you for watching",
    "subtitles by",
    "subscribe to my channel",
    "please subscribe",
    "amara.org",
)


def drop_bad_segments(words: Sequence[RawWord]) -> list[RawWord]:
    """Whisper's own reliability heuristics, applied per word via its segment scores."""
    keep = []
    for w in words:
        if w.compression > 2.4:
            continue  # repetition loop
        if w.no_speech > 0.6 and w.logprob < -1.0:
            continue  # probably silence or music
        keep.append(w)
    return keep


def drop_over_silence(
    words: Sequence[RawWord], rms_db: Sequence[float], hop: float, floor_db: float = -60.0
) -> list[RawWord]:
    """Drop words whose whole span lies below `floor_db` (frame RMS in dBFS, one per `hop` s)."""
    keep = []
    for w in words:
        lo, hi = int(w.start / hop), max(int(w.end / hop), int(w.start / hop))
        frames = rms_db[lo : hi + 1]
        if frames and max(frames) < floor_db:
            continue
        keep.append(w)
    return keep


def drop_outside_speech(
    words: Sequence[RawWord], regions: Sequence[tuple[float, float]], min_overlap: float = 0.5
) -> list[RawWord]:
    """Drop words that mostly lie outside VAD speech regions (music, noise, silence)."""
    keep = []
    for w in words:
        dur = max(w.end - w.start, 1e-3)
        overlap = sum(max(0.0, min(w.end, b) - max(w.start, a)) for a, b in regions if a < w.end)
        if overlap / dur >= min_overlap:
            keep.append(w)
    return keep


def collapse_repeats(
    words: Sequence[RawWord], max_ngram: int = 6, min_repeats: int = 5, keep: int = 2
) -> list[RawWord]:
    """Collapse n-grams repeated back-to-back `min_repeats`+ times down to `keep` copies."""
    toks = [re.sub(r"\W+", "", w.w.lower()) for w in words]
    drop: set[int] = set()
    i = 0
    while i < len(words):
        collapsed = False
        for n in range(1, max_ngram + 1):
            gram = toks[i : i + n]
            if len(gram) < n or not any(gram):
                continue
            reps = 1
            while toks[i + reps * n : i + (reps + 1) * n] == gram:
                reps += 1
            if reps >= min_repeats:
                for r in range(keep, reps):
                    drop.update(range(i + r * n, i + (r + 1) * n))
                i += reps * n
                collapsed = True
                break
        if not collapsed:
            i += 1
    return [w for j, w in enumerate(words) if j not in drop]


def drop_stock_phrases(words: Sequence[RawWord], min_prob: float = 0.5) -> list[RawWord]:
    """Remove known stock hallucinations ("Thanks for watching") when they are low-confidence."""
    text = " ".join(re.sub(r"[^\w\s.]", "", w.w.lower()) for w in words)
    drop: set[int] = set()
    for phrase in STOCK_HALLUCINATIONS:
        for m in re.finditer(re.escape(phrase), text):
            first = len(text[: m.start()].split())
            last = first + len(phrase.split())
            span = range(first, min(last, len(words)))
            if span and sum(words[k].prob for k in span) / len(span) < min_prob:
                drop.update(span)
    return [w for j, w in enumerate(words) if j not in drop]


GUARD_VERSION = 2


def apply_guards(
    words: Sequence[RawWord],
    rms_db: Sequence[float] | None = None,
    hop: float = 0.1,
    speech: Sequence[tuple[float, float]] | None = None,
) -> list[RawWord]:
    out = drop_bad_segments(words)
    if speech is not None:
        out = drop_outside_speech(out, speech)
    if rms_db is not None:
        out = drop_over_silence(out, rms_db, hop)
    out = collapse_repeats(out)
    return drop_stock_phrases(out)
