"""URL provider: any yt-dlp supported link. yt-dlp runs as a subprocess so cancel kills the tree."""

from __future__ import annotations

import json
import re
import subprocess
import sys

from pydantic import BaseModel, Field

from clipforge.errors import DownloadError
from clipforge.models import PlatformSignals
from clipforge.procs import CancelToken, run_streaming
from clipforge.providers.base import Acquired, ProgressFn
from clipforge.store import Project
from clipforge.urlcheck import validate_url

UPDATE_ACTION = "Use 'Update yt-dlp' in Settings (or run: make update-ytdlp), then retry."
_ERRORS: list[tuple[str, str, str]] = [
    (r"private video", "This video is private.", "Ask the owner for access, or use your own login (cookies-from-browser in Settings)."),
    (r"confirm your age|age[- ]restricted|inappropriate for some users", "This video is age-restricted.", "Enable cookies-from-browser in Settings, using a browser signed in to an eligible account."),
    (r"members[- ]only|join this channel|available to this channel's members", "This video is for channel members only.", "Enable cookies-from-browser in Settings with a member account, or upload the file."),
    (r"available in your country|blocked it in your country|geo[- ]?(blocked|restrict)", "This video is blocked in your region.", "Use a file upload instead, or a network in a permitted region."),
    (r"video unavailable|removed|no longer available|has been terminated|does not exist", "This video is unavailable (removed or deleted).", "Check the link in your browser."),
    (r"http error 429|too many requests|rate[- ]limit", "The site is rate-limiting requests.", "Wait a few minutes and retry."),
    (r"not a bot|sign in to confirm", "YouTube asked for a sign-in check before serving this video.", "Enable cookies-from-browser in Settings, or " + UPDATE_ACTION),
    (r"unsupported url", "This link is not supported.", "Paste a direct link to a single video page."),
    (r"unable to extract|nsig|please update|extractor error|signature", "The extractor could not read this page (yt-dlp is probably out of date).", UPDATE_ACTION),
    (r"javascript runtime|js runtime|deno", "A JavaScript runtime is needed to read this site.", "Install deno (brew install deno), then retry."),
]  # fmt: skip


def classify_error(output: str) -> DownloadError:
    low = output.lower()
    for pattern, message, action in _ERRORS:
        if re.search(pattern, low):
            return DownloadError(message, action)
    last = next((ln for ln in reversed(output.splitlines()) if ln.strip()), "unknown error")
    return DownloadError(f"Download failed: {last[:200]}", UPDATE_ACTION)


class UrlPreview(BaseModel):
    url: str
    video_id: str | None
    title: str
    channel: str | None
    duration: float | None
    upload_date: str | None
    thumbnail: str | None
    heights: list[int] = Field(default_factory=list)
    availability: str | None
    extractor: str | None
    note: str | None = None
    platform: PlatformSignals


def ytdlp_version() -> str:
    import yt_dlp.version as v

    return v.__version__


def _base_argv(cookies_from_browser: str | None) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-warnings",
        "--no-playlist",
        "--playlist-items",
        "1",
    ]
    if cookies_from_browser:
        argv += ["--cookies-from-browser", cookies_from_browser]
    return argv


def parse_info(url: str, info: dict) -> UrlPreview:
    """Turn yt-dlp's info JSON into a preview card, rejecting live streams."""
    note = None
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise DownloadError("This playlist has no playable videos.", "Paste a video link.")
        note = "This link is a playlist; only the first video will be processed."
        info = entries[0]
    if info.get("live_status") in {"is_live", "is_upcoming", "post_live"} or info.get("is_live"):
        raise DownloadError(
            "Wait until the stream ends and becomes a replay.",
            "Live streams and premieres cannot be processed yet.",
        )
    heights = sorted(
        {f["height"] for f in info.get("formats", []) if f.get("height")}, reverse=True
    )
    subs = info.get("subtitles") or {}
    platform = PlatformSignals(
        title=info.get("title"),
        description=info.get("description"),
        channel=info.get("channel") or info.get("uploader"),
        tags=list(info.get("tags") or []),
        chapters=list(info.get("chapters") or []),
        heatmap=list(info.get("heatmap") or []),
        chat_replay=bool(info.get("was_live") and "live_chat" in subs),
    )
    return UrlPreview(
        url=url,
        video_id=info.get("id"),
        title=info.get("title") or "Untitled",
        channel=platform.channel,
        duration=info.get("duration"),
        upload_date=info.get("upload_date"),
        thumbnail=info.get("thumbnail"),
        heights=heights,
        availability=info.get("availability"),
        extractor=info.get("extractor_key"),
        note=note,
        platform=platform,
    )


def resolve(url: str, cookies_from_browser: str | None = None, timeout: int = 90) -> UrlPreview:
    """Metadata-only extraction (no download)."""
    url = validate_url(url)
    p = subprocess.run(
        [*_base_argv(cookies_from_browser), "-J", "--skip-download", url],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if p.returncode != 0 or not p.stdout.strip():
        raise classify_error(p.stderr or p.stdout)
    return parse_info(url, json.loads(p.stdout))


def format_selector(max_height: int) -> str:
    h = max_height
    return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"


_PROG = re.compile(r"^CFP\|(.*)$")


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def parse_progress(line: str) -> dict | None:
    m = _PROG.match(line)
    if not m:
        return None
    done, total, est, speed, eta = (m.group(1).split("|") + [""] * 5)[:5]
    done_b, tot = _num(done), _num(total) or _num(est)
    return {
        "bytes": done_b,
        "total": tot,
        "speed": _num(speed),
        "eta": _num(eta),
        "pct": (done_b / tot) if done_b is not None and tot else None,
    }


class UrlSource:
    kind = "url"

    def __init__(
        self, url: str, max_height: int = 1080, cookies_from_browser: str | None = None
    ) -> None:
        self.url = url
        self.max_height = max_height
        self.cookies = cookies_from_browser

    def identity(self) -> str:
        return f"url:{self.url}:{self.max_height}"

    def acquire(self, project: Project, cancel: CancelToken, progress: ProgressFn) -> Acquired:
        preview = resolve(self.url, self.cookies)
        out_dir = project.path("source")
        for stale in out_dir.glob("download.*"):
            if stale.suffix != ".part" and not stale.name.endswith(".part"):
                stale.unlink()
        argv = [
            *_base_argv(self.cookies),
            "-f", format_selector(self.max_height),
            "--merge-output-format", "mkv",
            "--continue", "--retries", "10", "--fragment-retries", "10",
            "--retry-sleep", "exp=1:30", "--concurrent-fragments", "4",
            "--newline",
            "--progress-template",
            "download:CFP|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
            "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s",
            "-o", str(out_dir / "download.%(ext)s"),
            validate_url(self.url),
        ]  # fmt: skip

        def on_line(line: str) -> None:
            info = parse_progress(line)
            if info:
                progress({"stage": "acquire", **info})

        code, tail = run_streaming(argv, on_line, cancel)
        if code != 0:
            raise classify_error(tail)
        files = sorted(
            (f for f in out_dir.glob("download.*") if not f.name.endswith(".part")),
            key=lambda f: f.stat().st_size,
            reverse=True,
        )
        if not files:
            raise DownloadError("yt-dlp finished but produced no file.", UPDATE_ACTION)
        master = out_dir / ("master" + files[0].suffix)
        files[0].replace(master)
        return Acquired(
            master=master,
            kind=self.kind,
            title=preview.title,
            origin=self.url,
            platform=preview.platform,
            ytdlp_version=ytdlp_version(),
        )
