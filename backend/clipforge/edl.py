"""Edit Decision List: maps source time ranges to a contiguous output timeline.

The EDL is the single source of truth for cuts. Filler removal, pause compression and the
clip's own in/out points all produce one. Captions, camera paths and audio are remapped
through it so everything stays in sync. Invariants (property-tested):
  - kept segments are sorted and non-overlapping in source time;
  - output time is contiguous: segment k+1 starts where segment k ends;
  - output length equals the sum of kept source lengths (no speed changes in v1).
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from clipforge.models import Word

EPS = 1e-9
RemovalKind = Literal["filler", "pause", "repeat", "false_start", "manual"]


class Segment(BaseModel):
    src_in: float
    src_out: float
    out_in: float = 0.0

    @property
    def length(self) -> float:
        return self.src_out - self.src_in

    @property
    def out_out(self) -> float:
        return self.out_in + self.length


class Removed(BaseModel):
    """One removed range, kept so the editor can list and restore it."""

    id: str
    kind: RemovalKind
    src_in: float
    src_out: float
    text: str = ""
    restored: bool = False


class RemappedWord(BaseModel):
    i: int
    w: str
    src_start: float
    src_end: float
    out_start: float
    out_end: float


class EDL(BaseModel):
    clip_id: str
    segments: list[Segment]
    removed: list[Removed] = Field(default_factory=list)

    # -- construction ----------------------------------------------------
    @classmethod
    def from_ranges(
        cls, clip_id: str, keep: Sequence[tuple[float, float]], removed: Sequence[Removed] = ()
    ) -> EDL:
        """Build from kept source ranges. Ranges are sorted, clipped and overlaps merged."""
        merged: list[list[float]] = []
        for a, b in sorted((a, b) for a, b in keep if b - a > EPS):
            if merged and a <= merged[-1][1] + EPS:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        segs: list[Segment] = []
        t = 0.0
        for a, b in merged:
            segs.append(Segment(src_in=a, src_out=b, out_in=t))
            t += b - a
        return cls(clip_id=clip_id, segments=segs, removed=list(removed))

    def validate_invariants(self) -> None:
        t = 0.0
        prev_out = float("-inf")
        for s in self.segments:
            if s.src_out <= s.src_in:
                raise ValueError("empty or inverted segment")
            if s.src_in < prev_out - EPS:
                raise ValueError("segments overlap or are unsorted in source time")
            if abs(s.out_in - t) > 1e-6:
                raise ValueError("output time is not contiguous")
            t += s.length
            prev_out = s.src_out

    # -- queries ---------------------------------------------------------
    @property
    def duration(self) -> float:
        return self.segments[-1].out_out if self.segments else 0.0

    @property
    def cut_points_out(self) -> list[float]:
        """Output times where two non-adjacent source ranges are joined (audible/visible cuts)."""
        return [b.out_in for a, b in zip(self.segments, self.segments[1:], strict=False)]

    def _starts(self) -> list[float]:
        return [s.src_in for s in self.segments]

    def src_to_out(self, t: float) -> float | None:
        """Output time of a source time, or None if that instant was removed."""
        k = bisect.bisect_right(self._starts(), t + EPS) - 1
        if k < 0:
            return None
        s = self.segments[k]
        if t > s.src_out + EPS:
            return None
        return s.out_in + min(max(t - s.src_in, 0.0), s.length)

    def src_to_out_nearest(self, t: float) -> float:
        """Like src_to_out but snaps removed instants to the nearest kept boundary."""
        exact = self.src_to_out(t)
        if exact is not None:
            return exact
        best, best_d = 0.0, float("inf")
        for s in self.segments:
            for src, out in ((s.src_in, s.out_in), (s.src_out, s.out_out)):
                if abs(src - t) < best_d:
                    best, best_d = out, abs(src - t)
        return best

    def out_to_src(self, t: float) -> float:
        """Source time for an output time (clamped to the timeline)."""
        if not self.segments:
            return 0.0
        t = min(max(t, 0.0), self.duration)
        outs = [s.out_in for s in self.segments]
        k = max(bisect.bisect_right(outs, t + EPS) - 1, 0)
        s = self.segments[k]
        return s.src_in + min(max(t - s.out_in, 0.0), s.length)

    def remap_words(self, words: Sequence[Word]) -> list[RemappedWord]:
        """Words fully inside kept segments, with output times. Removed words are dropped."""
        out: list[RemappedWord] = []
        starts = self._starts()
        for w in words:
            k = bisect.bisect_right(starts, w.start + EPS) - 1
            if k < 0:
                continue
            seg = self.segments[k]
            if w.end > seg.src_out + 1e-6:
                continue  # removed, or straddles a cut (never for word-aligned cuts)
            out.append(
                RemappedWord(
                    i=w.i, w=w.w, src_start=w.start, src_end=w.end,
                    out_start=seg.out_in + (w.start - seg.src_in),
                    out_end=seg.out_in + (w.end - seg.src_in),
                )
            )  # fmt: skip
        return out

    def remap_series(self, fn: Callable[[float], float], fps: float) -> list[float]:
        """Sample a source-time function at every output frame (camera paths, zoom, etc.)."""
        n = round(self.duration * fps)
        return [fn(self.out_to_src((f + 0.5) / fps)) for f in range(n)]

    # -- editing ---------------------------------------------------------
    def with_removal(self, r: Removed) -> EDL:
        """Remove [r.src_in, r.src_out] from the kept ranges and record it."""
        keep: list[tuple[float, float]] = []
        for s in self.segments:
            if r.src_out <= s.src_in or r.src_in >= s.src_out:
                keep.append((s.src_in, s.src_out))
                continue
            if r.src_in > s.src_in:
                keep.append((s.src_in, r.src_in))
            if r.src_out < s.src_out:
                keep.append((r.src_out, s.src_out))
        return EDL.from_ranges(self.clip_id, keep, [*self.removed, r])

    def restore(self, removed_id: str) -> EDL:
        """Put a removed range back (marks it restored; adjacent segments merge)."""
        target = next((r for r in self.removed if r.id == removed_id and not r.restored), None)
        if target is None:
            return self
        keep = [(s.src_in, s.src_out) for s in self.segments] + [(target.src_in, target.src_out)]
        rem = [
            r.model_copy(update={"restored": r.id == removed_id or r.restored})
            for r in self.removed
        ]
        return EDL.from_ranges(self.clip_id, keep, rem)
