import json
import time

import pytest
from fastapi.testclient import TestClient

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
    with TestClient(app, base_url="http://evil.example.com") as c:
        assert c.get("/api/health").status_code == 403


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
