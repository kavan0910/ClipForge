"""Curation eval metrics (pure functions)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clipforge.models import Sentence, Word


@dataclass(frozen=True)
class Span:
    start: float
    end: float


def overlap_ratio(a: Span, b: Span) -> float:
    """Intersection over the shorter span (a label and a clip match when they cover each other)."""
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    shorter = min(a.end - a.start, b.end - b.start)
    return inter / shorter if shorter > 0 else 0.0


def iou(a: Span, b: Span) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    union = (a.end - a.start) + (b.end - b.start) - inter
    return inter / union if union > 0 else 0.0


def recall_at_k(
    pred: Sequence[Span], gold: Sequence[Span], k: int, threshold: float = 0.5
) -> float:
    """Share of gold moments matched (IoU >= threshold) by any of the top-k predictions."""
    if not gold:
        return float("nan")
    top = list(pred)[:k]
    hit = sum(any(iou(g, p) >= threshold for p in top) for g in gold)
    return hit / len(gold)


def precision_at_k(
    pred: Sequence[Span], gold: Sequence[Span], k: int, threshold: float = 0.5
) -> float:
    top = list(pred)[:k]
    if not top:
        return float("nan")
    return sum(any(iou(g, p) >= threshold for g in gold) for p in top) / len(top)


def bad_hit_rate(pred: Sequence[Span], bad: Sequence[Span], threshold: float = 0.5) -> float:
    """Share of predictions that mostly cover a labelled bad moment (lower is better)."""
    if not pred:
        return float("nan")
    return sum(any(overlap_ratio(p, b) >= threshold for b in bad) for p in pred) / len(pred)


def starts_on_sentence_boundary(
    clip: Span, sentences: Sequence[Sentence], words: Sequence[Word]
) -> bool:
    """The clip starts in the pause immediately before some sentence's first word."""
    for s in sentences:
        first = words[s.word_lo]
        prev_end = words[s.word_lo - 1].end if s.word_lo > 0 else 0.0
        if prev_end <= clip.start <= first.start:
            return True
    return False


def cut_inside_word(t: float, words: Sequence[Word]) -> bool:
    return any(w.start < t < w.end for w in words)


def pct(x: Sequence[bool]) -> float:
    return sum(x) / len(x) if x else float("nan")
