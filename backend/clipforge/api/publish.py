"""Publishing routes: ready-to-upload package and the YouTube publisher."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from clipforge import edits as clipedits
from clipforge.config import Settings, get_settings
from clipforge.curate.run import load_clips
from clipforge.errors import ClipforgeError
from clipforge.publish import package
from clipforge.publish.youtube import PublishError, YouTubeAuth, YouTubePublisher
from clipforge.store import Project


class YouTubeRequest(BaseModel):
    schedule: str | None = None  # ISO-8601, local time when naive
    privacy: str = "private"


def register(app: FastAPI, open_project: Callable[[str], Project], settings: Settings) -> None:
    root = settings.data_dir.expanduser()
    jobs: dict[str, dict[str, Any]] = {}

    def auth() -> YouTubeAuth:
        s = get_settings()  # re-read: keys can be added while the app runs
        cid, secret = s.google_client_id, s.google_client_secret
        if not cid or not secret:
            raise PublishError(
                "YouTube publishing needs a Google OAuth client.",
                "Create a Desktop OAuth client in Google Cloud Console (YouTube Data API v3), then add its id and secret in Settings.",
            )
        return YouTubeAuth(cid, secret.get_secret_value(), root / "secrets" / "youtube_token.json")

    def clip_of(project_id: str, clip_id: str) -> tuple[Project, Any, Path]:
        p = open_project(project_id)
        c = next((c for c in load_clips(p) if c.id == clip_id), None)
        if c is None:
            raise HTTPException(404, "No such clip")
        return p, c, p.path("clips", clip_id)

    @app.post("/api/projects/{project_id}/clips/{clip_id}/package")
    async def make_package(project_id: str, clip_id: str) -> dict[str, str]:
        def work() -> dict[str, str]:
            p, c, d = clip_of(project_id, clip_id)
            e = clipedits.load_edits(p, clip_id)
            dest = root / "exports" / "ready" / f"{project_id}-{clip_id}"
            package.package_from_edits(d, dest, e, c.title, c.hook, c.description, c.hashtags)
            return {"path": str(dest)}

        try:
            return await run_in_threadpool(work)
        except FileNotFoundError as ex:
            raise HTTPException(409, str(ex)) from None

    @app.get("/api/publish/youtube/status")
    async def yt_status() -> dict[str, Any]:
        try:
            return {"configured": True, "connected": auth().connected()}
        except PublishError as e:
            return {
                "configured": False,
                "connected": False,
                "message": e.message,
                "action": e.action,
            }

    @app.post("/api/publish/youtube/connect")
    async def yt_connect() -> dict[str, bool]:
        a = auth()
        await run_in_threadpool(a.login_interactive)
        return {"connected": True}

    @app.post("/api/publish/youtube/disconnect")
    async def yt_disconnect() -> dict[str, bool]:
        auth().disconnect()
        return {"connected": False}

    @app.post("/api/projects/{project_id}/clips/{clip_id}/publish/youtube")
    async def yt_publish(project_id: str, clip_id: str, req: YouTubeRequest) -> dict[str, str]:
        p, c, d = clip_of(project_id, clip_id)
        if not (d / "out.mp4").exists():
            raise HTTPException(409, "Render the clip first.")
        pub = YouTubePublisher(auth())
        e = clipedits.load_edits(p, clip_id)
        key = f"{project_id}/{clip_id}"
        if jobs.get(key, {}).get("state") == "running":
            raise HTTPException(409, "An upload for this clip is already running.")
        jobs[key] = {"state": "running", "pct": 0.0}
        tags = e.hashtags or c.hashtags
        desc = (
            (e.description or c.description)
            + "\n\n"
            + " ".join(f"#{t}" for t in tags[:3])
            + " #Shorts"
        )

        def work() -> None:
            try:
                res = pub.publish(d / "out.mp4", e.title or c.title, desc, tags, req.schedule, d / "thumb.jpg", req.privacy,
                                  lambda x: jobs[key].update(pct=x))  # fmt: skip
                jobs[key] = {"state": "done", "pct": 1.0, **res}
            except ClipforgeError as ex:
                jobs[key] = {
                    "state": "error",
                    "pct": 0.0,
                    "message": ex.message,
                    "action": ex.action,
                }
            except Exception as ex:  # surface unexpected failures instead of a silent thread death
                jobs[key] = {
                    "state": "error",
                    "pct": 0.0,
                    "message": "The upload failed.",
                    "action": str(ex)[:200],
                }

        threading.Thread(target=work, daemon=True).start()
        return {"state": "running"}

    @app.get("/api/projects/{project_id}/clips/{clip_id}/publish/youtube")
    async def yt_job(project_id: str, clip_id: str) -> dict[str, Any]:
        return jobs.get(f"{project_id}/{clip_id}", {"state": "idle", "pct": 0.0})
