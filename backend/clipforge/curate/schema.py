"""Curation schemas: what the LLM returns (validated locally) and the final clip record."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SENTENCE_ID = re.compile(r"^S\d{4,}$")
RiskFlag = Literal["profanity", "sensitive_topic", "unverified_claim", "sponsor_read"]


def _sid(v: str) -> str:
    if not SENTENCE_ID.match(v):
        raise ValueError(f"{v!r} is not a sentence ID like S0142")
    return v


class Candidate(BaseModel):
    start_sentence: str
    end_sentence: str
    strength: int = Field(ge=0, le=100)
    reason: str

    _ids = field_validator("start_sentence", "end_sentence")(_sid)


class ScanResult(BaseModel):
    candidates: list[Candidate]


class Scores(BaseModel):
    hook: int = Field(ge=0, le=100)
    self_contained: int = Field(ge=0, le=100)
    single_idea: int = Field(ge=0, le=100)
    payoff: int = Field(ge=0, le=100)
    emotion_novelty_utility: int = Field(ge=0, le=100)
    shareability: int = Field(ge=0, le=100)


class EmphasisRef(BaseModel):
    sentence: str
    word: str

    _id = field_validator("sentence")(_sid)


class ClipProposal(BaseModel):
    start_sentence: str
    end_sentence: str
    title: str = Field(max_length=60)
    hook: str
    hook_evidence_start: str
    hook_evidence_end: str
    summary: str
    why_it_works: str
    scores: Scores
    overall: int = Field(ge=0, le=100)
    emphasis: list[EmphasisRef] = Field(max_length=6)
    description: str
    hashtags: list[str] = Field(max_length=8)
    risk_flags: list[RiskFlag]

    _ids = field_validator(
        "start_sentence", "end_sentence", "hook_evidence_start", "hook_evidence_end"
    )(_sid)

    @field_validator("hook")
    @classmethod
    def _hook_len(cls, v: str) -> str:
        if len(v.split()) > 12:
            raise ValueError("hook must be at most 12 words")
        return v


class CurateResult(BaseModel):
    clips: list[ClipProposal]


class HookCheck(BaseModel):
    passed: bool
    coverage: float
    missing: list[str]


class Clip(BaseModel):
    """Final clip record written to clips.json (the spec's per-clip schema plus timing)."""

    id: str
    start_sentence: str
    end_sentence: str
    start: float
    end: float
    duration: float
    title: str
    hook: str
    summary: str
    why_it_works: str
    scores: Scores
    overall: int
    signal_score: float
    rank_score: float
    emphasis_word_indices: list[int]
    description: str
    hashtags: list[str]
    risk_flags: list[str]
    hook_check: HookCheck
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    transcript: str = ""
