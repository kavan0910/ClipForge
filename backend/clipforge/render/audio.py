"""Audio for a clip: EDL cuts with click-free joins and two-pass EBU R128 loudness normalisation."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from clipforge.edl import EDL
from clipforge.errors import MediaError
from clipforge.procs import CancelToken, run_streaming

TARGET_LUFS = -14.0
TARGET_TP = -1.0  # acceptance limit on the FINAL file
SAFETY_LIMIT = 0.708  # -3 dBFS sample-peak limiter after loudnorm; a net that rarely acts
NORMALISE_TP = -2.0  # aim lower before AAC: lossy coding adds inter-sample overshoot
FADE = 0.006  # seconds: every join dips through zero for 6 ms each side, so no discontinuity


def edl_audio_filter(edl: EDL, track: int, fade: float = FADE) -> str:
    """filter_complex that trims each kept segment, fades its edges and concatenates them."""
    parts, labels = [], []
    n = len(edl.segments)
    for k, s in enumerate(edl.segments):
        chain = f"[0:{track}]atrim=start={s.src_in:.6f}:end={s.src_out:.6f},asetpts=PTS-STARTPTS"
        if k > 0:
            chain += f",afade=t=in:st=0:d={fade}"
        if k < n - 1:
            chain += f",afade=t=out:st={max(s.length - fade, 0):.6f}:d={fade}"
        parts.append(f"{chain}[a{k}]")
        labels.append(f"[a{k}]")
    parts.append("".join(labels) + f"concat=n={n}:v=0:a=1[out]")
    return ";".join(parts)


def render_pcm(
    master: Path, track: int, edl: EDL, out: Path, cancel: CancelToken | None = None
) -> None:
    """Assembled clip audio as 48 kHz float PCM (a lossless working file)."""
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(master), "-filter_complex",
            edl_audio_filter(edl, track), "-map", "[out]", "-ar", "48000", "-c:a", "pcm_f32le", str(out)]  # fmt: skip
    code, tail = run_streaming(argv, cancel=cancel)
    if code != 0:
        raise MediaError("Could not assemble the clip audio.", tail[-300:])


_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def measure(wav: Path, cancel: CancelToken | None = None) -> dict[str, float]:
    """Pass 1: measure integrated loudness, true peak, LRA and threshold."""
    af = f"loudnorm=I={TARGET_LUFS}:TP={NORMALISE_TP}:LRA=11:print_format=json"
    code, tail = run_streaming(
        ["ffmpeg", "-nostdin", "-v", "info", "-i", str(wav), "-af", af, "-f", "null", "-"],
        cancel=cancel,
    )
    m = _JSON.search(tail) or _JSON.search(_full_stderr(wav, af))
    if code != 0 or not m:
        raise MediaError("Loudness measurement failed.", tail[-300:])
    return {
        k: float(v)
        for k, v in json.loads(m.group(0)).items()
        if k.startswith(("input_", "target_"))
    }


def _full_stderr(wav: Path, af: str) -> str:
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-i", str(wav), "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True, check=False)  # fmt: skip
    return p.stderr


def normalise_filter(m: dict[str, float]) -> str:
    """Pass 2: linear loudnorm from the measurement (falls back to dynamic if needed)."""
    return (
        f"loudnorm=I={TARGET_LUFS}:TP={NORMALISE_TP}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
        f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
        ":linear=true:print_format=summary"
        f",alimiter=limit={SAFETY_LIMIT}:attack=5:release=50:level=disabled"
    )


def loudness_report(path: Path) -> dict[str, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a finished file (EBU R128)."""
    p = subprocess.run(["ffmpeg", "-nostdin", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, check=False)  # fmt: skip
    text = p.stderr
    summary = text[text.rfind("Summary:") :]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    if not i or not tp:
        raise MediaError("Could not measure loudness.", text[-200:])
    return {"lufs": float(i.group(1)), "true_peak_dbtp": float(tp.group(1))}


def normalise(
    pcm: Path, out: Path, cancel: CancelToken | None = None, tolerance: float = 0.3
) -> dict[str, float]:
    """Two-pass loudnorm, then a closed loop on the result: re-measure and trim the gain until the
    integrated loudness is within `tolerance` LU of the target (the safety limiter can undershoot
    peaky speech). Returns the final measurement."""
    m = measure(pcm, cancel)
    code, tail = run_streaming(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(pcm), "-af", normalise_filter(m),
                                "-ar", "48000", "-c:a", "pcm_f32le", str(out)], cancel=cancel)  # fmt: skip
    if code != 0:
        raise MediaError("Loudness normalisation failed.", tail[-300:])
    rep = loudness_report(out)
    for _ in range(3):
        err = TARGET_LUFS - rep["lufs"]
        if abs(err) <= tolerance:
            break
        tmp = out.with_suffix(".trim.wav")
        af = f"volume={err:.3f}dB,alimiter=limit={SAFETY_LIMIT}:attack=5:release=50:level=disabled"
        code, tail = run_streaming(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(out), "-af", af,
                                    "-c:a", "pcm_f32le", str(tmp)], cancel=cancel)  # fmt: skip
        if code != 0:
            raise MediaError("Loudness trim failed.", tail[-300:])
        tmp.replace(out)
        rep = loudness_report(out)
    return rep
