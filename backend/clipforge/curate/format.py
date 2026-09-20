"""Transcript -> numbered lines the LLM reads, and chunking for the scan stage."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from clipforge.llm.meter import estimate_tokens
from clipforge.models import Sentence


def fmt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m):02d}:{s:04.1f}"


def format_line(s: Sentence, tags: str = "") -> str:
    speaker = f" ({s.speaker})" if s.speaker else ""
    suffix = f" {tags}" if tags else ""
    return f"{s.id} [{fmt_time(s.start)}-{fmt_time(s.end)}]{speaker} {s.text}{suffix}"


def format_sentences(
    sentences: Sequence[Sentence], tags_for: Callable[[Sentence], str] | None = None
) -> list[str]:
    return [format_line(s, tags_for(s) if tags_for else "") for s in sentences]


@dataclass(frozen=True)
class Chunk:
    lo: int  # inclusive sentence index
    hi: int  # inclusive


def chunk_lines(
    lines: Sequence[str], target_tokens: int = 9000, overlap: float = 0.15
) -> list[Chunk]:
    """Chunks of about `target_tokens`, each overlapping the previous by `overlap` of its size."""
    tokens = [estimate_tokens(line) for line in lines]
    chunks: list[Chunk] = []
    lo = 0
    while lo < len(lines):
        acc, hi = 0, lo
        while hi < len(lines) and (acc + tokens[hi] <= target_tokens or hi == lo):
            acc += tokens[hi]
            hi += 1
        chunks.append(Chunk(lo, hi - 1))
        if hi >= len(lines):
            break
        back, kept = hi, 0
        while back > lo + 1 and kept < acc * overlap:
            back -= 1
            kept += tokens[back]
        lo = max(back, lo + 1)
    return chunks


def total_tokens(lines: Sequence[str]) -> int:
    return sum(estimate_tokens(line) for line in lines)
