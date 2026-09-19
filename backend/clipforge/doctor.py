"""Environment diagnostics. Every failing check carries an actionable fix."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Literal

import psutil

from clipforge.config import Settings, get_settings

Status = Literal["ok", "warn", "fail"]
GIB = 1024**3


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def add(self, name: str, status: Status, detail: str, fix: str = "") -> None:
        self.checks.append(Check(name, status, detail, fix))


def _run(argv: list[str], timeout: float = 15) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (p.stdout or "") + (p.stderr or "")


def default_render_workers(ram_bytes: int) -> int:
    """Heavy stages (Chromium + ffmpeg) share memory; scale workers with RAM."""
    return 1 if ram_bytes <= 8 * GIB else 2 if ram_bytes <= 16 * GIB else 3


def parse_filters(text: str) -> set[str]:
    """Names from `ffmpeg -filters` output (third column of each row)."""
    names: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s[TSC.]{2,3}\s+(\S+)\s", line)
        if m:
            names.add(m.group(1))
    return names


def check_hardware(r: Report, settings: Settings) -> None:
    ram = psutil.virtual_memory().total
    arch = platform.machine()
    apple = platform.system() == "Darwin" and arch == "arm64"
    r.add("platform", "ok", f"{platform.system()} {arch}, {ram / GIB:.0f} GiB RAM")
    if ram < 8 * GIB:
        r.add("memory", "fail", f"{ram / GIB:.1f} GiB is below the 8 GiB minimum")
    elif ram <= 8 * GIB:
        r.add(
            "memory",
            "warn",
            "8 GiB: heavy stages run strictly one at a time",
            "Use ASR_MODEL=large-v3-turbo and RENDER_WORKERS=1",
        )
    else:
        r.add("memory", "ok", f"render workers default to {default_render_workers(ram)}")
    backend = settings.asr_backend
    if backend == "auto":
        backend = "mlx-whisper" if apple else "faster-whisper"
    r.add("asr backend", "ok", f"{backend} (model {settings.asr_model})")


def check_disk(r: Report, settings: Settings) -> None:
    root = settings.data_dir.expanduser()
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    status: Status = "ok" if free >= 40 * GIB else "warn" if free >= 15 * GIB else "fail"
    fix = "" if status == "ok" else "Free space or set DATA_DIR to a larger volume"
    r.add("disk", status, f"{free / GIB:.0f} GiB free at {probe}", fix)


def check_ffmpeg(r: Report) -> None:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        r.add("ffmpeg", "fail", "ffmpeg/ffprobe not found", "brew install ffmpeg")
        return
    m = re.search(r"ffmpeg version (\S+)", _run([ff, "-version"]))
    r.add("ffmpeg", "ok", f"{m.group(1) if m else 'unknown'} at {ff}")
    filters = parse_filters(_run([ff, "-hide_banner", "-filters"]))
    encoders = _run([ff, "-hide_banner", "-encoders"])
    r.add(
        "libx264",
        "ok" if "libx264" in encoders else "fail",
        "present" if "libx264" in encoders else "missing",
        "Install an ffmpeg with libx264",
    )
    r.add(
        "loudnorm",
        "ok" if "loudnorm" in filters else "fail",
        "present" if "loudnorm" in filters else "missing",
        "Install a full ffmpeg build",
    )
    has_ass = "subtitles" in filters or "ass" in filters
    r.add(
        "libass",
        "ok" if has_ass else "warn",
        "present" if has_ass else "missing: ASS draft captions unavailable",
        "" if has_ass else "make ffmpeg-full (see ADR-002)",
    )
    has_zimg = "zscale" in filters
    r.add(
        "zimg (HDR tone-map)",
        "ok" if has_zimg else "warn",
        "present" if has_zimg else "missing: HDR to SDR conversion unavailable",
        "" if has_zimg else "make ffmpeg-full (see ADR-002)",
    )
    if platform.system() == "Darwin":
        vt = "h264_videotoolbox" in encoders
        r.add(
            "videotoolbox", "ok" if vt else "warn", "present" if vt else "missing (Fast mode off)"
        )


def check_ytdlp(r: Report) -> None:
    try:
        import yt_dlp.version as v

        r.add("yt-dlp", "ok", v.__version__)
    except ImportError:
        r.add("yt-dlp", "fail", "not installed", "uv sync")
    runtimes = [n for n in ("deno", "node", "bun") if shutil.which(n)]
    if "deno" in runtimes:
        r.add("yt-dlp JS runtime", "ok", "deno")
    elif runtimes:
        r.add(
            "yt-dlp JS runtime",
            "warn",
            ", ".join(runtimes) + " found but deno is recommended",
            "brew install deno (YouTube extraction may need a JS runtime)",
        )
    else:
        r.add(
            "yt-dlp JS runtime",
            "fail",
            "none found",
            "brew install deno (YouTube extraction needs a JS runtime)",
        )


def check_node(r: Report) -> None:
    node = shutil.which("node")
    if not node:
        r.add("node", "warn", "not found: Remotion captions unavailable", "brew install node")
        return
    ver = _run([node, "--version"]).strip()
    r.add("node", "ok", ver)


def check_keys(r: Report, settings: Settings) -> None:
    if settings.anthropic_api_key:
        r.add("ANTHROPIC_API_KEY", "ok", "set (value hidden)")
    else:
        r.add(
            "ANTHROPIC_API_KEY",
            "warn",
            "not set: curation disabled",
            "Add it to .env or the OS keychain",
        )
    if settings.hf_token:
        r.add("HF_TOKEN", "ok", "set (value hidden)")
    else:
        r.add(
            "HF_TOKEN",
            "warn",
            "not set: speaker diarization off (single-speaker mode)",
            "Optional: create a Hugging Face token and accept the pyannote terms",
        )


def run_doctor(settings: Settings | None = None) -> Report:
    settings = settings or get_settings()
    r = Report()
    check_hardware(r, settings)
    check_disk(r, settings)
    check_ffmpeg(r)
    check_ytdlp(r)
    check_node(r)
    check_keys(r, settings)
    return r
