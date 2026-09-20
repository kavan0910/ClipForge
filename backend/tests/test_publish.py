"""Publisher protocol tests over a mock transport (the real network is not touched)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest

from clipforge.publish import package
from clipforge.publish.social import InstagramPublisher, NotAvailable
from clipforge.publish.youtube import (
    CHUNK,
    UPLOAD_URL,
    PublishError,
    YouTubeAuth,
    YouTubePublisher,
    clean_tags,
    clean_title,
    parse_schedule,
)  # fmt: skip
from clipforge.render import audiogram


def make_pub(tmp_path: Path, handler) -> tuple[YouTubePublisher, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    http = httpx.Client(transport=httpx.MockTransport(wrapped))
    tok = tmp_path / "t.json"
    tok.write_text(json.dumps({"access_token": "AT", "refresh_token": "RT", "expires_at": 9e12}))
    return YouTubePublisher(
        YouTubeAuth("id", "secret", tok, http), http, sleep=lambda _: None
    ), seen


def test_title_and_tags_are_sanitised():
    assert clean_title("<b>Hi</b> " + "x" * 200) == ("bHi/b " + "x" * 200)[:100]
    tags = clean_tags(["#one", "two,three", "x" * 600])
    assert tags == ["one", "twothree"]


def test_schedule_must_be_future():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_schedule("2026-01-02T10:00:00+00:00", now) == "2026-01-02T10:00:00.000Z"
    with pytest.raises(PublishError):
        parse_schedule("2025-12-31T10:00:00+00:00", now)
    assert parse_schedule(None) is None


def test_resumable_upload_scheduling_and_resume(tmp_path: Path):
    video = tmp_path / "out.mp4"
    video.write_bytes(b"v" * (CHUNK + 1000))
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"jpg")
    state = {"puts": 0}
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/videos") and req.method == "POST":
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private" and "publishAt" in body["status"]
            assert body["status"]["selfDeclaredMadeForKids"] is False
            assert req.headers["authorization"] == "Bearer AT"
            assert req.headers["x-upload-content-length"] == str(CHUNK + 1000)
            return httpx.Response(200, headers={"location": "https://upload.example/session"})
        if req.url.host == "upload.example":
            state["puts"] += 1
            if state["puts"] == 1:
                assert req.headers["content-range"] == f"bytes 0-{CHUNK - 1}/{CHUNK + 1000}"
                return httpx.Response(308, headers={"range": f"bytes=0-{CHUNK - 1}"})
            if state["puts"] == 2:
                return httpx.Response(503)  # transient: must query the offset, then resume
            if req.content == b"":
                assert req.headers["content-range"] == f"bytes */{CHUNK + 1000}"
                return httpx.Response(308, headers={"range": f"bytes=0-{CHUNK - 1}"})
            assert req.headers["content-range"] == f"bytes {CHUNK}-{CHUNK + 999}/{CHUNK + 1000}"
            return httpx.Response(200, json={"id": "VID1", "status": {"privacyStatus": "private"}})
        if "thumbnails" in req.url.path:
            return httpx.Response(403, json={"error": {"message": "not verified"}})
        raise AssertionError(req.url)

    pub, _ = make_pub(tmp_path, handler)
    progress: list[float] = []
    res = pub.publish(video, "T", "D", ["a"], future, thumb, on_progress=progress.append)
    assert res["id"] == "VID1" and res["url"] == "https://youtu.be/VID1"
    assert res["scheduled_for"].endswith("Z")
    assert progress[-1] == 1.0 and any("thumbnail" in w for w in res["warnings"])


def test_quota_error_is_explained(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    err = {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
    pub, _ = make_pub(tmp_path, lambda r: httpx.Response(403, json=err))
    with pytest.raises(PublishError) as e:
        pub.publish(video, "T", "D")
    assert "quota" in e.value.message.lower()
    assert "6 uploads" in e.value.action


def test_refresh_and_token_file_mode(tmp_path: Path):
    tok = tmp_path / "t.json"
    tok.write_text(json.dumps({"access_token": "old", "refresh_token": "RT", "expires_at": 0}))
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        )
    )
    a = YouTubeAuth("id", "s", tok, http)
    assert a.access_token() == "new"
    assert oct(tok.stat().st_mode & 0o777) == "0o600"
    a.disconnect()
    assert not a.connected()
    with pytest.raises(PublishError):
        a.access_token()


def test_authorization_url_uses_pkce_and_offline_access(tmp_path: Path):
    a = YouTubeAuth("cid", "s", tmp_path / "t.json")
    url = a.authorization_url("http://127.0.0.1:1/", "st", "chal")
    assert (
        "code_challenge_method=S256" in url
        and "access_type=offline" in url
        and "youtube.upload" in url
    )


def test_instagram_is_a_documented_stub():
    with pytest.raises(NotAvailable) as e:
        InstagramPublisher().publish(Path("x"), "t", "d", None)
    assert "package" in e.value.action


def test_package_contents(tmp_path: Path):
    d = tmp_path / "clip"
    d.mkdir()
    (d / "out.mp4").write_bytes(b"v")
    (d / "thumb.jpg").write_bytes(b"j")
    dest = package.build_package(
        d, tmp_path / "ready", "T" * 150, "Hook", "Desc", ["a", "b", "c", "d", "e", "f"]
    )
    info = json.loads((dest / "copy.json").read_text())
    assert len(info["copy"]["youtube_shorts"]["title"]) == 100
    assert info["copy"]["instagram_reels"]["caption"].count("#") == 5
    assert (dest / "README.txt").exists() and (dest / "out.mp4").exists()
    with pytest.raises(FileNotFoundError):
        package.build_package(tmp_path, tmp_path / "x", "t", "h", "d", [])


def test_audiogram_levels_follow_the_audio():
    sr = 48000
    t = np.arange(sr * 2) / sr
    pcm = np.where(t < 1, 0.3 * np.sin(2 * np.pi * 440 * t), 0.0).astype(np.float32)
    lv = audiogram.spectrum_frames(pcm, 60)
    assert lv[10].max() > 0.5 and lv[50].max() < 0.05
    frame = audiogram.draw_frame(
        audiogram.background((0, 0, 0), (10, 10, 30)),
        audiogram.title_layer("Hello", (255, 255, 255)),
        lv[10],
        (255, 200, 0),
    )
    assert frame.shape == (1920, 1080, 3) and frame.dtype == np.uint8
    assert UPLOAD_URL.startswith("https://www.googleapis.com/upload/youtube/v3")


def test_panns_files_download_without_wget(tmp_path: Path):
    from clipforge.errors import MediaError
    from clipforge.signals import audio

    calls: list[str] = []

    def fake(url: str, dest: Path) -> None:
        calls.append(dest.name)
        dest.write_bytes(b"x" * (400_000_000 if dest.name.endswith(".pth.part") else 20_000))

    audio.ensure_panns_files(tmp_path, fake)
    assert {p.name for p in tmp_path.iterdir()} == set(audio.PANNS_FILES)
    audio.ensure_panns_files(tmp_path, fake)  # complete files are not fetched again
    assert len(calls) == 2
    (tmp_path / "class_labels_indices.csv").write_bytes(
        b""
    )  # a wget leftover of 0 bytes is replaced
    audio.ensure_panns_files(tmp_path, fake)
    assert len(calls) == 3

    def short(url: str, dest: Path) -> None:
        dest.write_bytes(b"x")

    (tmp_path / "class_labels_indices.csv").unlink()
    with pytest.raises(MediaError):
        audio.ensure_panns_files(tmp_path, short)
    assert not list(tmp_path.glob("*.part"))


def test_face_models_download_and_extract(tmp_path: Path):
    import io
    import zipfile

    from clipforge.vision import models

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("det_500m.onnx", b"x" * 2_500_000)
    zbytes = buf.getvalue()
    calls: list[str] = []

    def fake(url: str, dest: Path) -> None:
        calls.append(dest.name)
        dest.write_bytes(b"y" * models.YUNET[1] if "onnx" in dest.name else zbytes)

    import pytest as _pt

    with _pt.MonkeyPatch.context() as mp:
        mp.setattr(models, "BUFFALO", (models.BUFFALO[0], len(zbytes)))
        models.ensure_face_models(tmp_path, fake)
        assert (tmp_path / "yunet.onnx").exists() and (
            tmp_path / "buffalo_sc" / "det_500m.onnx"
        ).exists()
        models.ensure_face_models(tmp_path, fake)  # present: nothing is fetched again
        assert len(calls) == 2


def test_env_migration_and_enlargement(tmp_path):
    from clipforge import settings_store
    from clipforge.reframe.camera import Rect
    from clipforge.reframe.solve import FrameSpec
    from clipforge.render.sr import enlargement

    env = tmp_path / ".env"
    env.write_text("A=1\nMAX_SOURCE_HEIGHT=1080\n")
    assert settings_store.migrate_env(env) is True
    assert "MAX_SOURCE_HEIGHT=2160" in env.read_text()
    assert settings_store.migrate_env(env) is False  # once only
    env.write_text("MAX_SOURCE_HEIGHT=1440\n")
    assert settings_store.migrate_env(env) is False  # a deliberate choice is left alone
    frames = [FrameSpec("single", [Rect(0, 0, 608, 1080)]) for _ in range(5)]
    assert abs(enlargement(frames) - 1920 / 1080) < 1e-6
    assert enlargement([FrameSpec("single", [Rect(0, 0, 1215, 2160)])]) < 1.0
