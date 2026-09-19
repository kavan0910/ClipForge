"""Subprocess helpers: streamed output, cancellation that kills the whole process tree."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence


class Cancelled(Exception):
    """Raised when a cancel token fires."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


def cancel_on_signals(token: CancelToken) -> None:
    """Route SIGTERM/SIGINT to the cancel token so child process trees are killed cleanly."""

    def handler(signum: int, _frame: object) -> None:
        token.cancel()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def kill_tree(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """SIGTERM the process group, then SIGKILL after `grace` seconds."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait()


def run_streaming(
    argv: Sequence[str],
    on_line: Callable[[str], None] | None = None,
    cancel: CancelToken | None = None,
    env: dict[str, str] | None = None,
    grace: float = 5.0,
) -> tuple[int, str]:
    """Run argv in its own session, stream merged stdout/stderr lines, return (code, tail).

    Raises Cancelled if the token fires; the whole process group is terminated first.
    """
    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
        env=env,
    )
    done = threading.Event()

    def watchdog() -> None:
        while not done.is_set():
            if cancel and cancel.wait(0.1):
                kill_tree(proc, grace)
                return

    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            tail.append(line)
            del tail[:-40]
            if on_line:
                on_line(line)
        code = proc.wait()
    finally:
        done.set()
        thread.join(timeout=1)
        if proc.poll() is None:
            kill_tree(proc, grace)
    if cancel and cancel.cancelled:
        raise Cancelled
    return code, "\n".join(tail)
