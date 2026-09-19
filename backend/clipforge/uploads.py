"""Resumable chunked uploads streamed straight to disk (no in-memory buffering).

Protocol (tus-like): create -> PATCH chunks at exact offsets -> status (HEAD) to resume.
Data lives in DATA_DIR/uploads/<id>/data.partial until complete.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from clipforge.errors import ClipforgeError, MediaError
from clipforge.media import ACCEPTED_EXTS, check_free_space
from clipforge.procs import CancelToken
from clipforge.providers.base import Acquired, ProgressFn
from clipforge.store import Project

_BAD = re.compile(r"[^A-Za-z0-9._ -]+")


def sanitize_filename(name: str) -> str:
    """Whitelist characters, drop any directory part and leading dots."""
    base = Path(name.replace("\\", "/")).name
    base = _BAD.sub("_", base).strip(" .") or "upload"
    return base[:120]


class OffsetMismatch(ClipforgeError):
    code = "offset_mismatch"

    def __init__(self, expected: int) -> None:
        super().__init__(f"Upload offset mismatch; server is at {expected}.", "Resume from there.")
        self.expected = expected


class UploadMeta(BaseModel):
    id: str
    filename: str
    size: int
    offset: int = 0

    @property
    def complete(self) -> bool:
        return self.offset >= self.size


class UploadStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, upload_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{24}", upload_id):
            raise MediaError("Unknown upload.", "Start the upload again.")
        return self.root / upload_id

    def create(self, filename: str, size: int) -> UploadMeta:
        clean = sanitize_filename(filename)
        ext = Path(clean).suffix.lower()
        if ext not in ACCEPTED_EXTS:
            raise MediaError(
                f"Unsupported file type {ext or '(none)'}.",
                "Accepted: " + ", ".join(sorted(ACCEPTED_EXTS)),
            )
        if size <= 0:
            raise MediaError("The file is empty.", "Choose a different file.")
        check_free_space(self.root, size)
        meta = UploadMeta(id=secrets.token_hex(12), filename=clean, size=size)
        d = self._dir(meta.id)
        d.mkdir(parents=True)
        (d / "data.partial").touch()
        self._save(meta)
        return meta

    def _save(self, meta: UploadMeta) -> None:
        d = self._dir(meta.id)
        tmp = d / "meta.json.tmp"
        tmp.write_text(meta.model_dump_json())
        os.replace(tmp, d / "meta.json")

    def status(self, upload_id: str) -> UploadMeta:
        f = self._dir(upload_id) / "meta.json"
        if not f.exists():
            raise MediaError("Unknown upload.", "Start the upload again.")
        meta = UploadMeta.model_validate(json.loads(f.read_text()))
        # The data file is the truth if a crash happened between write and meta save.
        actual = (self._dir(upload_id) / "data.partial").stat().st_size
        if actual != meta.offset and actual <= meta.size:
            meta.offset = actual
        return meta

    def append(self, upload_id: str, offset: int, chunks: Iterable[bytes]) -> UploadMeta:
        meta = self.status(upload_id)
        if offset != meta.offset:
            raise OffsetMismatch(meta.offset)
        data = self._dir(upload_id) / "data.partial"
        with data.open("r+b") as f:
            f.seek(offset)
            for chunk in chunks:
                if meta.offset + len(chunk) > meta.size:
                    f.truncate(meta.offset)
                    raise MediaError("Received more data than declared.", "Restart the upload.")
                f.write(chunk)
                meta.offset += len(chunk)
            f.flush()
            os.fsync(f.fileno())
        self._save(meta)
        return meta

    def cancel(self, upload_id: str) -> None:
        shutil.rmtree(self._dir(upload_id), ignore_errors=True)

    def take(self, upload_id: str, dest_dir: Path) -> tuple[Path, str]:
        """Move a finished upload into a project; returns (path, original filename)."""
        meta = self.status(upload_id)
        if not meta.complete:
            raise MediaError("The upload is not finished.", "Resume it and try again.")
        dest = dest_dir / ("master" + Path(meta.filename).suffix.lower())
        os.replace(self._dir(upload_id) / "data.partial", dest)
        shutil.rmtree(self._dir(upload_id), ignore_errors=True)
        return dest, meta.filename


class UploadSource:
    kind = "upload"

    def __init__(self, store: UploadStore, upload_id: str) -> None:
        self.store, self.upload_id = store, upload_id

    def identity(self) -> str:
        m = self.store.status(self.upload_id)
        return f"upload:{m.id}:{m.size}"

    def acquire(self, project: Project, cancel: CancelToken, progress: ProgressFn) -> Acquired:
        dest, original = self.store.take(self.upload_id, project.path("source"))
        progress({"stage": "acquire", "pct": 1.0})
        return Acquired(master=dest, kind=self.kind, title=Path(original).stem, origin=original)
