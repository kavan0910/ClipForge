"""Core records shared by the pipeline library, API and CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AudioTrack(BaseModel):
    index: int  # ffprobe stream index
    codec: str
    channels: int
    sample_rate: int
    language: str | None = None
    title: str | None = None
    default: bool = False


class VideoInfo(BaseModel):
    codec: str
    width: int  # display width after applying rotation
    height: int
    fps: float
    variable_fps: bool
    pix_fmt: str | None = None
    bit_rate: int | None = None
    rotation: int = 0
    sar: str = "1:1"
    hdr: bool = False
    color_transfer: str | None = None
    color_primaries: str | None = None


class Probe(BaseModel):
    duration: float
    format_name: str
    size: int
    bit_rate: int | None = None
    video: VideoInfo | None = None
    audio: list[AudioTrack]


class QualityReport(BaseModel):
    resolution: str | None
    fps: float | None
    video_bitrate_kbps: int | None
    audio_summary: str
    warnings: list[str] = Field(default_factory=list)


class PlatformSignals(BaseModel):
    """Optional signals harvested from the source platform (yt-dlp)."""

    title: str | None = None
    description: str | None = None
    channel: str | None = None
    tags: list[str] = Field(default_factory=list)
    chapters: list[dict] = Field(default_factory=list)
    heatmap: list[dict] = Field(default_factory=list)
    chat_replay: bool = False


class Source(BaseModel):
    """Provider-agnostic source record. Nothing downstream branches on `kind`."""

    id: str
    kind: Literal["url", "upload", "path"]
    title: str
    content_hash: str
    master_path: str
    probe: Probe
    selected_audio: int  # ffprobe stream index
    quality: QualityReport
    platform: PlatformSignals = Field(default_factory=PlatformSignals)
    origin: str | None = None  # URL or original filename
    ytdlp_version: str | None = None
    proxy_path: str | None = None
    audio_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_video(self) -> bool:
        return self.probe.video is not None


class Word(BaseModel):
    i: int
    w: str
    start: float
    end: float
    prob: float
    speaker: str | None = None


class Sentence(BaseModel):
    id: str  # S0001
    word_lo: int  # inclusive word index
    word_hi: int  # inclusive word index
    start: float
    end: float
    text: str
    speaker: str | None = None


class Transcript(BaseModel):
    language: str
    asr_backend: str
    asr_model: str
    duration: float
    words: list[Word]
    sentences: list[Sentence]
    diarized: bool = False
    skipped: list[dict] = Field(
        default_factory=list
    )  # sections not transcribed: {start, end, language}
