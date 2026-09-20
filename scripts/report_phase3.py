"""Aggregate measure.json + edl.json over every rendered clip into the Phase 3 acceptance table."""

from __future__ import annotations

import json
import statistics
import sys

from clipforge.config import get_settings
from clipforge.edl import EDL
from clipforge.models import Transcript
from clipforge.reframe import camera as cam


def rows() -> list[dict]:
    out = []
    for proj in sorted(get_settings().projects_dir.iterdir()):
        tr_file = proj / "transcript" / "transcript.json"
        if not tr_file.exists():
            continue
        tr = Transcript.model_validate_json(tr_file.read_text())
        for d in sorted((proj / "clips").glob("c*")) if (proj / "clips").exists() else []:
            m_file, e_file = d / "measure.json", d / "edl.json"
            if not (m_file.exists() and e_file.exists() and (d / "out.mp4").exists()):
                continue
            m, edl = json.loads(m_file.read_text()), EDL.model_validate_json(e_file.read_text())
            cuts = [t for s in edl.segments for t in (s.src_in, s.src_out)]
            mid_word = sum(any(w.start < c < w.end for w in tr.words) for c in cuts)
            first = tr.words[
                next(i for i, w in enumerate(tr.words) if w.start >= edl.segments[0].src_in - 0.01)
            ]
            f = m["facts"]
            fr = m.get("framing") or {}
            out.append({
                "clip": f"{proj.name}/{d.name}", "layouts": ",".join(m["layout"]["layouts"]), "sec": m["clip_seconds"],
                "render_s": m["render_seconds"], "mid_word_cuts": mid_word, "click": m["clicks"]["worst_ratio"],
                "cuts": m["clicks"]["cuts"], "drift": m["av_drift_frames"], "lufs": m["loudness"]["lufs"],
                "tp": m["loudness"]["true_peak_dbtp"], "jerk_p99": m["jerk"]["p99"], "jerk_max": m["jerk"]["max"],
                "min_layout_s": m["layout"]["min_layout_seconds"], "face": fr.get("face_in_crop"),
                "eye": fr.get("eye_line_within_30_50"), "eye_med": fr.get("eye_line_median"),
                "fmt": f"{f['width']}x{f['height']} {f['video_codec']}/{f['profile']} {f['pix_fmt']} {f['color_space']}/{f['color_primaries']}/{f['color_transfer']}",
                "fps": f["fps"], "first_word": first.w,
            })  # fmt: skip
    return out


def main() -> None:
    rs = rows()
    hdr = f"{'clip':30s} {'layout':10s} {'s':>5s} {'render':>6s} {'midw':>4s} {'click':>5s} {'drift':>5s} {'LUFS':>6s} {'TP':>5s} {'jerk99':>6s} {'face':>5s} {'eye3050':>7s}"
    print(hdr)
    for r in rs:
        face = "-" if r["face"] is None else f"{r['face']:.0%}"
        eye = "-" if r["eye"] is None or r["face"] is None else f"{r['eye']:.0%}"
        print(
            f"{r['clip']:30s} {r['layouts']:10s} {r['sec']:5.1f} {r['render_s']:6.1f} {r['mid_word_cuts']:4d} {r['click']:5.2f} {r['drift']:5.2f} {r['lufs']:6.1f} {r['tp']:5.1f} {r['jerk_p99']:6.1f} {face:>5s} {eye:>7s}"
        )
    face_rs = [r for r in rs if r["face"] is not None]
    checks = {
        "clips rendered": len(rs),
        "mid-word cuts (total)": sum(r["mid_word_cuts"] for r in rs),
        "worst click ratio (limit 1.5)": max(r["click"] for r in rs),
        "worst A/V drift, frames (limit 1)": max(r["drift"] for r in rs),
        "LUFS range (target -14 +/- 1)": (min(r["lufs"] for r in rs), max(r["lufs"] for r in rs)),
        "worst true peak dBTP (limit -1)": max(r["tp"] for r in rs),
        f"jerk p99 max (limit {cam.JERK_P99_LIMIT})": max(r["jerk_p99"] for r in rs),
        "min layout dwell s (limit 1.2)": min(r["min_layout_s"] for r in rs),
        "face-in-crop, min over clips with faces (limit 98%)": min(
            (r["face"] for r in face_rs), default=None
        ),
        "eye line in 30-50%, mean": round(statistics.fmean(r["eye"] for r in face_rs), 4)
        if face_rs
        else None,
        "formats": sorted({r["fmt"] for r in rs}),
        "render seconds per clip second, mean": round(
            statistics.fmean(r["render_s"] / r["sec"] for r in rs), 2
        ),
    }
    print()
    for k, v in checks.items():
        print(f"{k:55s} {v}")


if __name__ == "__main__":
    sys.exit(main())
