"""Import a local file by reference (symlink), never copying multi-gigabyte masters."""

from __future__ import annotations

from pathlib import Path

from clipforge.errors import MediaError
from clipforge.media import ACCEPTED_EXTS
from clipforge.procs import CancelToken
from clipforge.providers.base import Acquired, ProgressFn
from clipforge.store import Project


class PathSource:
    kind = "path"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def identity(self) -> str:
        st = self.path.stat() if self.path.exists() else None
        return f"path:{self.path.resolve()}:{st.st_size if st else 0}:{st.st_mtime_ns if st else 0}"

    def acquire(self, project: Project, cancel: CancelToken, progress: ProgressFn) -> Acquired:
        real = self.path.resolve()
        if not real.is_file():
            raise MediaError(f"File not found: {self.path}", "Check the path.")
        if real.suffix.lower() not in ACCEPTED_EXTS:
            raise MediaError(
                f"Unsupported file type {real.suffix or '(none)'}.",
                "Accepted: " + ", ".join(sorted(ACCEPTED_EXTS)),
            )
        link = project.path("source", "master" + real.suffix.lower())
        link.unlink(missing_ok=True)
        link.symlink_to(real)
        progress({"stage": "acquire", "pct": 1.0})
        return Acquired(master=link, kind=self.kind, title=real.stem, origin=str(real))
