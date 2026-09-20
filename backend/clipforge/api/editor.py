"""Review/editor, render queue, export and settings routes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from clipforge import brand as brandmod
from clipforge import edits as clipedits
from clipforge import export as exportmod
from clipforge import jobs, settings_store, storage
from clipforge.captions.spec import list_templates, load_template
from clipforge.captions.timeline import build_timeline
from clipforge.config import Settings, get_settings
from clipforge.curate.run import load_clips, save_clips
from clipforge.curate.schema import Clip
from clipforge.errors import MediaError
from clipforge.pipeline import load_source, rms_db_frames
from clipforge.stages import load_transcript
from clipforge.store import Project


class RenderRequest(BaseModel):
    fast: bool = False
    template: str | None = None
    brand: str | None = None
    renderer: Literal["auto", "remotion", "ass"] = "auto"
    captions: bool = True
    cleanup: Literal["off", "light", "aggressive"] = "light"
    punch_in: float = 1.0


class ExportRequest(BaseModel):
    clips: list[tuple[str, str]]  # (project id, clip id)
    format: Literal["mp4_folder", "mp4_zip", "otio", "fcpxml", "fcp7", "edl"]


class SettingsUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class ApiKey(BaseModel):
    anthropic_api_key: str | None = None
    hf_token: str | None = None
    google_client_secret: str | None = None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "clip"


def register(
    app: FastAPI, open_project: Callable[[str], Project], projects_dir: Path, settings: Settings
) -> None:
    def rms(p: Project) -> list[float]:
        """100 ms RMS frames of the project's audio, cached (computing it for hours of audio is slow)."""
        f = p.path("source", "rms_db.json")
        if f.exists():
            return json.loads(f.read_text())
        src = load_source(p)
        data = [round(x, 1) for x in rms_db_frames(Path(src.audio_path or ""))]
        f.write_text(json.dumps(data))
        return data

    def find_clip(p: Project, clip_id: str) -> Clip:
        c = next((c for c in load_clips(p) if c.id == clip_id), None)
        if c is None:
            raise HTTPException(404, "Clip not found")
        return c

    # -- editor ---------------------------------------------------------------------------------
    @app.get("/api/projects/{project_id}/clips/{clip_id}/editor")
    async def editor(project_id: str, clip_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            p = open_project(project_id)
            clip = find_clip(p, clip_id)
            src, tr = load_source(p), load_transcript(p)
            e = clipedits.load_edits(p, clip_id)
            eff = clipedits.effective_clip(clip, e)
            edl = clipedits.build_edl(eff, tr.words, e, rms(p), src.probe.duration)
            kept = {w.i for w in edl.remap_words(tr.words)}
            lo, hi = clipedits.first_last_words(clip, tr.words)
            ctx_lo, ctx_hi = (
                max(lo - 40, 0),
                min(hi + 40, len(tr.words) - 1),
            )  # a little context to extend the trim
            d = p.path("clips", clip_id)
            reframe = (
                json.loads((d / "reframe.json").read_text())
                if (d / "reframe.json").exists()
                else None
            )
            return {
                "clip": eff.model_dump(), "edits": e.model_dump(), "duration": round(edl.duration, 3),
                "segments": [s.model_dump() for s in edl.segments], "removed": [r.model_dump() for r in edl.removed],
                "words": [{"i": w.i, "w": w.w, "start": w.start, "end": w.end, "kept": w.i in kept, "speaker": w.speaker} for w in tr.words[ctx_lo : ctx_hi + 1]],
                "first_word": lo, "last_word": hi,
                "layouts": [{"t0": s["t0"], "t1": s["t1"], "layout": s["layout"]} for s in reframe["plan"]] if reframe else [],
                "files": {n: (d / n).exists() for n in ("out.mp4", "thumb.jpg", "base_preview.mp4", "out.srt", "captions.json")},
                "render": jobs.render_status(p, clip_id), "templates": list_templates(), "brand_kits": brandmod.list_kits(),
            }  # fmt: skip

        return await run_in_threadpool(work)

    @app.put("/api/projects/{project_id}/clips/{clip_id}/edits")
    async def put_edits(project_id: str, clip_id: str, e: clipedits.ClipEdits) -> dict[str, Any]:
        p = open_project(project_id)
        find_clip(p, clip_id)
        if e.template and e.template not in list_templates():
            raise MediaError(f"Unknown caption template {e.template!r}.", "Pick one from the list.")
        await run_in_threadpool(clipedits.save_edits, p, clip_id, e)
        if e.status:  # keep clips.json in step so lists and exports see the decision
            clips = load_clips(p)
            save_clips(
                p,
                [
                    c.model_copy(update={"status": e.status}) if c.id == clip_id else c
                    for c in clips
                ],
            )
        return {"ok": True}

    @app.get("/api/projects/{project_id}/clips/{clip_id}/timeline")
    async def preview_timeline(
        project_id: str, clip_id: str, template: str | None = None
    ) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            p = open_project(project_id)
            clip = find_clip(p, clip_id)
            tr, src = load_transcript(p), load_source(p)
            e = clipedits.load_edits(p, clip_id)
            eff = clipedits.effective_clip(clip, e)
            edl = clipedits.build_edl(eff, tr.words, e, rms(p), src.probe.duration)
            tpl = load_template(template or e.template or "karaoke-pop")
            tl = build_timeline(
                edl.remap_words(tr.words),
                tpl,
                edl.duration,
                set(eff.emphasis_word_indices),
                hook=eff.hook,
                hook_enabled=e.hook_enabled,
            )
            return {"timeline": tl.model_dump(), "template": tpl.model_dump()}

        return await run_in_threadpool(work)

    # -- render queue -----------------------------------------------------------------------------
    @app.post("/api/projects/{project_id}/clips/{clip_id}/render")
    async def render(project_id: str, clip_id: str, req: RenderRequest) -> dict[str, Any]:
        p = open_project(project_id)
        find_clip(p, clip_id)
        opts = req.model_dump()
        pid = await run_in_threadpool(jobs.start_render, p, clip_id, opts)
        return {"pid": pid}

    @app.post("/api/projects/{project_id}/clips/{clip_id}/render/cancel")
    async def render_cancel(project_id: str, clip_id: str) -> dict[str, bool]:
        return {
            "cancelled": await run_in_threadpool(
                jobs.cancel_render, open_project(project_id), clip_id
            )
        }

    @app.get("/api/renders")
    async def renders() -> list[dict[str, Any]]:
        def work() -> list[dict[str, Any]]:
            rows = []
            for d in projects_dir.iterdir() if projects_dir.exists() else []:
                if not (d / "curation").exists():
                    continue
                p = Project(d)
                for c in load_clips(p):
                    st = jobs.render_status(p, c.id)
                    if st["state"] != "idle":
                        rows.append({"project": d.name, "clip": c.id, "title": c.title, "duration": c.duration, **st,
                                     "has_video": (d / "clips" / c.id / "out.mp4").exists(), "status": c.status})  # fmt: skip
            return rows

        return await run_in_threadpool(work)

    # -- export --------------------------------------------------------------------------------------
    @app.post("/api/export")
    async def export(req: ExportRequest) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            export_dir = settings.data_dir.expanduser() / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            files: dict[str, list[Path]] = {}
            warnings: list[str] = []
            for pid, cid in req.clips:
                p = open_project(pid)
                clip = find_clip(p, cid)
                d = p.path("clips", cid)
                d.mkdir(parents=True, exist_ok=True)
                name = slug(clip.title) + f"-{pid}-{cid}"
                if req.format in ("mp4_folder", "mp4_zip"):
                    if not (d / "out.mp4").exists():
                        raise MediaError(
                            f"“{clip.title}” has not been rendered yet.",
                            "Render it first (Render queue).",
                        )
                    files[name] = [
                        d / n
                        for n in (
                            "out.mp4",
                            "thumb.jpg",
                            "out.srt",
                            "out.vtt",
                            "out.ass",
                            "metadata.json",
                        )
                    ]
                else:
                    src, tr = load_source(p), load_transcript(p)
                    e = clipedits.load_edits(p, cid)
                    edl = clipedits.build_edl(
                        clipedits.effective_clip(clip, e), tr.words, e, rms(p), src.probe.duration
                    )
                    cap = None
                    if (d / "captions.json").exists():
                        from clipforge.captions.timeline import Timeline

                        cap = Timeline.model_validate_json((d / "captions.json").read_text())
                    fps = src.probe.video.fps if src.probe.video else 25.0
                    path, w = exportmod.export_cut(
                        clip.title,
                        Path(src.master_path),
                        edl,
                        fps,
                        req.format,
                        d / f"cut_{req.format}",
                        cap,
                        src.probe.duration,
                    )
                    warnings += w
                    files[name] = [path, d / "out.srt"]
            stamp = "export-" + re.sub(r"\W", "", str(len(list(export_dir.iterdir()))))
            dest = exportmod.bundle(
                files,
                export_dir / stamp,
                as_zip=req.format != "mp4_folder"
                and req.format in ("mp4_zip", "otio", "fcpxml", "fcp7", "edl"),
            )
            return {
                "path": str(dest),
                "name": dest.name,
                "warnings": warnings,
                "is_zip": dest.suffix == ".zip",
            }

        return await run_in_threadpool(work)

    @app.get("/api/exports/{name}")
    async def download_export(name: str) -> FileResponse:
        f = settings.data_dir.expanduser() / "exports" / Path(name).name
        if not f.is_file() or f.suffix != ".zip":
            raise HTTPException(404, "Not found")
        return FileResponse(f, filename=f.name, media_type="application/zip")

    # -- settings, storage, brand ----------------------------------------------------------------------
    @app.get("/api/settings")
    async def get_settings_route() -> dict[str, Any]:
        s = get_settings()
        return {
            "anthropic_api_key": settings_store.mask(
                s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
            ),
            "hf_token": settings_store.mask(s.hf_token.get_secret_value() if s.hf_token else None),
            "google_client_secret": settings_store.mask(
                s.google_client_secret.get_secret_value() if s.google_client_secret else None
            ),
            "values": {
                "CLIPFORGE_SCAN_MODEL": s.scan_model,
                "CLIPFORGE_CURATE_MODEL": s.curate_model,
                "CLIPFORGE_MAX_SCAN_MODEL": s.max_scan_model,
                "CLIPFORGE_MAX_CURATE_MODEL": s.max_curate_model,
                "MAX_JOB_COST_USD": str(s.max_job_cost_usd),
                "ASR_BACKEND": s.asr_backend,
                "ASR_MODEL": s.asr_model,
                "ASR_MODEL_NON_ENGLISH": s.asr_model_non_english,
                "MAX_SOURCE_HEIGHT": str(s.max_source_height),
                "YTDLP_COOKIES_FROM_BROWSER": s.ytdlp_cookies_from_browser or "",
                "RENDER_WORKERS": str(s.render_workers or ""),
                "GOOGLE_CLIENT_ID": s.google_client_id or "",
            },
            "data_dir": str(s.data_dir.expanduser()),
        }

    @app.put("/api/settings")
    async def put_settings(u: SettingsUpdate) -> dict[str, bool]:
        clean = {k: v for k, v in u.values.items() if k in settings_store.PUBLIC_KEYS}
        try:
            Settings.model_validate(
                {k: v for k, v in clean.items() if v != ""}
            )  # validate before writing
        except Exception as e:
            raise MediaError("One of those settings is not valid.", str(e)[:200]) from e
        settings_store.write_env({k: v for k, v in clean.items()})
        return {"ok": True}

    @app.put("/api/settings/secrets")
    async def put_secrets(k: ApiKey) -> dict[str, bool]:
        upd = {}
        if k.anthropic_api_key:
            upd["ANTHROPIC_API_KEY"] = k.anthropic_api_key.strip()
        if k.hf_token:
            upd["HF_TOKEN"] = k.hf_token.strip()
        if k.google_client_secret:
            upd["GOOGLE_CLIENT_SECRET"] = k.google_client_secret.strip()
        if upd:
            settings_store.write_env(upd)
        return {"ok": True}

    @app.get("/api/storage")
    async def storage_usage() -> dict[str, Any]:
        return await run_in_threadpool(storage.usage, projects_dir)

    @app.post("/api/projects/{project_id}/cleanup")
    async def cleanup(project_id: str) -> dict[str, int]:
        p = open_project(project_id)
        return {"freed_bytes": await run_in_threadpool(storage.cleanup, p.root)}

    @app.get("/api/templates")
    async def templates() -> list[dict[str, Any]]:
        return [
            {
                "id": t,
                "label": load_template(t).label,
                "description": load_template(t).description,
                "font": load_template(t).font.family,
            }
            for t in list_templates()
        ]

    @app.get("/api/brand")
    async def brand_list() -> list[dict[str, Any]]:
        return [brandmod.load_kit(k).model_dump() for k in brandmod.list_kits()]

    @app.put("/api/brand/{kit_id}")
    async def brand_put(kit_id: str, kit: brandmod.BrandKit) -> dict[str, bool]:
        if kit.id != kit_id or not re.fullmatch(r"[a-z0-9-]{1,32}", kit_id):
            raise MediaError(
                "Brand kit ids use lowercase letters, digits and dashes.", "Rename it."
            )
        brandmod.save_kit(kit)
        return {"ok": True}

    @app.post("/api/brand/{kit_id}/logo")
    async def brand_logo(kit_id: str, request: Request) -> dict[str, bool]:
        if not re.fullmatch(r"[a-z0-9-]{1,32}", kit_id):
            raise MediaError("Invalid brand kit id.", "Use lowercase letters, digits and dashes.")
        data = await request.body()
        if len(data) > 5_000_000 or not data.startswith(b"\x89PNG"):
            raise MediaError(
                "The logo must be a PNG under 5 MB.", "Export it with a transparent background."
            )
        d = brandmod.brand_dir() / kit_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "logo.png").write_bytes(data)
        return {"ok": True}
