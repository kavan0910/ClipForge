"""Split long audio into overlapping chunks and merge their words without duplicates."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from clipforge.asr.types import RawChunk, RawWord


@dataclass(frozen=True)
class ChunkSpec:
    index: int
    start: float
    end: float


def plan_chunks(duration: float, chunk: float = 600.0, overlap: float = 15.0) -> list[ChunkSpec]:
    """Chunks of ~`chunk` seconds; consecutive chunks overlap by `overlap` seconds."""
    if duration <= chunk * 1.15:
        return [ChunkSpec(0, 0.0, duration)]
    specs: list[ChunkSpec] = []
    start, i = 0.0, 0
    while start < duration:
        end = min(start + chunk, duration)
        specs.append(ChunkSpec(i, start, end))
        if end >= duration:
            break
        start = end - overlap
        i += 1
    # Avoid a tiny trailing chunk: fold it into the previous one.
    if len(specs) > 1 and specs[-1].end - specs[-1].start < overlap * 3:
        last = specs.pop()
        specs[-1] = ChunkSpec(specs[-1].index, specs[-1].start, last.end)
    return specs


def _cut_point(prev_words: list[RawWord], lo: float, hi: float) -> float:
    """Middle of the widest inter-word gap of the earlier chunk inside the overlap [lo, hi]."""
    best_gap, best_mid = -1.0, (lo + hi) / 2
    inside = [w for w in prev_words if w.end >= lo and w.start <= hi]
    for a, b in pairwise(inside):
        gap = b.start - a.end
        if gap > best_gap and lo <= (a.end + b.start) / 2 <= hi:
            best_gap, best_mid = gap, (a.end + b.start) / 2
    return best_mid


def merge_chunks(chunks: list[RawChunk]) -> list[RawWord]:
    """Concatenate chunk words, cutting each overlap at the widest pause. No duplicates."""
    chunks = sorted(chunks, key=lambda c: c.index)
    if not chunks:
        return []
    out: list[RawWord] = []
    lower = float("-inf")
    for cur, nxt in zip(chunks, [*chunks[1:], None], strict=True):
        upper = float("inf")
        if nxt is not None and nxt.start < cur.end:
            upper = _cut_point(cur.words, nxt.start, cur.end)
        out.extend(w for w in cur.words if lower <= w.start < upper)
        lower = upper
    return out
