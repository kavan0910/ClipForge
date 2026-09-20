"""Disk usage per project and cleanup of regenerable intermediates."""

from __future__ import annotations

import shutil
from pathlib import Path

INTERMEDIATE_FILES = ("audio_raw.wav", "audio_norm.wav", "audio_final.wav", "debug.mp4")
INTERMEDIATE_DIRS = ("thumb_candidates", "layer_captions", "layer_hook")


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file() and not f.is_symlink():  # a symlinked master is not this project's storage
            total += f.stat().st_size
    return total


def usage(projects_dir: Path) -> dict:
    rows = []
    for d in sorted(projects_dir.iterdir()) if projects_dir.exists() else []:
        if (d / "project.json").exists():
            rows.append(
                {"id": d.name, "bytes": dir_size(d), "intermediate_bytes": intermediate_size(d)}
            )
    return {
        "projects": rows,
        "total_bytes": sum(r["bytes"] for r in rows),
        "free_bytes": shutil.disk_usage(
            projects_dir if projects_dir.exists() else Path.home()
        ).free,
    }


def intermediate_size(project_dir: Path) -> int:
    total = 0
    for clip in (project_dir / "clips").glob("c*") if (project_dir / "clips").exists() else []:
        for n in INTERMEDIATE_FILES:
            f = clip / n
            total += f.stat().st_size if f.exists() else 0
        for n in INTERMEDIATE_DIRS:
            if (clip / n).is_dir():
                total += dir_size(clip / n)
    chunks = project_dir / "transcript" / "chunks"
    return total + (dir_size(chunks) if chunks.exists() else 0)


def cleanup(project_dir: Path) -> int:
    """Delete regenerable intermediates (working audio, caption stills, thumbnail candidates). Returns bytes freed."""
    freed = intermediate_size(project_dir)
    for clip in (project_dir / "clips").glob("c*") if (project_dir / "clips").exists() else []:
        for n in INTERMEDIATE_FILES:
            (clip / n).unlink(missing_ok=True)
        for n in INTERMEDIATE_DIRS:
            shutil.rmtree(clip / n, ignore_errors=True)
    shutil.rmtree(project_dir / "transcript" / "chunks", ignore_errors=True)
    return freed
