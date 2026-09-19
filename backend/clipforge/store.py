"""Project directories, stage cache manifests and the append-only event log."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SAFE_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def canonical_hash(*parts: Any) -> str:
    """Stable sha256 over JSON-serialisable parts."""
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class StageRecord(BaseModel):
    stage: str
    version: int
    input_hash: str
    param_hash: str
    status: str = "done"
    started: float
    ended: float
    seconds: float
    outputs: list[str] = Field(default_factory=list)


class Project:
    """Filesystem layout for one project. The filesystem is the source of truth."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def create(cls, projects_dir: Path, project_id: str) -> Project:
        if not project_id or set(project_id) - SAFE_ID_CHARS:
            raise ValueError("invalid project id")
        root = projects_dir / project_id
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("source", "stages", "transcript", "signals", "logs"):
            (root / sub).mkdir(exist_ok=True)
        return cls(root)

    @classmethod
    def open(cls, projects_dir: Path, project_id: str) -> Project:
        if not project_id or set(project_id) - SAFE_ID_CHARS:
            raise ValueError("invalid project id")
        root = projects_dir / project_id
        if not root.is_dir():
            raise FileNotFoundError(project_id)
        return cls(root)

    @property
    def id(self) -> str:
        return self.root.name

    def path(self, *parts: str) -> Path:
        """Resolve a path inside the project, refusing traversal."""
        p = (self.root.joinpath(*parts)).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("path escapes project")
        return p

    def delete(self) -> None:
        """Remove every artifact. A symlinked master is unlinked, never followed."""
        shutil.rmtree(self.root, ignore_errors=False)

    # -- stage cache ---------------------------------------------------
    def stage_file(self, stage: str) -> Path:
        return self.path("stages", stage, "stage.json")

    def read_stage(self, stage: str) -> StageRecord | None:
        f = self.stage_file(stage)
        if not f.exists():
            return None
        try:
            return StageRecord.model_validate_json(f.read_text())
        except ValueError:
            return None

    def run_stage(
        self,
        stage: str,
        version: int,
        input_hash: str,
        params: dict[str, Any],
        fn: Callable[[], list[str]],
        outputs_exist: Callable[[], bool] | None = None,
    ) -> tuple[StageRecord, bool]:
        """Run `fn` unless a matching record exists. Returns (record, was_cached)."""
        param_hash = canonical_hash(params)
        prev = self.read_stage(stage)
        if (
            prev
            and prev.version == version
            and prev.input_hash == input_hash
            and prev.param_hash == param_hash
            and (outputs_exist is None or outputs_exist())
        ):
            return prev, True
        self.stage_file(stage).unlink(missing_ok=True)
        t0 = time.time()
        outputs = fn()
        t1 = time.time()
        rec = StageRecord(
            stage=stage,
            version=version,
            input_hash=input_hash,
            param_hash=param_hash,
            started=t0,
            ended=t1,
            seconds=round(t1 - t0, 3),
            outputs=outputs,
        )
        f = self.stage_file(stage)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".partial")
        tmp.write_text(rec.model_dump_json(indent=2))
        os.replace(tmp, f)
        return rec, False

    # -- events --------------------------------------------------------
    @property
    def events_file(self) -> Path:
        return self.path("logs", "events.jsonl")

    def emit(self, type_: str, **data: Any) -> None:
        line = json.dumps({"t": round(time.time(), 3), "type": type_, **data}, default=str)
        with self.events_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_events(self, offset: int = 0) -> tuple[list[dict], int]:
        """Return (events after byte `offset`, new offset). Partial trailing lines are left."""
        f = self.events_file
        if not f.exists():
            return [], offset
        with f.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:
            return [], offset
        chunk = data[: end + 1]
        events = [json.loads(line) for line in chunk.decode().splitlines() if line]
        return events, offset + len(chunk)
