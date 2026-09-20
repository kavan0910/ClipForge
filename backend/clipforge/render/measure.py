"""Independent measurements of a finished clip (the acceptance numbers)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from clipforge.edl import EDL
from clipforge.reframe.solve import Solved
from clipforge.render.audio import loudness_report


def probe_facts(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)  # fmt: skip
    d = json.loads(p.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    num, den = (int(x) for x in v["avg_frame_rate"].split("/"))
    return {
        "video_codec": v["codec_name"], "profile": v.get("profile"), "pix_fmt": v["pix_fmt"],
        "width": v["width"], "height": v["height"], "fps": num / den,
        "color_space": v.get("color_space"), "color_primaries": v.get("color_primaries"),
        "color_transfer": v.get("color_transfer"), "video_seconds": float(v["duration"]),
        "audio_seconds": float(a["duration"]), "frames": int(v.get("nb_frames", 0) or 0),
        "bit_rate_kbps": round(int(d["format"]["bit_rate"]) / 1000),
    }  # fmt: skip


def audio_click_ratio(path: Path, cut_times: list[float]) -> dict[str, float]:
    """Waveform discontinuity at cuts: max |x[n]-x[n-1]| within 3 ms of a cut, relative to the
    99th percentile of the same statistic over the rest of the clip (<= 1.5 means no click)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout  # fmt: skip
    x = np.frombuffer(raw, dtype=np.float32)
    d = np.abs(np.diff(x))
    ref = float(np.percentile(d, 99)) or 1e-9
    worst = 0.0
    for t in cut_times:
        c = int(t * 48000)
        seg = d[max(c - 144, 0) : c + 144]
        if seg.size:
            worst = max(worst, float(seg.max()) / ref)
    return {"cuts": len(cut_times), "worst_ratio": round(worst, 3), "reference_p99": ref}


def face_framing(path: Path, solved: Solved, offset_frames: int = 0) -> dict[str, float]:
    """Face-in-crop and eye-line on the rendered frames, measured in an isolated subprocess.

    (OpenCV + onnxruntime together in the main process crash at interpreter exit.)
    """
    import sys
    import tempfile

    from clipforge.procs import run_streaming

    with tempfile.TemporaryDirectory() as tmp:
        layouts, out = Path(tmp) / "layouts.json", Path(tmp) / "out.json"
        layouts.write_text(json.dumps(["card"] * offset_frames + [f.layout for f in solved.frames]))
        code, tail = run_streaming(
            [
                sys.executable,
                "-m",
                "clipforge.render.faces_check",
                str(path),
                str(layouts),
                str(out),
            ]
        )
        if code != 0:
            return {"error": tail[-200:]}  # type: ignore[dict-item]
        return json.loads(out.read_text())


def layout_stats(solved: Solved) -> dict[str, float]:
    runs: list[tuple[str, int]] = []
    for f in solved.frames:
        if runs and runs[-1][0] == f.layout:
            runs[-1] = (f.layout, runs[-1][1] + 1)
        else:
            runs.append((f.layout, 1))
    secs = [n / solved.fps for _, n in runs]
    return {"layout_changes": len(runs) - 1, "min_layout_seconds": round(min(secs), 3) if secs else 0.0,
            "layouts": sorted({r[0] for r in runs})}  # type: ignore[dict-item]  # fmt: skip


def measure_clip(
    out: Path, solved: Solved, edl: EDL, check_faces: bool = True, offset_frames: int = 0
) -> dict:
    facts = probe_facts(out)
    frame = 1.0 / facts["fps"]
    res = {
        "facts": facts,
        "av_drift_frames": round(abs(facts["video_seconds"] - facts["audio_seconds"]) / frame, 3),
        "loudness": loudness_report(out),
        "clicks": audio_click_ratio(
            out, [c + offset_frames / facts["fps"] for c in edl.cut_points_out]
        ),
        "jerk": {k: round(v, 3) for k, v in solved.jerk().items()},
        "layout": layout_stats(solved),
        "cuts_out": [round(c, 3) for c in edl.cut_points_out],
    }
    if check_faces:
        res["framing"] = face_framing(out, solved, offset_frames)
    return res
