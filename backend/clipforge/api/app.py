"""FastAPI app: localhost-only REST + SSE over the pipeline library."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from clipforge import jobs
from clipforge.config import Settings, get_settings
from clipforge.doctor import run_doctor
from clipforge.errors import ClipforgeError, MediaError
from clipforge.providers import url as url_provider
from clipforge.store import Project
from clipforge.uploads import OffsetMismatch, UploadStore

MAX_CHUNK = 16 * 1024 * 1024
TOKEN_HEADER = "x-clipforge-token"  # noqa: S105
SERVABLE = ("source/proxy_720p.mp4", "source/source.json", "transcript/transcript.json")
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class SourceSpec(BaseModel):
    type: Literal["url", "upload", "path"]
    url: str | None = None
    upload_id: str | None = None
    path: str | None = None


class Options(BaseModel):
    clips: int | None = None
    steering: str = ""
    preset: Literal["economy", "balanced", "max"] = "balanced"
    min_duration: float = 20.0
    max_duration: float = 90.0
    curate: bool = True
    language: str | None = None
    diarize: bool = True
    audio_track: int | None = None
    brand_vocabulary: list[str] = Field(default_factory=list)


class CreateProject(BaseModel):
    source: SourceSpec
    options: Options = Field(default_factory=Options)


class ResolveRequest(BaseModel):
    url: str


class UploadCreate(BaseModel):
    filename: str
    size: int


def create_app(settings: Settings | None = None, token: str | None = None) -> FastAPI:
    settings = settings or get_settings()
    token = token or os.environ.get("CLIPFORGE_TOKEN") or secrets.token_urlsafe(24)
    projects_dir = settings.projects_dir
    projects_dir.mkdir(parents=True, exist_ok=True)
    uploads = UploadStore(settings.data_dir.expanduser() / "uploads")
    app = FastAPI(title="Clipforge", docs_url=None, redoc_url=None)
    app.state.token = token

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "[::1]", "testserver"}:
            return JSONResponse({"code": "bad_host", "message": "Host not allowed."}, 403)
        if (
            request.url.path.startswith("/api")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get(TOKEN_HEADER) != token
        ):
            return JSONResponse(
                {"code": "forbidden", "message": "Missing or wrong session token.",
                 "action": "Reload the page."}, 403,
            )  # fmt: skip
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ClipforgeError)
    async def clipforge_error(_: Request, exc: ClipforgeError):
        status = 409 if isinstance(exc, OffsetMismatch) else 422
        body: dict[str, Any] = dict(exc.to_dict())
        if isinstance(exc, OffsetMismatch):
            body["offset"] = exc.expected
        return JSONResponse(body, status)

    def open_project(project_id: str) -> Project:
        try:
            return Project.open(projects_dir, project_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Project not found") from None

    # -- system ----------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "ytdlp": url_provider.ytdlp_version()}

    @app.get("/api/doctor")
    async def doctor() -> dict[str, Any]:
        report = await run_in_threadpool(run_doctor, settings)
        return {"ok": report.ok, "checks": [c.__dict__ for c in report.checks]}

    @app.post("/api/ytdlp/update")
    async def update_ytdlp() -> dict[str, Any]:
        uv = shutil.which("uv")
        argv = [uv, "pip", "install", "--upgrade", "yt-dlp"] if uv else [
            sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]  # fmt: skip
        p = await run_in_threadpool(
            lambda: subprocess.run(argv, capture_output=True, text=True, timeout=300, check=False)
        )
        if p.returncode != 0:
            raise ClipforgeError("Updating yt-dlp failed.", (p.stderr or p.stdout)[-300:])
        return {"ok": True, "note": "Restart Clipforge to load the new version."}

    # -- link source ------------------------------------------------------
    @app.post("/api/sources/resolve")
    async def resolve(req: ResolveRequest) -> dict[str, Any]:
        preview = await run_in_threadpool(
            url_provider.resolve, req.url, settings.ytdlp_cookies_from_browser
        )
        cap = settings.max_source_height
        usable = [h for h in preview.heights if h <= cap]
        return {**preview.model_dump(), "will_download_height": max(usable) if usable else None,
                "max_source_height": cap}  # fmt: skip

    # -- uploads ------------------------------------------------------------
    @app.post("/api/uploads")
    async def create_upload(req: UploadCreate) -> dict[str, Any]:
        meta = await run_in_threadpool(uploads.create, req.filename, req.size)
        return meta.model_dump()

    @app.get("/api/uploads/{upload_id}")
    async def upload_status(upload_id: str) -> dict[str, Any]:
        return (await run_in_threadpool(uploads.status, upload_id)).model_dump()

    @app.patch("/api/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request) -> dict[str, Any]:
        try:
            offset = int(request.headers.get("upload-offset", ""))
        except ValueError:
            raise MediaError("Missing Upload-Offset header.", "Client bug.") from None
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > MAX_CHUNK:
                raise HTTPException(413, "Chunk too large; send at most 16 MiB per request.")
        meta = await run_in_threadpool(uploads.append, upload_id, offset, [bytes(buf)])
        return meta.model_dump()

    @app.delete("/api/uploads/{upload_id}")
    async def cancel_upload(upload_id: str) -> dict[str, bool]:
        await run_in_threadpool(uploads.cancel, upload_id)
        return {"ok": True}

    # -- projects -------------------------------------------------------------
    @app.post("/api/projects")
    async def create_project(req: CreateProject) -> dict[str, Any]:
        spec = req.source
        if spec.type == "url" and not spec.url:
            raise MediaError("No link given.", "Paste a video link.")
        if spec.type == "upload":
            if not spec.upload_id:
                raise MediaError("No upload given.", "Upload a file first.")
            if not (await run_in_threadpool(uploads.status, spec.upload_id)).complete:
                raise MediaError("The upload is not finished.", "Wait for it to complete.")
        if spec.type == "path" and not spec.path:
            raise MediaError("No path given.", "Enter a file path.")
        pid = secrets.token_hex(4)
        project = Project.create(projects_dir, pid)
        title = spec.url or spec.path or "Uploaded file"
        project.path("project.json").write_text(json.dumps({"id": pid, "title": title}))
        job = {"source": spec.model_dump(exclude_none=True), "options": req.options.model_dump()}
        await run_in_threadpool(jobs.start, project, job)
        return {"project_id": pid}

    @app.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        out = []
        for d in sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if (d / "project.json").exists():
                meta = json.loads((d / "project.json").read_text())
                out.append({**meta, "status": jobs.status(Project(d))})
        return out

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        p = open_project(project_id)
        data: dict[str, Any] = {"id": p.id, "status": jobs.status(p)}
        src, tr = p.path("source", "source.json"), p.path("transcript", "transcript.json")
        if src.exists():
            s = json.loads(src.read_text())
            s.pop("master_path", None)
            data["source"] = s
        if tr.exists():
            t = json.loads(tr.read_text())
            data["transcript"] = {
                k: t[k] for k in ("language", "asr_model", "duration", "diarized")
            }
            data["transcript"]["words"] = len(t["words"])
            data["transcript"]["sentences"] = t["sentences"]
        events, _ = p.read_events(0)
        err = next((e for e in reversed(events) if e["type"] == "job_error"), None)
        if err and data["status"] == "error":
            data["error"] = {k: err.get(k) for k in ("code", "message", "action")}
        return data

    @app.post("/api/projects/{project_id}/cancel")
    async def cancel_project(project_id: str) -> dict[str, bool]:
        p = open_project(project_id)
        return {"cancelled": await run_in_threadpool(jobs.cancel, p)}

    @app.post("/api/projects/{project_id}/resume")
    async def resume_project(project_id: str) -> dict[str, Any]:
        p = open_project(project_id)
        return {"pid": await run_in_threadpool(jobs.start, p)}

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, bool]:
        p = open_project(project_id)
        await run_in_threadpool(jobs.cancel, p)
        await run_in_threadpool(p.delete)
        return {"ok": True}

    class Recurate(BaseModel):
        mode: Literal["fresh", "more_like", "shorter", "different_topic"] = "fresh"
        reference_clip_id: str | None = None
        steering: str = ""
        clips: int | None = None
        preset: str = "balanced"

    @app.get("/api/projects/{project_id}/clips")
    async def list_clips(project_id: str) -> dict[str, Any]:
        from clipforge.curate.run import load_clips

        p = open_project(project_id)
        clips = await run_in_threadpool(load_clips, p)
        return {"clips": [c.model_dump() for c in clips], "label": "Clip score (heuristic)"}

    @app.post("/api/projects/{project_id}/recurate")
    async def recurate(project_id: str, req: Recurate) -> dict[str, Any]:
        p = open_project(project_id)
        if not settings.anthropic_api_key:
            raise ClipforgeError(
                "No Anthropic API key is configured.", "Add ANTHROPIC_API_KEY to .env."
            )
        spec = json.loads(p.path("job.json").read_text())
        spec["recurate"] = req.model_dump()
        pid = await run_in_threadpool(jobs.start, p, spec)
        return {"pid": pid}

    @app.get("/api/projects/{project_id}/events")
    async def events(project_id: str, request: Request) -> EventSourceResponse:
        p = open_project(project_id)
        try:
            offset = int(
                request.headers.get("last-event-id") or request.query_params.get("from", 0)
            )
        except ValueError:
            offset = 0

        async def stream() -> AsyncIterator[dict[str, str]]:
            nonlocal offset
            while True:
                if await request.is_disconnected():
                    return
                new, offset_after = await run_in_threadpool(p.read_events, offset)
                for e in new:
                    yield {"event": e["type"], "data": json.dumps(e), "id": str(offset_after)}
                offset = offset_after
                if any(e["type"] in jobs.TERMINAL for e in new) and jobs.running_pid(p) is None:
                    return  # job finished: the client closes; a resume opens a new stream
                if not new:
                    yield {"event": "ping", "data": "{}"}
                await asyncio.sleep(0.4)

        return EventSourceResponse(stream(), ping=15)

    @app.get("/api/files/{project_id}/{rel:path}")
    async def files(project_id: str, rel: str) -> FileResponse:
        p = open_project(project_id)
        if rel not in SERVABLE and not rel.startswith("clips/"):
            raise HTTPException(404, "Not found")
        try:
            path = p.path(*rel.split("/"))
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "Not found")
        return FileResponse(path)

    # -- built UI ---------------------------------------------------------------
    if (WEB_DIST / "index.html").exists():
        index = (
            (WEB_DIST / "index.html")
            .read_text()
            .replace("</head>", f'<meta name="clipforge-token" content="{token}"></head>')
        )

        @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
        async def spa(full_path: str) -> Response:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(404, "Not found")
            f = (WEB_DIST / full_path).resolve()
            if full_path and f.is_file() and f.is_relative_to(WEB_DIST.resolve()):
                return FileResponse(f)
            return HTMLResponse(index)

    return app


app = create_app() if os.environ.get("CLIPFORGE_NO_APP") != "1" else None  # pyright: ignore[reportAssignmentType]
