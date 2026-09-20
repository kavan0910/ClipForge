"""Combine per-second features into an interest curve and per-sentence tags (pure, no I/O)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from clipforge.models import Sentence

DEFAULT_WEIGHTS: dict[str, float] = {
    "energy": 0.20,
    "speech_rate": 0.10,
    "density": 0.10,
    "laughter": 0.20,
    "applause": 0.10,
    "cuts": 0.05,
    "motion": 0.05,
    "heatmap": 0.25,
    "chat": 0.25,
}


def pct_rank(x: Sequence[float] | np.ndarray) -> np.ndarray:
    """Percentile rank in [0, 1]; ties share the mean rank. Constant input maps to zeros."""
    a = np.asarray(x, dtype=float)
    if a.size == 0 or np.ptp(a) == 0:
        return np.zeros(a.size)
    order = a.argsort(kind="mergesort")
    ranks = np.empty(a.size)
    ranks[order] = np.arange(a.size)
    for v in np.unique(a):  # average ranks of ties
        m = a == v
        ranks[m] = ranks[m].mean()
    return ranks / (a.size - 1)


def smooth(x: Sequence[float], window: int) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if window <= 1 or a.size == 0:
        return a
    kernel = np.ones(window) / window
    padded = np.pad(a, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def interest_curve(
    features: Mapping[str, Sequence[float]], weights: Mapping[str, float] | None = None
) -> np.ndarray:
    """Weighted mean of percentile-ranked features. Absent or all-zero features are skipped."""
    weights = weights or DEFAULT_WEIGHTS
    n = max((len(v) for v in features.values()), default=0)
    total, acc = 0.0, np.zeros(n)
    for name, values in features.items():
        w = weights.get(name, 0.0)
        a = np.asarray(values, dtype=float)
        if w <= 0 or a.size != n or not np.any(a):
            continue
        acc += w * pct_rank(a)
        total += w
    return acc / total if total else acc


def span_score(interest: Sequence[float], start: float, end: float) -> float:
    """Mean interest over [start, end) seconds (0 if the span is outside the curve)."""
    a = np.asarray(interest, dtype=float)
    lo, hi = max(int(start), 0), min(int(np.ceil(end)), a.size)
    return float(a[lo:hi].mean()) if hi > lo else 0.0


def sentence_tags(
    s: Sentence,
    energy_db: Sequence[float],
    laughter: Sequence[float] | None = None,
    applause: Sequence[float] | None = None,
    heatmap: Sequence[float] | None = None,
    chat: Sequence[float] | None = None,
    energy_cuts: tuple[float, float] | None = None,
) -> dict[str, object]:
    """Objective tags for one sentence, shown to the LLM as {energy:high, laughter, ...}."""
    lo, hi = int(s.start), max(int(np.ceil(s.end)), int(s.start) + 1)
    tags: dict[str, object] = {}
    e = np.asarray(energy_db[lo:hi], dtype=float)
    if e.size and energy_cuts:
        m = float(e.mean())
        if m >= energy_cuts[1]:
            tags["energy"] = "high"
        elif m <= energy_cuts[0]:
            tags["energy"] = "low"
    if laughter is not None and np.asarray(laughter[lo:hi]).max(initial=0) >= 0.3:
        tags["laughter"] = True
    if applause is not None and np.asarray(applause[lo:hi]).max(initial=0) >= 0.3:
        tags["applause"] = True
    if chat is not None and np.asarray(chat[lo:hi]).max(initial=0) >= 0.9:
        tags["chat_spike"] = True
    if heatmap is not None:
        h = float(np.asarray(heatmap[lo:hi]).max(initial=0))
        if h >= 0.6:
            tags["heatmap"] = round(h, 2)
    return tags


def format_tags(tags: Mapping[str, object]) -> str:
    if not tags:
        return ""
    parts = [k if v is True else f"{k}:{v}" for k, v in tags.items()]
    return "{" + ", ".join(parts) + "}"
