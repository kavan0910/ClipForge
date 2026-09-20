"""Speech cleanup: filler words, repeats, false starts and long pauses -> removals for an EDL."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from clipforge.curate.postprocess import snap_end, snap_start
from clipforge.edl import EDL, RemovalKind, Removed
from clipforge.models import Word

Level = Literal["off", "light", "aggressive"]
FILLER = re.compile(r"^(u+m+|u+h+|e+r+m*|a+h+m*|h+m+|m+h*m+|uh+m+)$")
# Repeats are only collapsed for function words: "no no no" or "very very" can be emphasis.
FUNCTION_WORDS = {"the", "a", "an", "i", "and", "to", "of", "that", "it", "is", "we", "you", "so",
                  "but", "in", "my", "our", "they", "this", "for", "with"}  # fmt: skip
PAUSE_THRESHOLD = {"light": 0.8, "aggressive": 0.5}
PAUSE_TARGET = 0.30  # compressed pauses keep about this much silence
MIN_KEPT = 0.30  # never leave a kept segment shorter than this


def norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


@dataclass
class WordRemoval:
    lo: int  # index into the clip's word list (inclusive)
    hi: int
    kind: RemovalKind


def find_word_removals(words: Sequence[Word], level: Level) -> list[WordRemoval]:
    """Word ranges to remove. Conservative: never touches words that carry meaning."""
    if level == "off":
        return []
    found: list[WordRemoval] = []
    n = len(words)
    for k, w in enumerate(words):
        t = norm(w.w)
        if FILLER.match(t):
            found.append(WordRemoval(k, k, "filler"))
    if level == "aggressive":
        for k in range(n - 1):
            a, b = norm(words[k].w), norm(words[k + 1].w)
            # "you know" as a verbal tic: bounded by a comma or a pause on at least one side.
            if a == "you" and b == "know":
                before = words[k].w.endswith(",") or (
                    k > 0 and words[k].start - words[k - 1].end > 0.2
                )
                after = words[k + 1].w.endswith(",") or (
                    k + 2 < n and words[k + 2].start - words[k + 1].end > 0.2
                )
                if (before or after) and (
                    k == 0 or norm(words[k - 1].w) not in {"as", "if", "did", "do"}
                ):
                    found.append(WordRemoval(k, k + 1, "filler"))
            if a == b and a in FUNCTION_WORDS and words[k + 1].start - words[k].end < 0.6:
                found.append(WordRemoval(k, k, "repeat"))
            if words[k].w.endswith("-") and len(a) <= 3:
                found.append(WordRemoval(k, k, "false_start"))
    return _merge(found)


def _merge(items: list[WordRemoval]) -> list[WordRemoval]:
    items = sorted(items, key=lambda r: (r.lo, r.hi))
    out: list[WordRemoval] = []
    for r in items:
        if out and r.lo <= out[-1].hi + 1:
            out[-1] = WordRemoval(out[-1].lo, max(out[-1].hi, r.hi), out[-1].kind)
        else:
            out.append(r)
    return out


def plan_cleanup(
    clip_id: str, words: Sequence[Word], clip_start: float, clip_end: float, level: Level,
    rms_db: Sequence[float], duration: float, hop: float = 0.1,
) -> EDL:  # fmt: skip
    """Build an EDL for [clip_start, clip_end] with removals for the chosen cleanup level.

    `words` are all transcript words (global indices); only those inside the clip are edited.
    Cut points are snapped into the pauses around removed words, so no cut lands inside a word.
    """
    edl = EDL.from_ranges(clip_id, [(clip_start, clip_end)])
    if level == "off":
        return edl
    idx = [
        k for k, w in enumerate(words) if w.start >= clip_start - 1e-6 and w.end <= clip_end + 1e-6
    ]
    if not idx:
        return edl
    first, last = idx[0], idx[-1]
    inside = words[first : last + 1]
    removals: list[Removed] = []
    counter = 0

    def add(kind: RemovalKind, a: float, b: float, text: str) -> None:
        nonlocal counter
        if b - a <= 0.02:
            return
        counter += 1
        removals.append(
            Removed(
                id=f"r{counter:03d}", kind=kind, src_in=round(a, 3), src_out=round(b, 3), text=text
            )
        )  # type: ignore[arg-type]

    for r in find_word_removals(inside, level):
        gi, gj = first + r.lo, first + r.hi
        if gi == first or gj == last:
            continue  # never trim the clip's first or last word (already snapped by curation)
        a = snap_end(words, gi - 1, rms_db, duration, hop, postroll=0.20)
        b = snap_start(words, gj + 1, rms_db, hop, preroll=0.12)
        if b - a < 0.05:
            continue
        add(r.kind, a, b, " ".join(w.w for w in words[gi : gj + 1]))
    thr = PAUSE_THRESHOLD[level]
    for k in range(first, last):
        gap = words[k + 1].start - words[k].end
        if gap > thr:
            keep_a = words[k].end + PAUSE_TARGET / 2
            keep_b = words[k + 1].start - PAUSE_TARGET / 2
            add("pause", keep_a, keep_b, f"{gap:.1f}s pause")
    # Apply, skipping any removal that overlaps an earlier one or would leave a sliver.
    for r in sorted(removals, key=lambda r: r.src_in):
        trial = edl.with_removal(r)
        if any(s.length < MIN_KEPT for s in trial.segments):
            continue
        edl = trial
    edl.validate_invariants()
    return edl


def jump_cut_zoom(edl: EDL, scale: float = 1.08) -> list[float]:
    """Alternating punch-in per segment (1.0, scale, 1.0, ...) to mask jump cuts."""
    return [1.0 if k % 2 == 0 else scale for k in range(len(edl.segments))]
