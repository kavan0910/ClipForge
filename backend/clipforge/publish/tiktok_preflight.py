"""Local, advisory checks for a rendered clip before TikTok upload."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

from clipforge.media import probe


def build_report(
    duration: float,
    width: int,
    height: int,
    fps: float,
    audio_present: bool,
    still_frame_ratio: float | None,
    qr_visible: bool | None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str, action: str = "") -> None:
        checks.append({"name": name, "status": status, "detail": detail, "action": action})

    if duration < 5:
        add(
            "Clip duration",
            "review",
            f"Clip is {duration:.1f} seconds; very brief clips may lack context.",
            "Review whether the clip tells a complete, useful moment.",
        )
    else:
        add("Clip duration", "ok", f"Clip is {duration:.1f} seconds.")

    ratio = width / height if height else 0
    if 0.52 <= ratio <= 0.60 and height >= 720:
        add("Portrait format", "ok", f"Rendered at {width}x{height}.")
    else:
        add(
            "Portrait format",
            "review",
            f"Rendered at {width}x{height}; this is not a typical 9:16 portrait frame.",
            "Use the editor's portrait layout and review the crop before rendering again.",
        )

    if 24 <= fps <= 60:
        add("Frame rate", "ok", f"Rendered at {fps:g} fps.")
    else:
        add(
            "Frame rate",
            "review",
            f"Rendered at {fps:g} fps.",
            "Check for judder or unusually low frame rate in the preview.",
        )

    if audio_present:
        add("Audio track", "ok", "An audio track is present.")
    else:
        add(
            "Audio track",
            "review",
            "No audio track was detected.",
            "Check whether the clip needs original narration, dialogue, or sound.",
        )

    if still_frame_ratio is None:
        add(
            "Visual movement",
            "review",
            "Could not inspect sampled frames.",
            "Review the clip visually for long still or repeated sections.",
        )
    elif still_frame_ratio >= 0.8:
        add(
            "Visual movement",
            "review",
            "Most sampled frame pairs look nearly identical.",
            "Review for long still-image sections; use meaningful motion or original footage where appropriate.",
        )
    else:
        add("Visual movement", "ok", "Sampled frames show visual change.")

    if qr_visible is True:
        add(
            "QR code",
            "review",
            "A QR code was detected in sampled frames.",
            "Check whether it is necessary to the story; avoid making the video primarily a QR-code display.",
        )
    elif qr_visible is False:
        add(
            "QR code",
            "ok",
            "No QR code was detected in sampled frames; small or brief codes may be missed.",
        )
    else:
        add("QR code", "review", "QR-code scan was unavailable; inspect the video manually.")

    add(
        "Originality and rights",
        "review",
        "File analysis cannot establish authorship, permission, or whether edits add meaningful creative value.",
        "Use content you own or have permission to use. Add your own perspective or storytelling; a crop, resize, or caption alone may not be enough. Inspect for third-party watermarks.",
    )

    return {
        "checks": checks,
        "review_count": sum(check["status"] == "review" for check in checks),
        "notice": "Advisory only. This does not predict or guarantee TikTok moderation or recommendation eligibility.",
    }


def inspect_video(path: Path) -> dict[str, Any]:
    """Probe media facts and inspect sparse frame samples without modifying the file."""
    media = probe(path)
    if media.video is None:
        raise ValueError("TikTok preflight requires a video track.")

    still_frame_ratio, qr_visible = _visual_signals(path, media.duration)
    return build_report(
        media.duration,
        media.video.width,
        media.video.height,
        media.video.fps,
        bool(media.audio),
        still_frame_ratio,
        qr_visible,
    )


def _visual_signals(path: Path, duration: float) -> tuple[float | None, bool | None]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        return None, None

    detector = cv2.QRCodeDetector()
    samples: list[Any] = []
    qr_visible = False
    try:
        for index in range(12):
            cap.set(cv2.CAP_PROP_POS_MSEC, duration * index / 11 * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            samples.append(cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA))
            if not qr_visible:
                qr_visible = bool(detector.detectAndDecode(frame)[0])
    except cv2.error:
        return None, None
    finally:
        cap.release()

    if len(samples) < 2:
        return None, qr_visible
    near_still = sum(float(cv2.absdiff(a, b).mean()) < 1.5 for a, b in pairwise(samples))
    return near_still / (len(samples) - 1), qr_visible
