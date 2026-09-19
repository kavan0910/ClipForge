import subprocess
from pathlib import Path

import pytest


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    m = {
        "av": d / "av.mp4",
        "noaudio": d / "noaudio.mp4",
        "garbage": d / "garbage.mp4",
        "rotated": d / "rotated.mp4",
        "twotrack": d / "twotrack.mkv",
        "audio": d / "tone.wav",
        "hd": d / "hd.mp4",
    }
    v = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3"]
    a = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3"]
    ff(*v, *a, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(m["av"]))
    ff(*v, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(m["noaudio"]))
    m["garbage"].write_bytes(b"this is not media" * 100)
    ff("-display_rotation", "90", "-i", str(m["av"]), "-c", "copy", str(m["rotated"]))
    ff(*v, *a, "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
       "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", str(m["twotrack"]))  # fmt: skip
    ff(*a, str(m["audio"]))
    hd_video = ["-f", "lavfi", "-i", "testsrc=size=2560x1440:rate=25:duration=1"]
    ff(*hd_video, *a, "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
       str(m["hd"]))  # fmt: skip
    return m
