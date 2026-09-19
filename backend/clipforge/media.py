"""ffprobe validation, quality reports, hashing and ffmpeg normalisation jobs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from clipforge.errors import DiskSpaceError, MediaError
from clipforge.models import AudioTrack, Probe, QualityReport, VideoInfo
from clipforge.procs import CancelToken, run_streaming

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a"}
ACCEPTED_EXTS = VIDEO_EXTS | AUDIO_EXTS
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
GIB = 1024**3


def _frac(s: str | None) -> float:
    if not s or s == "0/0":
        return 0.0
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> Probe:
    """Validate a media file with ffprobe. Raises MediaError with actionable text."""
    if not path.is_file():
        raise MediaError(f"File not found: {path.name}", "Check the path and try again.")
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0 or not p.stdout.strip():
        raise MediaError(
            "This file is not readable as video or audio (it may be corrupt or not media).",
            "Try re-exporting or re-downloading the file.",
        )
    data = json.loads(p.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    audio = [
        AudioTrack(
            index=s["index"],
            codec=s.get("codec_name", "unknown"),
            channels=int(s.get("channels", 0)),
            sample_rate=int(s.get("sample_rate", 0) or 0),
            language=s.get("tags", {}).get("language"),
            title=s.get("tags", {}).get("title"),
            default=bool(s.get("disposition", {}).get("default")),
        )
        for s in streams
        if s.get("codec_type") == "audio"
    ]
    if not audio:
        raise MediaError(
            "This file has no audio stream, so there is nothing to transcribe.",
            "Use a file with speech, or add an audio track first.",
        )
    vs = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    duration = float(fmt.get("duration") or 0)
    if duration <= 0:
        raise MediaError("Could not determine the duration.", "The file may be truncated.")
    video = _video_info(vs) if vs else None
    return Probe(
        duration=duration,
        format_name=fmt.get("format_name", ""),
        size=int(fmt.get("size", 0)),
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        video=video,
        audio=audio,
    )


def _video_info(s: dict) -> VideoInfo:
    rotation = 0
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = round(float(sd["rotation"]))
    rotation = int(s.get("tags", {}).get("rotate", rotation) or rotation)
    w, h = int(s["width"]), int(s["height"])
    if abs(rotation) % 180 == 90:
        w, h = h, w
    avg, real = _frac(s.get("avg_frame_rate")), _frac(s.get("r_frame_rate"))
    fps = avg or real
    vfr = bool(avg and real and abs(avg - real) / real > 0.01)
    transfer = s.get("color_transfer")
    return VideoInfo(
        codec=s.get("codec_name", "unknown"),
        width=w,
        height=h,
        fps=round(fps, 3),
        variable_fps=vfr,
        pix_fmt=s.get("pix_fmt"),
        bit_rate=int(s["bit_rate"]) if s.get("bit_rate") else None,
        rotation=rotation,
        sar=s.get("sample_aspect_ratio", "1:1") or "1:1",
        hdr=transfer in HDR_TRANSFERS,
        color_transfer=transfer,
        color_primaries=s.get("color_primaries"),
    )


def default_audio_track(pr: Probe) -> int:
    """Default-flagged track, else the one with the most channels, else the first."""
    flagged = [a for a in pr.audio if a.default]
    pool = flagged or pr.audio
    return max(pool, key=lambda a: (a.channels, -a.index)).index


def quality_report(pr: Probe, selected_audio: int, has_zimg: bool = True) -> QualityReport:
    warnings: list[str] = []
    v = pr.video
    audio = next(a for a in pr.audio if a.index == selected_audio)
    audio_summary = f"{audio.codec}, {audio.channels} ch, {audio.sample_rate} Hz"
    if audio.sample_rate and audio.sample_rate < 16000:
        warnings.append("Audio sample rate is below 16 kHz; transcription accuracy may suffer.")
    if v is None:
        warnings.append("Audio-only source: visual stages are skipped.")
        return QualityReport(
            resolution=None,
            fps=None,
            video_bitrate_kbps=None,
            audio_summary=audio_summary,
            warnings=warnings,
        )
    if max(v.width, v.height) < 1440:
        warnings.append(
            f"Source is {v.width}x{v.height}. A vertical crop is only about "
            f"{round(v.height * 9 / 16)} px wide, so clips will be soft; "
            "a higher-resolution source (1440p or above) is recommended."
        )
    if v.variable_fps:
        warnings.append("Variable frame rate detected; it is conformed during processing.")
    if v.hdr:
        note = "HDR source: it will be tone-mapped to SDR."
        if not has_zimg:
            note = "HDR source, but this ffmpeg lacks zscale, so colours cannot be tone-mapped yet."
        warnings.append(note)
    if v.rotation:
        warnings.append(f"Rotation metadata ({v.rotation} degrees) is applied automatically.")
    if len(pr.audio) > 1:
        warnings.append(f"{len(pr.audio)} audio tracks found; track {selected_audio} is selected.")
    return QualityReport(
        resolution=f"{v.width}x{v.height}",
        fps=v.fps,
        video_bitrate_kbps=round(v.bit_rate / 1000) if v.bit_rate else None,
        audio_summary=audio_summary,
        warnings=warnings,
    )


def fast_hash(path: Path, sample: int = 4 << 20) -> str:
    """Fast dedupe hash: size plus start, middle and end samples (not an integrity check)."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        for off in (0, max(size // 2 - sample // 2, 0), max(size - sample, 0)):
            f.seek(off)
            h.update(f.read(sample))
    return h.hexdigest()[:32]


def check_free_space(directory: Path, needed: int, reserve: int = 2 * GIB) -> None:
    probe_dir = directory
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    free = shutil.disk_usage(probe_dir).free
    if free < needed + reserve:
        raise DiskSpaceError(
            f"Not enough free disk space: {free / GIB:.1f} GiB free, "
            f"about {(needed + reserve) / GIB:.1f} GiB needed.",
            "Free up space or move DATA_DIR to a larger drive.",
        )


def ffmpeg_filters() -> set[str]:
    from clipforge.doctor import parse_filters

    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    return parse_filters(out.stdout)


_TIME = re.compile(r"out_time_us=(\d+)")


def _ffmpeg(
    args: list[str],
    duration: float,
    on_progress: Callable[[float], None] | None,
    cancel: CancelToken | None,
) -> None:
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-progress", "pipe:1", "-nostats", *args]

    def on_line(line: str) -> None:
        m = _TIME.match(line)
        if m and on_progress and duration > 0:
            on_progress(min(int(m.group(1)) / 1e6 / duration, 1.0))

    code, tail = run_streaming(argv, on_line, cancel)
    if code != 0:
        raise MediaError(
            "ffmpeg failed while preparing the media.",
            "Details: " + " | ".join(line for line in tail.splitlines() if "=" not in line)[-400:],
        )


def make_wav(
    src: Path,
    dest: Path,
    track: int,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
) -> None:
    """16 kHz mono PCM for ASR, written atomically."""
    part = dest.with_suffix(".partial.wav")
    _ffmpeg(
        ["-i", str(src), "-map", f"0:{track}", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(part)],
        duration, on_progress, cancel,
    )  # fmt: skip
    part.replace(dest)


def proxy_filter(video: VideoInfo, has_zimg: bool) -> str:
    """720p analysis proxy: square pixels, capped height, tone-mapped if HDR and possible."""
    parts = []
    if video.hdr and has_zimg:
        parts.append(
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv"
        )
    parts.append("scale='trunc(iw*sar/2)*2':ih,setsar=1")
    parts.append("scale=-2:'min(720,ih)'")
    parts.append("format=yuv420p")
    return ",".join(parts)


def make_proxy(
    src: Path,
    dest: Path,
    video: VideoInfo,
    duration: float,
    has_zimg: bool,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
) -> None:
    part = dest.with_suffix(".partial.mp4")
    _ffmpeg(
        ["-i", str(src), "-map", "0:v:0", "-an", "-vf", proxy_filter(video, has_zimg),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "30",
         "-movflags", "+faststart", str(part)],
        duration, on_progress, cancel,
    )  # fmt: skip
    part.replace(dest)
