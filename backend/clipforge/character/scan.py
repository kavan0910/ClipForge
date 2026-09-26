"""Character scan: find where a named character appears in raw footage.

One thumbnail per shot (shot boundaries come from the existing scene-cut signal, PySceneDetect on
the 720p proxy) is batched into vision calls, mirroring the transcript pipeline's cheap-scan-then-
select shape but over frames instead of sentences. No transcript or ASR is involved.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from itertools import pairwise
from pathlib import Path

from clipforge.character.schema import Scene, ShotBatchResult
from clipforge.llm.client import Block, LLMClient

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
BATCH_SIZE = 20
MIN_SHOT = 0.25  # a shot shorter than this (usually a false cut) is merged into its neighbour
MIN_CONFIDENCE = 55  # a match below this is dropped as too uncertain to include in the edit
MERGE_GAP = 0.5  # adjacent matched shots within this many seconds of each other become one scene
THUMB_WIDTH = 320


def load_prompt() -> str:
    return (PROMPTS_DIR / "character" / "v1" / "scan.md").read_text().strip()


def shots_from_cuts(cuts: Sequence[float], duration: float) -> list[tuple[float, float]]:
    """Shot ranges from sorted scene-cut timestamps, merging any shot shorter than MIN_SHOT into
    its neighbour so one stray detection can't turn into an unusably short candidate."""
    if duration <= 0:
        return []
    bounds = [0.0, *sorted(t for t in cuts if 0 < t < duration), duration]
    shots: list[tuple[float, float]] = []
    for a, b in pairwise(bounds):
        if shots and b - a < MIN_SHOT:
            shots[-1] = (shots[-1][0], b)
        else:
            shots.append((a, b))
    return shots


def grab_thumb(proxy: Path, ts: float, out: Path, width: int = THUMB_WIDTH) -> bytes:
    """One JPEG frame at `ts`, small and cheap to send to the API. Empty bytes if ffmpeg can't
    read that timestamp (near the very end of a truncated proxy, for instance)."""
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(proxy), "-frames:v", "1",
            "-vf", f"scale={width}:-2", "-q:v", "5", "-y", str(out)]  # fmt: skip
    subprocess.run(argv, check=False, capture_output=True)
    return out.read_bytes() if out.exists() else b""


def _batches(items: Sequence[tuple[float, float]], n: int) -> list[Sequence[tuple[float, float]]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def merge_adjacent(scenes: list[Scene], gap: float = MERGE_GAP) -> list[Scene]:
    if not scenes:
        return []
    ordered = sorted(scenes, key=lambda s: s.start)
    merged = [ordered[0]]
    for s in ordered[1:]:
        last = merged[-1]
        if s.start - last.end <= gap:
            merged[-1] = Scene(
                start=last.start,
                end=max(last.end, s.end),
                confidence=max(last.confidence, s.confidence),
            )
        else:
            merged.append(s)
    return merged


def select_scenes(
    scenes: list[Scene], target_duration: float, min_scene: float = 0.6, max_scene: float = 6.0
) -> list[Scene]:
    """Trim each scene to a sane on-screen length, then keep the most confident ones up to the
    target total, presented back in chronological (story) order."""
    trimmed = [
        Scene(start=s.start, end=min(s.end, s.start + max_scene), confidence=s.confidence)
        for s in scenes
        if s.end - s.start >= min_scene
    ]
    trimmed.sort(key=lambda s: -s.confidence)
    picked: list[Scene] = []
    total = 0.0
    for s in trimmed:
        if picked and total >= target_duration:
            break
        picked.append(s)
        total += s.duration
    return sorted(picked, key=lambda s: s.start)


def scan_character(
    proxy: Path,
    duration: float,
    cuts: Sequence[float],
    character: str,
    thumbs_dir: Path,
    llm: LLMClient,
    model: str,
    reference_images: list[bytes] | None = None,
    on_progress: Callable[..., None] | None = None,
) -> list[Scene]:
    """Batch every shot's mid-frame through vision calls; return matched (not yet merged/trimmed)
    scenes with the model's per-shot confidence."""
    shots = shots_from_cuts(cuts, duration)
    if not shots:
        return []
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    system = load_prompt()
    ref_blocks = [Block(image=img) for img in (reference_images or [])]
    progress = on_progress or (lambda **_: None)
    scenes: list[Scene] = []
    batches = _batches(shots, BATCH_SIZE)
    for bi, batch in enumerate(batches):
        blocks = [
            Block(
                text=(
                    f"Character: {character}\n{len(ref_blocks)} reference image(s) of them follow."
                    if ref_blocks
                    else f"Character: {character}. No reference images were given; use your own "
                    "knowledge of them if you recognise the name, otherwise mark every frame not present."
                )
            ),
            *ref_blocks,
            Block(text=f"Now {len(batch)} numbered frames from the video, in order:"),
        ]
        readable = [False] * len(batch)
        for i, (s, e) in enumerate(batch):
            data = grab_thumb(proxy, (s + e) / 2, thumbs_dir / f"shot_{bi}_{i}.jpg")
            if not data:
                blocks.append(Block(text=f"Frame {i}: (could not be read; treat as not present)"))
                continue
            blocks.append(Block(text=f"Frame {i} (t={s:.1f}-{e:.1f}s):"))
            blocks.append(Block(image=data))
            readable[i] = True
        result = llm.structured(
            stage="character_scan", model=model, system=system, blocks=blocks,
            model_cls=ShotBatchResult, tool_name="report_matches", max_tokens=2000,
        )  # fmt: skip
        for m in result.matches:
            # A frame that failed to grab is never a match, regardless of what the model says: it
            # was never actually shown that image, so trusting a "not present" hint would be blind faith.
            if not (0 <= m.index < len(batch)) or not readable[m.index]:
                continue
            if m.present and m.confidence >= MIN_CONFIDENCE:
                s, e = batch[m.index]
                scenes.append(Scene(start=s, end=e, confidence=m.confidence / 100))
        progress(
            pct=min(1.0, (bi + 1) / len(batches)),
            note=f"scanned {min((bi + 1) * BATCH_SIZE, len(shots))}/{len(shots)} shots, "
            f"{len(scenes)} matched so far",
        )
    return merge_adjacent(scenes)
