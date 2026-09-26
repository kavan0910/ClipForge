"""Character-edit render engine against real ffmpeg on a tiny synthetic source: the filter graphs
(punch-zoom, vertical fit, colour grade, concat, loudness) are exactly the part a mocked test can't
catch a syntax error in."""

from __future__ import annotations

import json
import subprocess

from clipforge.character.edit import build_clip
from clipforge.character.render import DEFAULT_GRADE, render_character_edit, render_segment
from clipforge.character.schema import Scene
from clipforge.models import Probe, QualityReport, Source
from clipforge.pipeline import Reporter
from clipforge.procs import CancelToken
from clipforge.store import Project


def synthetic_video(path, seconds: float = 10.0, w: int = 1280, h: int = 720) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_type",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


def make_source(master) -> Source:
    return Source(
        id="s", kind="path", title="synthetic", content_hash="h", master_path=str(master),
        probe=Probe(duration=10.0, format_name="mp4", size=1, audio=[]), selected_audio=0,
        quality=QualityReport(resolution=None, fps=None, video_bitrate_kbps=None, audio_summary=""),
    )  # fmt: skip


def test_render_segment_produces_a_vertical_graded_clip(tmp_path):
    src = tmp_path / "src.mp4"
    synthetic_video(src, seconds=4.0)
    out = tmp_path / "seg.mp4"
    render_segment(src, 0.5, 2.5, DEFAULT_GRADE, True, out)
    assert out.exists() and out.stat().st_size > 0
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.15


def test_render_segment_with_no_grade_and_no_punch_still_produces_a_clip(tmp_path):
    src = tmp_path / "src.mp4"
    synthetic_video(src, seconds=3.0)
    out = tmp_path / "seg.mp4"
    render_segment(src, 0.0, 1.5, "none", False, out)
    assert out.exists() and out.stat().st_size > 0


def test_render_character_edit_concatenates_segments_and_normalises_loudness(tmp_path):
    src = tmp_path / "src.mp4"
    synthetic_video(src, seconds=10.0)
    project = Project.create(tmp_path / "projects", "p1")
    source = make_source(src)
    clip = build_clip(
        "c001", "Test Character",
        [Scene(start=1.0, end=3.0, confidence=0.9), Scene(start=6.0, end=8.0, confidence=0.8)],
    )  # fmt: skip
    reporter = Reporter(project, CancelToken())
    res = render_character_edit(project, source, clip, reporter, CancelToken())

    assert res.path.exists() and res.path.name == "out.mp4"
    info = probe(res.path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.3  # two 2s segments back to back
    assert (project.path("clips", "c001") / "thumb.jpg").exists()
    assert not (project.path("clips", "c001") / "segments").exists()  # working files are cleaned up
