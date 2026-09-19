"""Build the ASR initial prompt / glossary from platform metadata and brand vocabulary."""

from __future__ import annotations

import re
from collections.abc import Iterable

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-]{2,}")
_STOP = {"The", "This", "That", "And", "But", "For", "You", "Your", "Our", "With", "From", "Are"}


def proper_nouns(text: str, limit: int = 30) -> list[str]:
    """Capitalised words that are not sentence starters: likely names and product terms."""
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        for k, m in enumerate(_WORD.finditer(sentence)):
            token = m.group(0)
            if k > 0 and token[0].isupper() and token not in _STOP:
                found.append(token)
    seen: dict[str, None] = {}
    for t in found:
        seen.setdefault(t, None)
    return list(seen)[:limit]


def build_initial_prompt(
    title: str | None,
    channel: str | None = None,
    tags: Iterable[str] = (),
    description: str | None = None,
    brand_vocabulary: Iterable[str] = (),
    max_chars: int = 600,
) -> str:
    terms: dict[str, None] = {}
    for t in [*brand_vocabulary, channel or "", *tags, *proper_nouns(description or "")]:
        t = t.strip()
        if t:
            terms.setdefault(t, None)
    head = f"{title.strip()}. " if title and title.strip() else ""
    prompt = head + ("Glossary: " + ", ".join(terms) + "." if terms else "")
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars].rsplit(",", 1)[0].rstrip(", ") + "."
    return prompt.strip()
