from importlib import import_module
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipforge.api import publish as publish_api
from clipforge.api.app import create_app
from clipforge.api.publish import register
from clipforge.config import Settings
from clipforge.publish.tiktok_preflight import build_report
from clipforge.store import Project


def test_tiktok_preflight_flags_technical_and_content_risks():
    report = build_report(3, 1280, 720, 12, True, 0.95, True)
    by_name = {check["name"]: check for check in report["checks"]}

    assert by_name["Clip duration"]["status"] == "review"
    assert by_name["Portrait format"]["status"] == "review"
    assert by_name["Visual movement"]["status"] == "review"
    assert by_name["QR code"]["status"] == "review"
    assert by_name["Originality and rights"]["status"] == "review"
    assert "does not predict or guarantee" in report["notice"]


def test_tiktok_preflight_accepts_basic_portrait_video_but_keeps_authorship_manual():
    report = build_report(30, 1080, 1920, 30, True, 0.1, False)
    by_name = {check["name"]: check for check in report["checks"]}

    assert by_name["Clip duration"]["status"] == "ok"
    assert by_name["Portrait format"]["status"] == "ok"
    assert by_name["Frame rate"]["status"] == "ok"
    assert by_name["Visual movement"]["status"] == "ok"
    assert by_name["Originality and rights"]["status"] == "review"


def test_preflight_endpoint_requires_render_and_returns_report(tmp_path, monkeypatch):
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    project = SimpleNamespace(path=lambda *_: clip_dir)
    monkeypatch.setattr(publish_api, "load_clips", lambda _: [SimpleNamespace(id="c1")])
    report = build_report(30, 1080, 1920, 30, True, 0.1, False)
    monkeypatch.setattr(publish_api.tiktok_preflight, "inspect_video", lambda _: report)
    app = FastAPI()
    register(app, lambda _: cast(Project, project), Settings(DATA_DIR=tmp_path))

    with TestClient(app) as client:
        missing = client.get("/api/projects/p1/clips/c1/tiktok-preflight")
        assert missing.status_code == 409
        (clip_dir / "out.mp4").touch()
        ready = client.get("/api/projects/p1/clips/c1/tiktok-preflight")

    assert ready.status_code == 200
    assert ready.json() == report


def test_direct_uploaded_clip_check_cleans_temporary_upload_without_project(tmp_path, monkeypatch):
    report = build_report(30, 1080, 1920, 30, True, 0.1, False)
    checked = []

    def inspect(path):
        checked.append(path.read_bytes())
        return report

    api_app = import_module("clipforge.api.app")
    monkeypatch.setattr(api_app.tiktok_preflight, "inspect_video", inspect)
    app = create_app(Settings(DATA_DIR=tmp_path), token="token")

    with TestClient(app, headers={"x-clipforge-token": "token"}) as client:
        upload = client.post("/api/uploads", json={"filename": "finished.mp4", "size": 4}).json()
        client.patch(
            f"/api/uploads/{upload['id']}",
            content=b"clip",
            headers={"upload-offset": "0"},
        )
        result = client.post(f"/api/tiktok-preflight/{upload['id']}")
        missing = client.get(f"/api/uploads/{upload['id']}")

    assert result.status_code == 200
    assert result.json() == report
    assert checked == [b"clip"]
    assert missing.status_code == 422
    assert not list((tmp_path / "projects").glob("*"))
