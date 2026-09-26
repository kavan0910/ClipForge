"""Character-scan schemas: what the vision model returns per batch of frames, and a matched scene."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShotMatch(BaseModel):
    index: int
    present: bool
    confidence: int = Field(ge=0, le=100)


class ShotBatchResult(BaseModel):
    matches: list[ShotMatch]


class Scene(BaseModel):
    """One matched, not-yet-trimmed source range that is a candidate for the edit."""

    start: float
    end: float
    confidence: float  # 0-1, the batch's reported confidence for this shot

    @property
    def duration(self) -> float:
        return self.end - self.start
