"""Raw ASR output types shared by backends, guards and the merge step."""

from __future__ import annotations

from pydantic import BaseModel


class RawWord(BaseModel):
    w: str
    start: float  # seconds, absolute in the source audio
    end: float
    prob: float = 1.0
    no_speech: float = 0.0  # segment-level no-speech probability
    logprob: float = 0.0  # segment-level average log-probability
    compression: float = 0.0  # segment-level compression ratio


class RawChunk(BaseModel):
    index: int
    start: float  # chunk start in the source audio (seconds)
    end: float
    language: str
    words: list[RawWord]
