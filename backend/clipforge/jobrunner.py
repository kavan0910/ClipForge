"""Runs one project's pipeline as its own process: `python -m clipforge.jobrunner <project_dir>`.

Cancel = kill this process group (the API does it); resume = start it again. Completed
stages are skipped by the stage cache, ASR resumes from finished chunks and downloads
continue from yt-dlp's partial file.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from clipforge.config import get_settings
from clipforge.errors import ClipforgeError
from clipforge.pipeline import IngestOptions, Reporter, ingest, transcribe
from clipforge.procs import Cancelled, CancelToken, cancel_on_signals
from clipforge.providers.base import SourceProvider
from clipforge.providers.path import PathSource
from clipforge.providers.url import UrlSource
from clipforge.store import Project
from clipforge.uploads import UploadSource, UploadStore


def build_provider(spec: dict, settings) -> SourceProvider:
    kind = spec["type"]
    if kind == "url":
        return UrlSource(
            spec["url"], settings.max_source_height, settings.ytdlp_cookies_from_browser
        )
    if kind == "path":
        return PathSource(spec["path"])
    if kind == "upload":
        return UploadSource(
            UploadStore(settings.data_dir.expanduser() / "uploads"), spec["upload_id"]
        )
    raise ValueError(f"unknown source type {kind}")


def run_job(project: Project) -> int:
    # The projects directory decides the data dir, so API and runner always agree.
    settings = get_settings().model_copy(update={"data_dir": project.root.parent.parent})
    job = json.loads(project.path("job.json").read_text())
    cancel = CancelToken()
    cancel_on_signals(cancel)
    reporter = Reporter(project, cancel)
    project.emit("job_started")
    try:
        opts = job.get("options", {})
        provider = build_provider(job["source"], settings)
        src = ingest(
            project, provider, settings, reporter, cancel, IngestOptions(opts.get("audio_track"))
        )
        project.emit("source_ready", title=src.title, warnings=src.quality.warnings)
        t = transcribe(
            project, src, settings, reporter, cancel, opts.get("language"),
            opts.get("brand_vocabulary"), opts.get("diarize", True),
        )  # fmt: skip
        project.emit("job_done", words=len(t.words), sentences=len(t.sentences))
        return 0
    except Cancelled:
        project.emit("job_cancelled")
        return 130
    except ClipforgeError as e:
        project.emit("job_error", **e.to_dict())
        return 1
    except Exception as e:
        project.emit(
            "job_error", code="internal", message="Unexpected error: " + str(e)[:200],
            action="Check logs/job.log in the project folder and retry.",
        )  # fmt: skip
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(run_job(Project(Path(sys.argv[1]))))
