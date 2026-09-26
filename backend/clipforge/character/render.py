"""Render a character edit: per-shot colour grade + a slow push-in, concatenated, vertical-framed
the same way the transcript pipeline's 'fit' layout works, then one loudness-normalisation pass.

A different, simpler path from render/clip.py's `render_clip`: there is no single continuous span,
no words, and no captions here — `clip.segments` is several disjoint source ranges to cut together,
so this builds the video directly with ffmpeg filter graphs instead of the per-frame Python
compositor the word-driven layout system needs.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from clipforge.curate.schema import Clip
from clipforge.errors import MediaError
from clipforge.models import Source
from clipforge.pipeline import Reporter
from clipforge.procs import CancelToken, run_streaming
from clipforge.reframe.solve import OUT_H, OUT_W
from clipforge.render import audio as raudio
from clipforge.store import Project

# ffmpeg `curves`/`eq` chains. Keyed by name so a style picker can select one later; "moody_blue" is
# the genre's default look (cool shadows, lifted blacks, a little desaturation).
GRADES: dict[str, str] = {
    "none": "",
    "moody_blue": "curves=r='0/0 0.5/0.42 1/0.85':b='0/0.08 0.5/0.6 1/1',eq=contrast=1.08:saturation=0.85",
    "warm": "curves=r='0/0.05 0.5/0.55 1/1':b='0/0 0.5/0.42 1/0.9',eq=contrast=1.05:saturation=1.1",
    "high_contrast": "eq=contrast=1.25:saturation=1.15:gamma=0.92",
}
DEFAULT_GRADE = "moody_blue"
PUNCH_AMOUNT = 0.12  # a 12% push-in over the shot's length


@dataclass
class CharacterRenderResult:
    path: Path
    seconds: float


def _punch_zoom_filter(dur: float, amount: float = PUNCH_AMOUNT) -> str:
    """A slow, constant push-in over the whole shot: the crop window shrinks from the full frame
    to (1-amount) of it, centred, so the picture appears to zoom toward its middle. Driven by `t`
    directly (not per-frame filter state like zoompan), which stays smooth on real video."""
    d = max(dur, 0.05)
    ratio = f"({amount}*min(t,{d:.3f})/{d:.3f})"
    return f"crop=w='iw*(1-{ratio})':h='ih*(1-{ratio})':x='(iw-out_w)/2':y='(ih-out_h)/2'"


def _segment_filter_complex(dur: float, grade: str, punch_in: bool) -> str:
    pre = "[0:v]" + (_punch_zoom_filter(dur) + "," if punch_in else "")
    graph = (
        f"{pre}split=2[bg][fg];"
        f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
        f"gblur=sigma=30,eq=brightness=-0.08[bg2];"
        f"[fg]scale={OUT_W}:-2:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2:format=auto[vfit]"
    )
    grade_filter = GRADES.get(grade, GRADES[DEFAULT_GRADE])
    graph += f";[vfit]{grade_filter}[vout]" if grade_filter else ";[vfit]null[vout]"
    return graph


def render_segment(
    master: Path, start: float, end: float, grade: str, punch_in: bool, out: Path,
    cancel: CancelToken | None = None,
) -> None:  # fmt: skip
    dur = max(end - start, 0.05)
    argv = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}", "-i", str(master), "-t", f"{dur:.6f}",
        "-filter_complex", _segment_filter_complex(dur, grade, punch_in),
        "-map", "[vout]", "-map", "0:a?", "-r", "30",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out),
    ]  # fmt: skip
    code, tail = run_streaming(argv, cancel=cancel)
    if code != 0:
        raise MediaError("Could not render one of the edit's shots.", tail[-300:])


def _concat(segments: list[Path], out: Path, cancel: CancelToken | None) -> None:
    listing = out.with_suffix(".txt")
    listing.write_text("\n".join(f"file '{p.resolve()}'" for p in segments))
    code, tail = run_streaming(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
        cancel=cancel,
    )  # fmt: skip
    if code != 0:
        raise MediaError("Could not join the edit's shots together.", tail[-300:])


def _normalise_loudness(src: Path, out: Path, cancel: CancelToken | None) -> None:
    """Two-pass EBU R128 to the same -14 LUFS target as the transcript pipeline; video stream is
    copied untouched. Falls back to a plain copy if the source has no audio to measure."""
    wav = src.with_suffix(".audio.wav")
    code, _ = run_streaming(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vn", "-ar", "48000", "-c:a", "pcm_f32le", str(wav)],
        cancel=cancel,
    )  # fmt: skip
    if code != 0 or not wav.exists() or wav.stat().st_size == 0:
        shutil.copy2(src, out)
        return
    m = raudio.measure(wav, cancel)
    af = raudio.normalise_filter(m)
    code, tail = run_streaming(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-af", af, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)],
        cancel=cancel,
    )  # fmt: skip
    if code != 0:
        raise MediaError("Could not normalise the edit's loudness.", tail[-300:])


def _cover(segment: Path, out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(segment), "-frames:v", "1", "-q:v", "3", str(out)],
        check=False, capture_output=True,
    )  # fmt: skip


def render_character_edit(
    project: Project, source: Source, clip: Clip, reporter: Reporter, cancel: CancelToken,
    grade: str = DEFAULT_GRADE, punch_in: bool = True,
) -> CharacterRenderResult:  # fmt: skip
    segments = clip.segments or []
    if not segments:
        raise MediaError("This clip has no segments to render.", "Re-run clip selection.")
    d = project.path("clips", clip.id)
    d.mkdir(parents=True, exist_ok=True)
    work = d / "segments"
    work.mkdir(exist_ok=True)
    t0 = time.time()
    master = Path(source.master_path)
    seg_paths: list[Path] = []
    for i, (s, e) in enumerate(segments):
        out = work / f"seg_{i:03d}.mp4"
        render_segment(master, s, e, grade, punch_in, out, cancel)
        seg_paths.append(out)
        reporter.progress(
            "render", 0.1 + 0.7 * (i + 1) / len(segments), note=f"shot {i + 1}/{len(segments)}"
        )
    concatenated = work / "concatenated.mp4"
    _concat(seg_paths, concatenated, cancel)
    reporter.progress("render", 0.9, note="loudness")
    out = d / "out.mp4"
    _normalise_loudness(concatenated, out, cancel)
    _cover(seg_paths[len(seg_paths) // 2], d / "thumb.jpg")
    shutil.rmtree(work, ignore_errors=True)
    reporter.progress("render", 1.0)
    return CharacterRenderResult(out, round(time.time() - t0, 1))
