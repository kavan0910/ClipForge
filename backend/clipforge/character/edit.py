"""Assemble selected scenes into a Clip record, reusing the existing schema (and therefore every
downstream feature already built on it: approve/reject, auto-render, auto-package, publish)."""

from __future__ import annotations

import re

from clipforge.character.schema import Scene
from clipforge.curate.schema import Clip, HookCheck, Scores


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower()) or "character"


def build_clip(clip_id: str, character: str, scenes: list[Scene]) -> Clip:
    """`scenes` must be non-empty and in the order they should play (select_scenes already sorts
    them chronologically)."""
    if not scenes:
        raise ValueError("no scenes to assemble")
    segments = [(round(s.start, 3), round(s.end, 3)) for s in scenes]
    duration = round(sum(e - s for s, e in segments), 3)
    avg_conf = sum(s.confidence for s in scenes) / len(scenes)
    overall = round(avg_conf * 100)
    tag = slug(character)
    return Clip(
        id=clip_id, start_sentence="", end_sentence="",
        start=segments[0][0], end=segments[-1][1], duration=duration,
        title=f"{character} edit", hook=character,
        summary=f"{character} scenes pulled from the uploaded footage and cut together.",
        why_it_works=f"Assembled from {len(segments)} moment(s) where {character} was matched on screen.",
        scores=Scores(hook=overall, self_contained=overall, single_idea=overall,
                       payoff=overall, emotion_novelty_utility=overall, shareability=overall),
        overall=overall, signal_score=0.0, rank_score=round(avg_conf, 4),
        emphasis_word_indices=[], description=f"A {character} edit, cut from the uploaded footage.",
        hashtags=[tag, "edit"], risk_flags=[],
        hook_check=HookCheck(passed=True, coverage=1.0, missing=[]),
        transcript="", segments=segments,
    )  # fmt: skip
