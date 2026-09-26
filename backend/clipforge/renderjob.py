"""One clip render as its own process: `python -m clipforge.renderjob <project_dir> <clip_id>`.

Options come from clips/<id>/render.json. Progress and the final state go to the project's event log
(`render_progress`, `render_done`, `render_error`) so the UI's render queue can follow it over SSE.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from clipforge import edits as clipedits
from clipforge.brand import load_kit
from clipforge.captions.stage import CaptionOptions
from clipforge.config import get_settings
from clipforge.curate.run import load_clips
from clipforge.curate.schema import Clip
from clipforge.errors import ClipforgeError
from clipforge.pipeline import Reporter, load_source
from clipforge.procs import Cancelled, CancelToken, cancel_on_signals
from clipforge.publish import package
from clipforge.render.clip import render_clip
from clipforge.stages import load_transcript
from clipforge.store import Project

log = logging.getLogger(__name__)


class _ClipReporter(Reporter):
    def __init__(self, project: Project, clip_id: str, cancel: CancelToken) -> None:
        super().__init__(project, cancel)
        self.clip_id = clip_id

    def progress(self, stage: str, pct: float | None = None, **extra: object) -> None:  # type: ignore[override]
        super().progress("render_progress", pct, clip=self.clip_id, phase=stage, **extra)  # type: ignore[arg-type]


def run(project: Project, clip_id: str) -> int:
    get_settings().model_copy(update={"data_dir": project.root.parent.parent})
    cancel = CancelToken()
    cancel_on_signals(cancel)
    opts = json.loads(project.path("clips", clip_id, "render.json").read_text())
    reporter = _ClipReporter(project, clip_id, cancel)
    project.emit("render_started", clip=clip_id)
    try:
        clip = next(c for c in load_clips(project) if c.id == clip_id)
        source, tr = load_source(project), load_transcript(project)
        sig = project.path("signals", "signals.json")
        cuts = json.loads(sig.read_text()).get("scene_cuts", []) if sig.exists() else []
        kit = load_kit(opts["brand"]) if opts.get("brand") else None
        caps = (
            None
            if opts.get("captions") is False
            else CaptionOptions(
                opts.get("template") or (kit.default_template if kit else "karaoke-pop"),
                opts.get("renderer", "auto"),
                opts.get("hook", True),
            )
        )
        res = render_clip(project, source, tr, clip, opts.get("cleanup", "light"), bool(opts.get("fast")), False, reporter, cancel,
                          bool(opts.get("check_faces", False)), cuts, float(opts.get("punch_in", 1.0)), caps, kit, None, opts.get("upscale"))  # fmt: skip
        project.emit("render_done", clip=clip_id, seconds=round(res.seconds, 1), path=str(res.path))
        _auto_package(project, clip_id, clip)
        return 0
    except Cancelled:
        project.emit("render_cancelled", clip=clip_id)
        return 130
    except (ClipforgeError, StopIteration) as e:
        err = (
            e.to_dict()
            if isinstance(e, ClipforgeError)
            else {
                "code": "not_found",
                "message": "That clip no longer exists.",
                "action": "Re-curate and try again.",
            }
        )
        project.emit("render_error", clip=clip_id, **err)
        return 1
    except Exception as e:
        project.emit(
            "render_error",
            clip=clip_id,
            code="internal",
            message="Unexpected error: " + str(e)[:200],
            action="See the project's logs.",
        )
        raise


def _auto_package(project: Project, clip_id: str, clip: Clip) -> None:
    """Build the 'ready to upload' package (video, thumbnail, captions, per-platform copy
    including TikTok's) right after every successful render, so the only step left before
    posting anywhere is approving the clip and grabbing the folder. Never fails the render."""
    try:
        e = clipedits.load_edits(project, clip_id)
        dest = project.root.parent.parent / "exports" / "ready" / f"{project.id}-{clip_id}"
        package.package_from_edits(
            project.path("clips", clip_id), dest, e, clip.title, clip.hook, clip.description, clip.hashtags
        )  # fmt: skip
        project.emit("package_ready", clip=clip_id, path=str(dest))
    except Exception:
        log.warning("auto-package for clip %s failed", clip_id, exc_info=True)


if __name__ == "__main__":
    sys.exit(run(Project(Path(sys.argv[1])), sys.argv[2]))
