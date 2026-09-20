"""'Ready to upload' package: the video, thumbnail and per-platform copy in one folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from clipforge.edits import ClipEdits

LIMITS = {
    "youtube_shorts": {"title": 100, "description": 5000, "max_seconds": 180, "note": "Vertical and up to 3 minutes is a Short."},
    "instagram_reels": {"caption": 2200, "hashtags": 30, "max_seconds": 180, "note": "Reels up to 3 minutes; 5 hashtags or fewer reads best."},
    "tiktok": {"caption": 2200, "max_seconds": 600, "note": "Add hashtags in the caption; the cover is chosen in the app."},
}  # fmt: skip


def _tags(hashtags: list[str], n: int) -> str:
    return " ".join(f"#{h.lstrip('#')}" for h in hashtags[:n])


def build_package(
    clip_dir: Path, dest: Path, title: str, hook: str, description: str, hashtags: list[str]
) -> Path:
    """Copy out.mp4, thumb.jpg and the caption sidecars, and write copy for each platform."""
    out = clip_dir / "out.mp4"
    if not out.exists():
        raise FileNotFoundError("Render the clip first.")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("out.mp4", "thumb.jpg", "out.srt", "out.vtt"):
        if (clip_dir / name).exists():
            shutil.copy2(clip_dir / name, dest / name)
    copy = {
        "youtube_shorts": {"title": title[:100], "description": f"{description}\n\n{_tags(hashtags, 3)} #Shorts".strip()},
        "instagram_reels": {"caption": f"{hook}\n\n{description}\n\n{_tags(hashtags, 5)}".strip()[:2200]},
        "tiktok": {"caption": f"{hook} {_tags(hashtags, 5)}".strip()[:2200]},
    }  # fmt: skip
    (dest / "copy.json").write_text(
        json.dumps({"copy": copy, "limits": LIMITS}, indent=1, ensure_ascii=False)
    )
    (dest / "README.txt").write_text(
        "Ready to upload\n===============\n"
        "1. YouTube Shorts: upload out.mp4, paste the title and description from copy.json (or publish from Clipforge).\n"
        "2. Instagram Reels: open Instagram > Create > Reel, choose out.mp4, paste the caption, pick thumb.jpg as the cover.\n"
        "3. TikTok: upload out.mp4, paste the caption. out.srt is a caption file for platforms that accept one.\n"
        "The video already has burned-in captions and is 1080x1920 H.264 at about -14 LUFS.\n"
    )
    return dest


def package_from_edits(
    clip_dir: Path,
    dest: Path,
    edits: ClipEdits,
    clip_title: str,
    clip_hook: str,
    clip_desc: str,
    clip_tags: list[str],
) -> Path:
    return build_package(
        clip_dir,
        dest,
        edits.title or clip_title,
        edits.hook or clip_hook,
        edits.description or clip_desc,
        edits.hashtags or clip_tags,
    )
