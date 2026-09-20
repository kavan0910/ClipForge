"""ADR-004 / ADR-009: render path and encoder benchmark on a real 20 s clip (static crop).

A = pure ffmpeg filtergraph (crop + Lanczos scale + libx264)
B = our pipe path: decode -> Python compose (warpAffine Lanczos) -> ffmpeg encode (libx264 or VideoToolbox)
Reference = A with lossless x264 (crf 0). VMAF (libvmaf) of every candidate vs the reference.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from clipforge.edl import EDL
from clipforge.models import VideoInfo
from clipforge.reframe.camera import Rect
from clipforge.reframe.solve import FrameSpec, Solved
from clipforge.render.video import render_video

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "eval/fixtures/media/kende_internet_hall_of_fame.webm"
OUT = ROOT / "scratch/bench_render"
START, DUR, FPS = 20.0, 20.0, 23.976
RECT = (100, 0, 607.5, 1080)  # x, y, w, h in the 1080p source


def sh(argv: list[str]) -> float:
    t = time.time()
    p = subprocess.run(argv, capture_output=True, text=True, check=False)
    if p.returncode:
        print(p.stderr[-400:])
    return time.time() - t


def vmaf(dist: Path, ref: Path) -> float:
    p = subprocess.run(["ffmpeg", "-nostdin", "-i", str(dist), "-i", str(ref), "-lavfi",
                        "[0:v]setpts=PTS-STARTPTS[d];[1:v]setpts=PTS-STARTPTS[r];[d][r]libvmaf=n_threads=6", "-f", "null", "-"],
                       capture_output=True, text=True)  # fmt: skip
    line = next((ln for ln in p.stderr.splitlines() if "VMAF score" in ln), "")
    return float(line.split(":")[-1]) if line else float("nan")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    x, y, w, h = RECT
    vf = f"crop={w}:{h}:{x}:{y},scale=1080:1920:flags=lanczos,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    base = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-ss",
        str(START),
        "-t",
        str(DUR),
        "-i",
        str(SRC),
        "-an",
        "-vf",
        vf,
    ]
    ref = OUT / "ref_lossless.mp4"
    t_ref = sh([*base, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0", str(ref)])
    a = OUT / "A_filtergraph_x264.mp4"
    t_a = sh([*base, "-c:v", "libx264", "-preset", "slow", "-crf", "17", str(a)])

    silent = OUT / "silent.wav"
    sh(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            str(DUR),
            str(silent),
        ]
    )
    video = VideoInfo(codec="vp9", width=1920, height=1080, fps=FPS, variable_fps=False)
    edl = EDL.from_ranges("bench", [(START, START + DUR)])
    n = round(DUR * FPS)
    solved = Solved(
        FPS, [FrameSpec("single", [Rect(*RECT)]) for _ in range(n)], [], np.full(n, 0.5)
    )
    rows = [("A: ffmpeg filtergraph + libx264 slow crf17", t_a, a, ref)]
    ref_b = OUT / "ref_b_lossless.mp4"
    render_video(
        SRC,
        video,
        edl,
        solved,
        silent,
        ref_b,
        codec_args=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "0"],
    )
    for label, fast in (
        ("B: pipe compose + libx264 slow crf17", False),
        ("B: pipe compose + VideoToolbox q65", True),
    ):
        out = OUT / f"B_{'vt' if fast else 'x264'}.mp4"
        t = time.time()
        render_video(SRC, video, edl, solved, silent, out, fast=fast)
        rows.append((label, time.time() - t, out, ref_b))
    # compose-only cost (decode + Python compose, no encode)
    from clipforge.render.video import compose, decode_segment

    t = time.time()
    k = 0
    for fr in decode_segment(SRC, video, START, n, FPS, True):
        compose(fr, solved.frames[k])
        k += 1
    t_compose = time.time() - t

    print(f"clip: {DUR:.0f} s at {FPS} fps, {n} frames, source 1920x1080 -> 1080x1920 (M1, 8 GB)")
    print(f"reference (lossless x264 ultrafast): {t_ref:.1f} s")
    print(f"decode+compose only (no encode): {t_compose:.1f} s = {n / t_compose:.0f} fps")
    for label, secs, path, r in rows:
        print(
            f"{label:44s} {secs:6.1f} s ({DUR / secs:4.2f}x realtime)  {path.stat().st_size / 1e6:6.1f} MB  VMAF {vmaf(path, r):5.1f} (vs lossless of the same path)"
        )


if __name__ == "__main__":
    main()
