import json

import pytest
from fastapi.testclient import TestClient

from clipforge.api.app import create_app
from clipforge.config import Settings
from clipforge.curate.run import save_clips
from tests.test_edits_export import clip_for
from tests.test_llm_and_curate import build_project

TOKEN = "t"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # settings write ./.env
    monkeypatch.setenv("HOME", str(tmp_path))  # brand kits under ~/Clipforge
    projects = tmp_path / "data" / "projects"
    projects.mkdir(parents=True)
    project, src, tr = build_project(projects, minutes=8)
    project.path("project.json").write_text(json.dumps({"id": "cur", "title": "Synthetic"}))
    project.path("source", "source.json").write_text(src.model_dump_json())
    project.path("transcript", "transcript.json").write_text(tr.model_dump_json())
    words = tr.words
    c = clip_for(words, lo=20, hi=110).model_copy(
        update={"start": words[20].start - 0.05, "end": words[110].end + 0.05}
    )
    save_clips(project, [c])
    app = create_app(Settings(DATA_DIR=tmp_path / "data"), token=TOKEN)
    with TestClient(app, headers={"x-clipforge-token": TOKEN}) as client:
        yield client, project, tmp_path


def test_editor_payload_has_words_edl_files_and_templates(env):
    client, _, _ = env
    d = client.get("/api/projects/cur/clips/c001/editor").json()
    assert d["clip"]["id"] == "c001" and d["duration"] > 20 and d["segments"]
    assert d["words"] and all({"i", "w", "start", "end", "kept"} <= set(w) for w in d["words"])
    assert d["files"]["out.mp4"] is False and d["render"]["state"] == "idle"
    assert "karaoke-pop" in d["templates"] and len(d["templates"]) == 8


def test_text_cut_and_status_persist_and_reflect_in_the_edl(env):
    client, project, _ = env
    client.put("/api/projects/cur/clips/c001/edits", json={"cleanup": "off"})
    before = client.get("/api/projects/cur/clips/c001/editor").json()["duration"]
    r = client.put(
        "/api/projects/cur/clips/c001/edits",
        json={
            "cleanup": "off",
            "exclude": [[50, 55]],
            "status": "approved",
            "title": "Better title",
        },
    )
    assert r.status_code == 200
    d = client.get("/api/projects/cur/clips/c001/editor").json()
    assert (
        d["duration"] < before
        and d["clip"]["title"] == "Better title"
        and d["clip"]["status"] == "approved"
    )
    assert [r["kind"] for r in d["removed"]] == ["manual"]
    assert not any(w["kept"] for w in d["words"] if 50 <= w["i"] <= 55)
    assert (
        client.get("/api/projects/cur/clips").json()["clips"][0]["status"] == "approved"
    )  # clips.json kept in step
    bad = client.put("/api/projects/cur/clips/c001/edits", json={"template": "nope"})
    assert bad.status_code == 422 and "template" in bad.json()["message"].lower()
    assert client.get("/api/projects/cur/clips/zzz/editor").status_code == 404
    assert project.path("clips", "c001", "edits.json").exists()


def test_live_timeline_follows_the_template_and_the_edits(env):
    client, _, _ = env
    a = client.get("/api/projects/cur/clips/c001/timeline?template=karaoke-pop").json()
    b = client.get("/api/projects/cur/clips/c001/timeline?template=classic-subtitle").json()
    assert a["template"]["id"] == "karaoke-pop" and b["template"]["id"] == "classic-subtitle"
    assert a["timeline"]["chunks"] and a["timeline"]["hook"]["text"] == "H"
    assert len(b["timeline"]["chunks"]) <= len(
        a["timeline"]["chunks"]
    )  # subtitle style packs more words per chunk


def test_settings_never_return_secrets_and_write_env_with_0600(env):
    client, _, tmp = env
    assert (
        client.put(
            "/api/settings/secrets", json={"anthropic_api_key": "sk-ant-secretvalue1234"}
        ).status_code
        == 200
    )
    got = client.get("/api/settings").json()
    assert got["anthropic_api_key"].endswith("1234") and "secretvalue" not in json.dumps(got)
    env_file = tmp / ".env"
    assert (
        "ANTHROPIC_API_KEY=sk-ant-secretvalue1234" in env_file.read_text()
        and (env_file.stat().st_mode & 0o777) == 0o600
    )
    assert (
        client.put("/api/settings", json={"values": {"MAX_JOB_COST_USD": "0.5"}}).status_code == 200
    )
    assert (
        "MAX_JOB_COST_USD=0.5" in env_file.read_text()
        and "sk-ant-secretvalue1234" in env_file.read_text()
    )  # other keys kept
    assert (
        client.put("/api/settings", json={"values": {"MAX_JOB_COST_USD": "-3"}}).status_code == 422
    )
    assert (
        client.put("/api/settings", json={"values": {"EVIL": "1"}}).status_code == 200
    )  # unknown keys are ignored, not written
    assert "EVIL" not in env_file.read_text()


def test_storage_usage_and_cleanup_only_remove_regenerable_files(env):
    client, project, _ = env
    d = project.path("clips", "c001")
    d.mkdir(parents=True, exist_ok=True)
    (d / "audio_raw.wav").write_bytes(b"x" * 5000)
    (d / "thumb_candidates").mkdir()
    (d / "thumb_candidates" / "a.png").write_bytes(b"y" * 3000)
    (d / "out.mp4").write_bytes(b"keep")
    u = client.get("/api/storage").json()
    row = next(r for r in u["projects"] if r["id"] == "cur")
    assert row["intermediate_bytes"] >= 8000 and u["free_bytes"] > 0
    assert client.post("/api/projects/cur/cleanup").json()["freed_bytes"] >= 8000
    assert (
        not (d / "audio_raw.wav").exists()
        and not (d / "thumb_candidates").exists()
        and (d / "out.mp4").exists()
    )


def test_brand_kit_api_validates_ids_and_logos(env):
    client, _, tmp = env
    kit = {"id": "acme", "name": "Acme", "default_template": "karaoke-pop"}
    assert client.put("/api/brand/acme", json=kit).status_code == 200
    assert [k["id"] for k in client.get("/api/brand").json()] == ["acme"]
    assert client.put("/api/brand/Bad_Id", json={**kit, "id": "Bad_Id"}).status_code == 422
    assert client.post("/api/brand/acme/logo", content=b"not a png").status_code == 422
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    assert client.post("/api/brand/acme/logo", content=png).status_code == 200
    assert (tmp / "Clipforge" / "brand" / "acme" / "logo.png").exists()


def test_export_requires_a_render_for_video_and_exports_cuts_without_one(env):
    client, _, tmp = env
    r = client.post("/api/export", json={"clips": [["cur", "c001"]], "format": "mp4_zip"})
    assert r.status_code == 422 and "not been rendered" in r.json()["message"]
    ok = client.post("/api/export", json={"clips": [["cur", "c001"]], "format": "otio"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["is_zip"] and (tmp / "data" / "exports" / body["name"]).exists()
    dl = client.get(f"/api/exports/{body['name']}")
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    assert client.get("/api/exports/..%2F..%2Fsecret.zip").status_code == 404
    assert client.get("/api/renders").json() == []


def test_render_endpoints_report_status_and_cancel_cleanly(env):
    client, project, _ = env
    assert client.post("/api/projects/cur/clips/c001/render/cancel").json() == {"cancelled": False}
    project.emit("render_started", clip="c001")
    project.emit(
        "render_progress", clip="c001", stage="render_progress", pct=0.4, note="rendering video"
    )
    project.emit("render_error", clip="c001", code="media", message="boom", action="retry")
    rows = client.get("/api/renders").json()
    assert rows and rows[0]["state"] == "error" and rows[0]["error"]["message"] == "boom"


def test_project_list_shows_the_real_title(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from clipforge.api.app import create_app
    from clipforge.config import Settings

    settings = Settings(DATA_DIR=tmp_path)  # pyright: ignore[reportCallIssue]
    app = create_app(settings, token="t")
    d = tmp_path / "projects" / "abc12345"
    (d / "source").mkdir(parents=True)
    (d / "logs").mkdir()
    (d / "project.json").write_text(json.dumps({"id": "abc12345", "title": "Uploaded file"}))
    (d / "source" / "source.json").write_text(json.dumps({"title": "My Interview"}))
    rows = TestClient(app).get("/api/projects", headers={"x-clipforge-token": "t"}).json()
    assert rows[0]["title"] == "My Interview"
