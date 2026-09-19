"""SourceProvider: acquire a master file. Everything after that is provider-agnostic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from clipforge.models import PlatformSignals
from clipforge.procs import CancelToken
from clipforge.store import Project

ProgressFn = Callable[[dict], None]


@dataclass
class Acquired:
    master: Path
    kind: str  # url | upload | path
    title: str
    origin: str | None = None
    platform: PlatformSignals = field(default_factory=PlatformSignals)
    ytdlp_version: str | None = None


class SourceProvider(Protocol):
    kind: str

    def identity(self) -> str:
        """Stable string identifying the input, used in the stage cache key."""
        ...

    def acquire(self, project: Project, cancel: CancelToken, progress: ProgressFn) -> Acquired: ...
