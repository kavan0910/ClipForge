"""Cancelling a render must kill the whole subprocess tree (decoder, encoder, analysis)."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECTS = Path.home() / "Clipforge" / "projects" / "kende1"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (PROJECTS / "clips" / "c001").exists(), reason="needs a rendered project"
    ),
]


def running(pattern: str) -> list[str]:
    p = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return p.stdout.split()


def test_sigterm_mid_render_leaves_no_ffmpeg_and_a_rerun_completes():
    argv = [
        sys.executable,
        "-m",
        "clipforge.cli",
        "render",
        "kende1",
        "--clip",
        "c003",
        "--no-check-faces",
    ]
    proc = subprocess.Popen(
        argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    deadline = time.time() + 60
    while time.time() < deadline and not running(
        "rawvideo"
    ):  # the encoder is up: rendering is under way
        time.sleep(0.5)
    assert running("rawvideo"), "render never reached the encode stage"
    time.sleep(2)
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=30)
    time.sleep(1)
    assert running("rawvideo") == [] and running("clipforge.vision.analyze") == []
    assert proc.returncode == 130  # cancelled, not crashed
    done = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0 and (PROJECTS / "clips" / "c003" / "out.mp4").exists()
