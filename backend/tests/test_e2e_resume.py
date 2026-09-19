"""Slow, real end-to-end test: kill ASR mid-run, resume, verify no rework and no duplicates."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

MEDIA = Path(__file__).resolve().parents[2] / "eval/fixtures/media"
A = MEDIA / "hannah_cloke_extreme_weather.webm"
B = MEDIA / "kende_internet_hall_of_fame.webm"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(1500),
    pytest.mark.skipif(not (A.exists() and B.exists()), reason="run `make fixtures` first"),
]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_kill_mid_asr_then_resume(tmp_path):
    long_audio = tmp_path / "long.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(A), "-i", str(B), "-filter_complex",
         "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]", "-ac", "1", "-ar", "22050",
         str(long_audio)],
        check=True,
    )  # fmt: skip
    env = {**os.environ, "DATA_DIR": str(tmp_path / "data")}
    argv = [sys.executable, "-m", "clipforge.cli", "run", str(long_audio), "--project", "r1",
            "--no-diarize"]  # fmt: skip
    proc = subprocess.Popen(argv, env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    tdir = tmp_path / "data/projects/r1/transcript"

    def first_chunk() -> Path | None:
        return next(iter(tdir.glob("chunks/*/chunk_0000.json")), None)

    deadline = time.time() + 900
    while first_chunk() is None and time.time() < deadline:
        time.sleep(1)
    first = first_chunk()
    assert first is not None, "first ASR chunk never finished"
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=30)
    time.sleep(0.5)
    ps = subprocess.run(["pgrep", "-f", "clipforge.asr.worker"], capture_output=True, text=True)
    assert ps.stdout.strip() == "", "ASR worker survived cancellation"
    assert not (tdir / "transcript.json").exists()
    mtime = first.stat().st_mtime_ns

    done = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert first.stat().st_mtime_ns == mtime, "finished chunk was recomputed"
    t = json.loads((tdir / "transcript.json").read_text())
    starts = [w["start"] for w in t["words"]]
    assert starts == sorted(starts) and len(t["words"]) > 1500
    assert t["duration"] == pytest.approx(860.9, abs=1)
    # No duplicated phrase across the chunk seam (585-600 s).
    seam = [w["w"].lower() for w in t["words"] if 580 <= w["start"] <= 605]
    grams = [tuple(seam[i : i + 4]) for i in range(len(seam) - 3)]
    assert len(grams) == len(set(grams))
