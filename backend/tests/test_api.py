import json
import time
from importlib import import_module

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clipforge.api.app import create_app
from clipforge.config import Settings

TOKEN = "t"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(DATA_DIR=tmp_path), token=TOKEN)
    with TestClient(app, headers={"x-clipforge-token": TOKEN}) as c:
        yield c


def test_token_and_host_guards(tmp_path):
    app = create_app(Settings(DATA_DIR=tmp_path), token=TOKEN)
    with TestClient(app) as c:
        assert c.post("/api/uploads", json={"filename": "a.mp4", "size": 5}).status_code == 403
        assert c.get("/api/health").status_code == 200
    with TestClient(app, base_url="http://192.168.1.42:8765") as c:
        assert c.get("/api/health").status_code == 200
    with TestClient(app, base_url="http://evil.example.com") as c:
        assert c.get("/api/health").status_code == 403


def test_reveal_accepts_json_export_path(client, tmp_path):
    export = tmp_path / "exports" / "ready" / "project-clip"
    export.mkdir(parents=True)
    (export / "out.mp4").touch()
    response = client.post("/api/reveal", json={"path": str(export)})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_recurate_accepts_longer_duration_limits(client, tmp_path, monkeypatch):
    from clipforge.store import Project

    project = Project.create(tmp_path / "projects", "p123")
    project.path("job.json").write_text(
        json.dumps({"options": {"min_duration": 20, "max_duration": 90}})
    )
    captured = {}
    api_app = import_module("clipforge.api.app")
    monkeypatch.setattr(
        api_app,
        "get_settings",
        lambda: Settings(DATA_DIR=tmp_path).model_copy(
            update={"anthropic_api_key": SecretStr("configured")}
        ),
    )
    monkeypatch.setattr(api_app.jobs, "start", lambda p, spec: captured.update(spec) or 1)

    response = client.post(
        "/api/projects/p123/recurate",
        json={"mode": "fresh", "min_duration": 60, "max_duration": 120},
    )

    assert response.status_code == 200
    assert captured["recurate"]["min_duration"] == 60
    assert captured["recurate"]["max_duration"] == 120


def test_resolve_rejects_bad_urls_with_actionable_error(client):
    r = client.post("/api/sources/resolve", json={"url": "ftp://x.com/a"})
    assert r.status_code == 422 and r.json()["action"]
    r = client.post("/api/sources/resolve", json={"url": "http://127.0.0.1/x"})
    assert "private" in r.json()["message"]


def test_chunked_upload_resume_over_http(client):
    up = client.post("/api/uploads", json={"filename": "a.mp4", "size": 10}).json()
    uid = up["id"]
    assert (
        client.patch(f"/api/uploads/{uid}", content=b"abcd", headers={"upload-offset": "0"}).json()[
            "offset"
        ]
        == 4
    )
    bad = client.patch(f"/api/uploads/{uid}", content=b"zz", headers={"upload-offset": "0"})
    assert bad.status_code == 409 and bad.json()["offset"] == 4
    assert client.get(f"/api/uploads/{uid}").json()["offset"] == 4
    client.patch(f"/api/uploads/{uid}", content=b"efghij", headers={"upload-offset": "4"})
    assert client.get(f"/api/uploads/{uid}").json()["offset"] == 10
    assert client.delete(f"/api/uploads/{uid}").status_code == 200
    assert client.get(f"/api/uploads/{uid}").status_code == 422


def test_files_are_allow_listed_and_traversal_safe(client, tmp_path):
    pid = client.post(
        "/api/projects", json={"source": {"type": "path", "path": "/nonexistent.mp4"}}
    ).json()["project_id"]
    assert client.get(f"/api/files/{pid}/source/master.mp4").status_code == 404
    assert client.get(f"/api/files/{pid}/../../etc/passwd").status_code == 404
    assert client.get(f"/api/files/{pid}/logs/job.log").status_code == 404
    assert client.get("/api/projects/nope").status_code == 404
    assert client.get("/api/projects/..%2Fx").status_code == 404


def wait_status(client, pid, want, timeout=60):
    end = time.time() + timeout
    s: dict = {}
    while time.time() < end:
        s = client.get(f"/api/projects/{pid}").json()
        if s["status"] in want:
            return s
        time.sleep(0.3)
    raise AssertionError(f"timeout, last={s}")


def test_bad_path_job_fails_with_actionable_error_and_project_deletes(client, tmp_path):
    pid = client.post(
        "/api/projects", json={"source": {"type": "path", "path": "/nonexistent.mp4"}}
    ).json()["project_id"]
    s = wait_status(client, pid, {"error"})
    assert "not found" in s["error"]["message"].lower() and s["error"]["action"]
    assert any(p["id"] == pid for p in client.get("/api/projects").json())
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert not (tmp_path / "projects" / pid).exists()


def test_garbage_file_rejected_by_probe(client, tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not media" * 50)
    pid = client.post("/api/projects", json={"source": {"type": "path", "path": str(bad)}}).json()[
        "project_id"
    ]
    s = wait_status(client, pid, {"error"})
    assert "not readable" in s["error"]["message"]


def test_events_stream_delivers_progress(client, tmp_path):
    bad = tmp_path / "bad2.mp4"
    bad.write_bytes(b"x" * 100)
    pid = client.post("/api/projects", json={"source": {"type": "path", "path": str(bad)}}).json()[
        "project_id"
    ]
    seen = []
    with client.stream("GET", f"/api/projects/{pid}/events") as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                seen.append(line.split(":", 1)[1].strip())
            if "job_error" in seen:
                break
    assert "job_started" in seen and "job_error" in seen
    assert json.loads(json.dumps(seen))
