"""Start, cancel and inspect project jobs (each job is a separate process)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from clipforge.store import Project

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _child_env() -> dict[str, str]:
    """Children must find `clipforge` even if the editable install's .pth is missing or stale (uvicorn only
    knows the package through --app-dir)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(BACKEND_DIR), env.get("PYTHONPATH", "")) if p
    )
    return env


TERMINAL = {"job_done", "job_error", "job_cancelled"}


def _pid_file(project: Project):
    return project.path("logs", "job.pid")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(project: Project) -> int | None:
    f = _pid_file(project)
    if not f.exists():
        return None
    try:
        pid = int(f.read_text())
    except ValueError:
        return None
    return pid if _alive(pid) else None


def start(project: Project, spec: dict[str, Any] | None = None) -> int:
    """Start (or resume) the project's job. Refuses to start a second concurrent run."""
    if (pid := running_pid(project)) is not None:
        return pid
    if spec is not None:
        project.path("job.json").write_text(json.dumps(spec))
    log = project.path("logs", "job.log").open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "clipforge.jobrunner", str(project.root)],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=_child_env(),
    )  # fmt: skip
    _pid_file(project).write_text(str(proc.pid))
    # Reap the child when it exits so a finished job is not seen as alive (zombie).
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid


def cancel(project: Project, grace: float = 6.0) -> bool:
    """Signal the runner (it kills its own children); hard-kill the group after `grace`."""
    pid = running_pid(project)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    import time

    deadline = time.time() + grace
    while time.time() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return True


def status(project: Project) -> str:
    """idle | running | done | error | cancelled.

    The event log decides: a terminal event means the job is over even if the process is
    still exiting. A started job with no terminal event is running if its process is alive,
    otherwise it was killed.
    """
    events, _ = project.read_events(0)
    last = next((e for e in reversed(events) if e["type"] in TERMINAL | {"job_started"}), None)
    if last is None:
        return "running" if running_pid(project) is not None else "idle"
    if last["type"] in TERMINAL:
        return {"job_done": "done", "job_error": "error", "job_cancelled": "cancelled"}[
            last["type"]
        ]
    return "running" if running_pid(project) is not None else "cancelled"


# -- clip render jobs (one process per clip; independent of the ingest/curate job) ------------------
def render_pid_file(project: Project, clip_id: str):
    return project.path("clips", clip_id, "render.pid")


def render_running_pid(project: Project, clip_id: str) -> int | None:
    f = render_pid_file(project, clip_id)
    if not f.exists():
        return None
    try:
        pid = int(f.read_text())
    except ValueError:
        return None
    return pid if _alive(pid) else None


def start_render(project: Project, clip_id: str, options: dict[str, Any]) -> int:
    if (pid := render_running_pid(project, clip_id)) is not None:
        return pid
    d = project.path("clips", clip_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "render.json").write_text(json.dumps(options))
    log = (d / "render.log").open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "clipforge.renderjob", str(project.root), clip_id],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=_child_env(),
    )  # fmt: skip
    render_pid_file(project, clip_id).write_text(str(proc.pid))
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid


def cancel_render(project: Project, clip_id: str, grace: float = 6.0) -> bool:
    pid = render_running_pid(project, clip_id)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    import time

    end = time.time() + grace
    while time.time() < end and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return True


def render_status(project: Project, clip_id: str) -> dict[str, Any]:
    """{state: idle|running|done|error|cancelled, pct, error?} from the event log."""
    events, _ = project.read_events(0)
    mine = [e for e in events if e.get("clip") == clip_id and e["type"].startswith("render_")]
    if not mine:
        return {"state": "idle", "pct": 0.0}
    last_state = next(
        (
            e
            for e in reversed(mine)
            if e["type"] in {"render_started", "render_done", "render_error", "render_cancelled"}
        ),
        None,
    )
    prog = next((e for e in reversed(mine) if e["type"] == "render_progress"), None)
    pct = float(prog["pct"]) if prog and prog.get("pct") is not None else 0.0
    if last_state is None:
        return {"state": "idle", "pct": 0.0}
    t = last_state["type"]
    if t == "render_done":
        return {"state": "done", "pct": 1.0}
    if t == "render_error":
        return {
            "state": "error",
            "pct": pct,
            "error": {k: last_state.get(k) for k in ("code", "message", "action")},
        }
    if t == "render_cancelled":
        return {"state": "cancelled", "pct": pct}
    return {
        "state": "running" if render_running_pid(project, clip_id) is not None else "cancelled",
        "pct": pct,
        "note": prog.get("note") if prog else None,
    }
