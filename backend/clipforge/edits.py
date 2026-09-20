"""Editor state for a clip: trims, text-based cuts, cleanup, layout overrides, restores, metadata edits.

The AI proposes; these edits are the human decisions. They live in clips/<id>/edits.json and are applied
by every render, so the UI preview, the EDL list and the exported file always agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from clipforge.cleanup import Level, plan_cleanup
from clipforge.curate.postprocess import snap_end, snap_start
from clipforge.curate.schema import Clip
from clipforge.edl import EDL, Removed
from clipforge.models import Word
from clipforge.reframe.plan import Layout, LayoutSegment
from clipforge.store import Project


class LayoutOverride(BaseModel):
    t0: float  # source time
    t1: float
    layout: Layout


class ClipEdits(BaseModel):
    title: str | None = None
    hook: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None
    start_word: int | None = (
        None  # inclusive global word index; the cut snaps to the pause before it
    )
    end_word: int | None = None
    exclude: list[tuple[int, int]] = Field(
        default_factory=list
    )  # word ranges cut out (text-based trimming)
    cleanup: Level = "light"
    restored: list[float] = Field(
        default_factory=list
    )  # src_in of automatic removals the editor put back
    template: str | None = None
    brand: str | None = None
    hook_enabled: bool = True
    layouts: list[LayoutOverride] = Field(default_factory=list)
    status: Literal["proposed", "approved", "rejected"] | None = None


def edits_path(project: Project, clip_id: str) -> Path:
    return project.path("clips", clip_id, "edits.json")


def load_edits(project: Project, clip_id: str) -> ClipEdits:
    f = edits_path(project, clip_id)
    return ClipEdits.model_validate_json(f.read_text()) if f.exists() else ClipEdits()


def save_edits(project: Project, clip_id: str, edits: ClipEdits) -> None:
    f = edits_path(project, clip_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(edits.model_dump_json(indent=1))
    tmp.replace(f)


def effective_clip(clip: Clip, e: ClipEdits) -> Clip:
    """The clip with the editor's text edits and status applied (timing is resolved by clip_bounds)."""
    upd: dict = {}
    for k in ("title", "hook", "description", "hashtags"):
        v = getattr(e, k)
        if v is not None:
            upd[k] = v
    if e.status:
        upd["status"] = e.status
    return clip.model_copy(update=upd)


def first_last_words(clip: Clip, words: list[Word]) -> tuple[int, int]:
    """Indices of the first and last word inside the clip's original span."""
    inside = [
        i for i, w in enumerate(words) if w.start >= clip.start - 0.25 and w.end <= clip.end + 0.25
    ]
    return (inside[0], inside[-1]) if inside else (0, len(words) - 1)


def clip_bounds(
    clip: Clip, words: list[Word], e: ClipEdits, rms: list[float], duration: float
) -> tuple[float, float]:
    """Cut times for the (possibly re-trimmed) clip, always in the pauses around whole words."""
    lo, hi = first_last_words(clip, words)
    a = min(max(e.start_word if e.start_word is not None else lo, 0), len(words) - 1)
    b = min(max(e.end_word if e.end_word is not None else hi, a), len(words) - 1)
    if e.start_word is None and e.end_word is None:
        return clip.start, clip.end  # untouched: keep the curation's already-snapped times
    start = snap_start(words, a, rms) if e.start_word is not None else clip.start
    end = snap_end(words, b, rms, duration) if e.end_word is not None else clip.end
    return start, end


def build_edl(
    clip: Clip, words: list[Word], e: ClipEdits, rms: list[float], duration: float
) -> EDL:
    """EDL = trim + automatic cleanup (minus restored items) + the editor's manual cuts."""
    start, end = clip_bounds(clip, words, e, rms, duration)
    edl = plan_cleanup(clip.id, words, start, end, e.cleanup, rms, duration)
    for rid in [r.id for r in edl.removed if any(abs(r.src_in - x) < 0.02 for x in e.restored)]:
        edl = edl.restore(rid)
    inside = [i for i, w in enumerate(words) if w.start >= start - 1e-6 and w.end <= end + 1e-6]
    if not inside:
        return edl
    first, last = inside[0], inside[-1]
    for n, (lo, hi) in enumerate(sorted(e.exclude)):
        lo, hi = max(lo, first), min(hi, last)
        if lo > hi or lo == first or hi == last:
            continue  # edges are trimmed with start/end, never by a cut
        a = snap_end(words, lo - 1, rms, duration, postroll=0.20)
        b = snap_start(words, hi + 1, rms, preroll=0.12)
        if b - a < 0.05:
            continue
        trial = edl.with_removal(
            Removed(
                id=f"m{n + 1:03d}",
                kind="manual",
                src_in=round(a, 3),
                src_out=round(b, 3),
                text=" ".join(w.w for w in words[lo : hi + 1]),
            )
        )
        if all(s.length >= 0.3 for s in trial.segments):
            edl = trial
    edl.validate_invariants()
    return edl


def layout_overrides(e: ClipEdits) -> list[LayoutSegment]:
    return [LayoutSegment(o.t0, o.t1, o.layout, override=True) for o in e.layouts]
