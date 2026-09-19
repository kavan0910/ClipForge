"""Speaker assignment: map diarization turns onto words by maximum time overlap."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clipforge.models import Word


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


def assign_speakers(words: Sequence[Word], turns: Sequence[Turn]) -> list[Word]:
    """Give each word the speaker whose turn overlaps it most; nearest turn if none overlaps."""
    if not turns:
        return list(words)
    ordered = sorted(turns, key=lambda t: t.start)
    out = []
    for w in words:
        best, best_overlap = None, 0.0
        for t in ordered:
            if t.start > w.end:
                break
            overlap = min(w.end, t.end) - max(w.start, t.start)
            if overlap > best_overlap:
                best, best_overlap = t, overlap
        if best is None:
            mid = (w.start + w.end) / 2
            best = min(ordered, key=lambda t: min(abs(t.start - mid), abs(t.end - mid)))
        out.append(w.model_copy(update={"speaker": best.speaker}))
    return out


def label_speakers(turns: Sequence[Turn]) -> list[Turn]:
    """Rename raw diarization labels to 'Speaker 1..N' in order of first appearance."""
    names: dict[str, str] = {}
    out = []
    for t in sorted(turns, key=lambda t: t.start):
        names.setdefault(t.speaker, f"Speaker {len(names) + 1}")
        out.append(Turn(t.start, t.end, names[t.speaker]))
    return out
