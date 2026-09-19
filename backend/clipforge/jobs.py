"""Start, cancel and inspect project jobs (each job is a separate process)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from typing import Any

from clipforge.store import Project

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
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
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
