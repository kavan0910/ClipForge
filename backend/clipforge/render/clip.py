"""Render one clip: cleanup EDL, face analysis, layout, camera, audio and a single encode."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipforge import media
from clipforge.cleanup import Level, plan_cleanup
from clipforge.curate.schema import Clip
from clipforge.edl import EDL
from clipforge.errors import ClipforgeError, MediaError
from clipforge.models import Source, Transcript
from clipforge.pipeline import Reporter, rms_db_frames
from clipforge.procs import CancelToken, run_streaming
from clipforge.reframe import plan as rplan
from clipforge.reframe.camera import Rect, crop_size
from clipforge.reframe.solve import OUT_H, OUT_W, Solved, solve
from clipforge.render import audio as raudio
from clipforge.render import measure as rmeasure
from clipforge.render import video as rvideo
from clipforge.store import Project


@dataclass
class RenderResult:
    path: Path
    edl: EDL
    solved: Solved
    seconds: float
    measure: dict


def scale_solved(solved: Solved, sx: float, sy: float) -> Solved:
    """Analysis runs on the 720p proxy; renders crop the master: scale rectangles to master pixels."""
    for f in solved.frames:
        f.rects = [Rect(r.x * sx, r.y * sy, r.w * sx, r.h * sy) for r in f.rects]
    return solved


def clip_dir(project: Project, clip_id: str) -> Path:
    d = project.path("clips", clip_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_analysis(
    proxy: Path, out: Path, t0: float, t1: float, cancel: CancelToken, fps: float = 8.0
) -> dict:
    if not out.exists() or json.loads(out.read_text()).get("start") != t0:
        argv = [
            sys.executable,
            "-m",
            "clipforge.vision.analyze",
            str(proxy),
            str(out),
            str(t0),
            str(t1),
            str(fps),
        ]
        code, tail = run_streaming(argv, cancel=cancel)
        if code != 0:
            raise ClipforgeError("Face analysis failed.", tail[-300:])
    return json.loads(out.read_text())


def render_clip(
    project: Project, source: Source, transcript: Transcript, clip: Clip, level: Level = "light", fast: bool = False,
    debug: bool = False, reporter: Reporter | None = None, cancel: CancelToken | None = None, check_faces: bool = True,
    scene_cuts: list[float] | None = None, punch_in: float = 1.0,
) -> RenderResult:  # fmt: skip
    """Full Phase 3 render for one clip; writes clips/<id>/{edl,analysis,reframe,measure}.json + out.mp4."""
    t_start = time.time()
    cancel = cancel or CancelToken()
    report = reporter.progress if reporter else (lambda *a, **k: None)
    if not source.has_video or not source.probe.video or not source.proxy_path:
        raise MediaError(
            "This source has no video, so there is nothing to reframe.",
            "Audio-only sources use the audiogram layout (later phase).",
        )
    video = source.probe.video
    d = clip_dir(project, clip.id)
    master = Path(source.master_path)
    wav = Path(source.audio_path or "")
    fps = video.fps
    tonemap_ok = "zscale" in media.ffmpeg_filters()
    media.check_free_space(d, int(clip.duration * 12_000_000 / 8) + 200_000_000)

    # 1. EDL: the clip's own cut plus filler/pause cleanup
    report("render", 0.02, note="planning cuts")
    rms = rms_db_frames(wav)
    edl = plan_cleanup(
        clip.id, transcript.words, clip.start, clip.end, level, rms, source.probe.duration
    )
    (d / "edl.json").write_text(edl.model_dump_json(indent=1))

    # 2. Face analysis on the proxy over the clip range
    report("render", 0.05, note="analysing faces")
    t0, t1 = max(clip.start - 1.0, 0.0), min(clip.end + 1.0, source.probe.duration)
    analysis = run_analysis(Path(source.proxy_path), d / "analysis.json", t0, t1, cancel)

    # 3. Layout plan and camera solve
    report("render", 0.15, note="planning camera")
    scene = rplan.scene_from_analysis(analysis)
    turns = rplan.turns_from_words(transcript.words, clip.start, clip.end)
    cw, _ = crop_size(scene.width, scene.height, OUT_W / OUT_H, 1.0)
    shots = rplan.shots_from_cuts(clip.start, clip.end, scene_cuts or [])
    plan = rplan.build_plan(scene, shots, turns, cw)
    solved = solve(scene, plan, edl, fps, punch_in=punch_in)
    (d / "reframe.json").write_text(json.dumps({
        "plan": [{"t0": s.t0, "t1": s.t1, "layout": s.layout, "focus": s.focus, "cam_track": s.cam_track,
                  "tracks": s.tracks} for s in plan],
        "cut_frames": solved.cut_frames, "jerk": solved.jerk(),
        "x_frac": np.round(solved.x_frac, 5).tolist(),
    }))  # fmt: skip
    scale_solved(solved, video.width / analysis["width"], video.height / analysis["height"])

    # 4. Audio: EDL joins with fades, then two-pass loudness normalisation
    report("render", 0.2, note="mixing audio")
    pcm, norm = d / "audio_raw.wav", d / "audio_norm.wav"
    raudio.render_pcm(master, source.selected_audio, edl, pcm, cancel)
    raudio.normalise(pcm, norm, cancel)

    # 5. One video encode
    out = d / "out.mp4"
    report("render", 0.25, note="rendering video")
    rvideo.render_video(master, video, edl, solved, norm, out, fast, tonemap_ok,
                        lambda p: report("render", 0.25 + 0.7 * p), cancel)  # fmt: skip
    meas = rmeasure.measure_clip(out, solved, edl, check_faces)
    meas["render_seconds"] = round(time.time() - t_start, 1)
    meas["clip_seconds"] = round(edl.duration, 2)
    meas["encoder"] = "h264_videotoolbox" if fast else "libx264 slow crf17"
    (d / "measure.json").write_text(json.dumps(meas, indent=1))
    if debug:
        report("render", 0.97, note="debug render")
        render_debug(source, analysis, solved, edl, d / "debug.mp4", plan)
    report("render", 1.0)
    return RenderResult(out, edl, solved, time.time() - t_start, meas)


def render_debug(
    source: Source, analysis: dict, solved: Solved, edl: EDL, out: Path, plan: list
) -> None:
    """Source frames with face boxes, track ids, the crop rectangle and the layout label drawn on."""
    import subprocess

    import cv2

    video = source.probe.video
    assert video is not None
    scale = 960 / video.width
    dw, dh = 960, round(video.height * scale) // 2 * 2
    fps = solved.fps
    frames_by_t = analysis["frames"]
    times = np.array([f["t"] for f in frames_by_t])
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{dw}x{dh}",
            "-r", rvideo.fps_arg(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", str(out)]  # fmt: skip
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE)
    ax, ay = video.width / analysis["width"], video.height / analysis["height"]
    for seg in edl.segments:
        f0 = round(seg.out_in * fps)
        f1 = min(round(seg.out_out * fps), len(solved.frames))
        for i, frame in enumerate(
            rvideo.decode_segment(Path(source.master_path), video, seg.src_in, f1 - f0, fps, True)
        ):
            spec = solved.frames[f0 + i]
            img = cv2.resize(frame, (dw, dh))
            ts = seg.src_in + i / fps
            k = int(np.abs(times - ts).argmin())
            focus = spec.focus
            for fc in frames_by_t[k]["faces"]:
                x, y, w, h = (
                    int(v * s)
                    for v, s in (
                        (fc["x"] * ax, scale),
                        (fc["y"] * ay, scale),
                        (fc["w"] * ax, scale),
                        (fc["h"] * ay, scale),
                    )
                )
                col = (0, 255, 0) if fc["id"] == focus else (255, 160, 0)
                cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
                cv2.putText(
                    img,
                    f"#{fc['id']} m={fc['mouth']:.3f}",
                    (x, max(y - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    col,
                    1,
                )
            for r in spec.rects:
                if spec.layout != "fit_blur":
                    cv2.rectangle(
                        img,
                        (int(r.x * scale), int(r.y * scale)),
                        (int((r.x + r.w) * scale), int((r.y + r.h) * scale)),
                        (0, 255, 255),
                        2,
                    )
            cv2.putText(
                img,
                f"{spec.layout}{' CUT' if spec.cut else ''}  t={ts:.2f}",
                (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            assert proc.stdin is not None
            proc.stdin.write(img.tobytes())
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait()
