import os
import threading
import time

import pytest

from clipforge.procs import Cancelled, CancelToken, run_streaming


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_streams_lines_and_exit_code():
    lines: list[str] = []
    code, tail = run_streaming(["sh", "-c", "echo a; echo b; exit 3"], lines.append)
    assert (code, lines) == (3, ["a", "b"])
    assert tail == "a\nb"


def test_cancel_kills_the_whole_process_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    token = CancelToken()
    threading.Timer(0.5, token.cancel).start()
    t0 = time.time()
    with pytest.raises(Cancelled):
        run_streaming(["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], cancel=token)
    assert time.time() - t0 < 10
    time.sleep(0.2)
    assert not alive(int(pidfile.read_text()))
