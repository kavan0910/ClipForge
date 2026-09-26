"""API wiring for character-edit mode: validation, reference-image storage, mode surfaced on the
project, and re-curate correctly refused (it's a transcript-mode feature)."""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from clipforge.api.app import create_app
from clipforge.config import Settings

TOKEN = "t"
TINY_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg-bytes").decode()


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(DATA_DIR=tmp_path), token=TOKEN)
    with TestClient(app, headers={"x-clipforge-token": TOKEN}) as c:
        yield c


def wait_status(client, pid, want, timeout=30):
    end = time.time() + timeout
    s: dict = {}
    while time.time() < end:
        s = client.get(f"/api/projects/{pid}").json()
        if s["status"] in want:
            return s
        time.sleep(0.2)
    raise AssertionError(f"timeout, last={s}")


def test_character_edit_requires_a_character_name(client):
    r = client.post(
        "/api/projects",
        json={
            "source": {"type": "path", "path": "/nonexistent.mp4"},
            "options": {"mode": "character_edit"},
        },
    )
    assert r.status_code == 422 and "character" in r.json()["message"].lower()


def test_character_edit_project_reports_its_mode_and_saves_reference_images(client, tmp_path):
    r = client.post(
        "/api/projects",
        json={
            "source": {"type": "path", "path": "/nonexistent.mp4"},
            "options": {
                "mode": "character_edit",
                "character": "Gojo",
                "reference_images": [TINY_JPEG],
            },
        },
    )
    assert r.status_code == 200
    pid = r.json()["project_id"]
    assert client.get(f"/api/projects/{pid}").json()["mode"] == "character_edit"
    assert any(
        p["id"] == pid and p.get("mode") == "character_edit"
        for p in client.get("/api/projects").json()
    )
    ref = tmp_path / "projects" / pid / "character" / "refs" / "ref_0.jpg"
    assert ref.exists() and ref.read_bytes() == base64.b64decode(TINY_JPEG)
    # the missing source file still fails the job, same as clip mode, with the usual actionable error
    s = wait_status(client, pid, {"error"})
    assert s["error"]["action"]


def test_plain_clip_project_reports_the_clips_mode(client, tmp_path):
    pid = client.post(
        "/api/projects", json={"source": {"type": "path", "path": "/nonexistent.mp4"}}
    ).json()["project_id"]
    assert client.get(f"/api/projects/{pid}").json()["mode"] == "clips"


def test_recurate_is_refused_for_a_character_edit_project(client, tmp_path):
    pid = client.post(
        "/api/projects",
        json={
            "source": {"type": "path", "path": "/nonexistent.mp4"},
            "options": {"mode": "character_edit", "character": "Gojo"},
        },
    ).json()["project_id"]
    wait_status(client, pid, {"error"})
    r = client.post(f"/api/projects/{pid}/recurate", json={"mode": "fresh"})
    assert r.status_code == 422
    assert "character edit" in r.json()["message"].lower()


def test_a_reference_image_over_the_size_limit_is_dropped_not_saved(client, tmp_path):
    huge = base64.b64encode(b"x" * 6_000_000).decode()
    pid = client.post(
        "/api/projects",
        json={
            "source": {"type": "path", "path": "/nonexistent.mp4"},
            "options": {"mode": "character_edit", "character": "Gojo", "reference_images": [huge]},
        },
    ).json()["project_id"]
    assert not (tmp_path / "projects" / pid / "character" / "refs" / "ref_0.jpg").exists()


def test_job_json_carries_the_saved_reference_image_paths_not_the_base64(client, tmp_path):
    pid = client.post(
        "/api/projects",
        json={
            "source": {"type": "path", "path": "/nonexistent.mp4"},
            "options": {
                "mode": "character_edit",
                "character": "Gojo",
                "reference_images": [TINY_JPEG],
            },
        },
    ).json()["project_id"]
    job = json.loads((tmp_path / "projects" / pid / "job.json").read_text())
    saved = job["options"]["reference_images"]
    assert len(saved) == 1 and saved[0].endswith("ref_0.jpg") and TINY_JPEG not in json.dumps(job)
