"""Word list -> numbered sentences (S0001...) from punctuation, pauses and speaker changes."""

from __future__ import annotations

from collections.abc import Sequence

from clipforge.models import Sentence, Word

TERMINATORS = (".", "?", "!", "\u2026", "\u3002", "\uff1f", "\uff01")
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "vs.", "etc.", "e.g.", "i.e."}


def _ends_sentence(word: str) -> bool:
    w = word.strip().strip("\"')]}").lower()
    if not w.endswith(TERMINATORS) or w in ABBREVIATIONS:
        return False
    return not (len(w) == 2 and w[0].isalpha() and w[1] == ".")  # initials like "J."


def segment_sentences(
    words: Sequence[Word],
    pause_break: float = 1.0,
    max_words: int = 45,
    soft_pause: float = 0.35,
) -> list[Sentence]:
    """Break on terminal punctuation, long pauses and speaker changes; split run-ons at a pause."""
    if not words:
        return []
    sentences: list[Sentence] = []
    lo = 0

    def close(hi: int) -> None:
        nonlocal lo
        chunk = words[lo : hi + 1]
        sentences.append(
            Sentence(
                id=f"S{len(sentences) + 1:04d}",
                word_lo=chunk[0].i,
                word_hi=chunk[-1].i,
                start=chunk[0].start,
                end=chunk[-1].end,
                text=" ".join(w.w for w in chunk),
                speaker=chunk[0].speaker,
            )
        )
        lo = hi + 1

    for k, w in enumerate(words):
        nxt = words[k + 1] if k + 1 < len(words) else None
        if nxt is None:
            close(k)
        elif _ends_sentence(w.w) or nxt.start - w.end >= pause_break or nxt.speaker != w.speaker:
            close(k)
        elif k + 1 - lo >= max_words and nxt.start - w.end >= soft_pause:
            close(k)
    return sentences
