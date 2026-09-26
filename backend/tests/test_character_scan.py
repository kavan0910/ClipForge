"""Character-edit mode: shot merging/selection (pure logic), the vision scan's batching, and
assembling the final Clip record. No real ffmpeg or network calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clipforge.character import scan as cscan
from clipforge.character.edit import build_clip, slug
from clipforge.character.schema import Scene
from clipforge.llm.client import LLMClient
from clipforge.llm.meter import CostMeter


def test_shots_from_cuts_merges_a_shot_shorter_than_the_minimum():
    # 1.1 is 0.1s after 1.0: shorter than MIN_SHOT (0.25s), so it merges back into the shot before it.
    shots = cscan.shots_from_cuts([1.0, 1.1, 5.0], 10.0)
    assert shots == [(0.0, 1.1), (1.1, 5.0), (5.0, 10.0)]


def test_shots_from_cuts_handles_no_cuts():
    assert cscan.shots_from_cuts([], 10.0) == [(0.0, 10.0)]


def test_merge_adjacent_combines_close_scenes_keeping_the_higher_confidence():
    scenes = [Scene(start=0, end=2, confidence=0.6), Scene(start=2.3, end=4, confidence=0.9)]
    merged = cscan.merge_adjacent(scenes, gap=0.5)
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end, merged[0].confidence) == (0, 4, 0.9)


def test_merge_adjacent_keeps_far_apart_scenes_separate():
    scenes = [Scene(start=0, end=2, confidence=0.6), Scene(start=10, end=12, confidence=0.9)]
    assert len(cscan.merge_adjacent(scenes, gap=0.5)) == 2


def test_select_scenes_keeps_the_most_confident_up_to_target_then_reorders_chronologically():
    scenes = [
        Scene(start=20, end=25, confidence=0.95),  # 5s, most confident, appears later
        Scene(start=0, end=5, confidence=0.90),  # 5s, second most confident, appears first
        Scene(start=40, end=45, confidence=0.50),  # 5s, least confident: should be dropped
    ]
    picked = cscan.select_scenes(scenes, target_duration=8.0)
    assert [s.start for s in picked] == [0, 20]  # story order, not confidence order


def test_select_scenes_drops_too_short_and_trims_too_long():
    scenes = [Scene(start=0, end=0.2, confidence=0.9), Scene(start=1, end=20, confidence=0.9)]
    picked = cscan.select_scenes(scenes, target_duration=100, min_scene=0.6, max_scene=6.0)
    assert len(picked) == 1
    assert picked[0].start == 1 and picked[0].end == 7


def test_build_clip_assembles_segments_and_reuses_the_existing_clip_schema():
    scenes = [Scene(start=0, end=3, confidence=0.8), Scene(start=10, end=13, confidence=0.6)]
    clip = build_clip("c001", "Gojo Satoru", scenes)
    assert clip.segments == [(0.0, 3.0), (10.0, 13.0)]
    assert clip.duration == 6.0
    assert clip.start == 0.0 and clip.end == 13.0
    assert clip.rank_score == pytest.approx(0.7)
    assert clip.hashtags[0] == slug("Gojo Satoru") == "gojosatoru"
    assert clip.status == "proposed"


def test_build_clip_requires_at_least_one_scene():
    with pytest.raises(ValueError):
        build_clip("c001", "X", [])


def _fake_vision_client(mark_present: set[int]):
    def create(**req):
        n = sum(
            1
            for m in req["messages"]
            for b in m["content"]
            if b.get("type") == "text" and b["text"].startswith("Frame ")
        )
        payload = {
            "matches": [
                {
                    "index": i,
                    "present": i in mark_present,
                    "confidence": 90 if i in mark_present else 10,
                }
                for i in range(n)
            ]
        }
        content = [SimpleNamespace(type="text", text=json.dumps(payload))]
        usage = SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return SimpleNamespace(
            content=content, usage=usage, stop_reason="end_turn", model_dump=lambda: {}
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_scan_character_batches_shots_and_returns_only_matched_scenes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cscan, "grab_thumb", lambda proxy, ts, out, width=cscan.THUMB_WIDTH: b"fake-jpeg-bytes"
    )
    fake = _fake_vision_client(mark_present={0})
    llm = LLMClient("k", CostMeter(1.0), tmp_path / "run", "v1", client=fake)
    cuts = [2.0, 4.0, 6.0, 8.0]  # 5 shots of 2s each across a 10s video
    scenes = cscan.scan_character(
        tmp_path / "proxy.mp4",
        10.0,
        cuts,
        "Gojo",
        tmp_path / "thumbs",
        llm,
        "claude-haiku-4-5-20251001",
    )
    assert len(scenes) == 1
    assert (scenes[0].start, scenes[0].end) == (0.0, 2.0)  # only the shot marked present


def test_scan_character_splits_into_multiple_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cscan, "grab_thumb", lambda proxy, ts, out, width=cscan.THUMB_WIDTH: b"fake-jpeg-bytes"
    )
    calls = []
    fake = _fake_vision_client(mark_present=set())
    real_create = fake.messages.create
    fake.messages.create = lambda **req: (calls.append(req), real_create(**req))[1]
    llm = LLMClient("k", CostMeter(1.0), tmp_path / "run", "v1", client=fake)
    cuts = [float(i) for i in range(1, 50)]  # 50 shots: more than one BATCH_SIZE (20)
    cscan.scan_character(
        tmp_path / "proxy.mp4",
        50.0,
        cuts,
        "Gojo",
        tmp_path / "thumbs",
        llm,
        "claude-haiku-4-5-20251001",
    )
    assert len(calls) == 3  # 50 shots / 20 per batch


def test_scan_character_unreadable_frame_is_treated_as_not_present(tmp_path, monkeypatch):
    monkeypatch.setattr(cscan, "grab_thumb", lambda proxy, ts, out, width=cscan.THUMB_WIDTH: b"")
    fake = _fake_vision_client(mark_present={0})  # would match if the frame had been readable
    llm = LLMClient("k", CostMeter(1.0), tmp_path / "run", "v1", client=fake)
    scenes = cscan.scan_character(
        tmp_path / "proxy.mp4",
        4.0,
        [2.0],
        "Gojo",
        tmp_path / "thumbs",
        llm,
        "claude-haiku-4-5-20251001",
    )
    assert scenes == []
