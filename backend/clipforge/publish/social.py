"""Instagram Reels and TikTok publishers: documented stubs.

Why these are not implemented (verified against the platforms' developer docs at build time):
- Instagram: the Graph API content-publishing endpoint needs an Instagram *professional* account linked to a
  Facebook Page, a Meta app with `instagram_content_publish` (App Review for other people's accounts) and the
  video at a public HTTPS URL that Meta's servers fetch. A local-first tool has no public URL by design.
- TikTok: the Content Posting API requires an app audit; posts from unaudited clients are forced to private.

Use the 'ready to upload' package (publish/package.py) for both. The interface below is what a real
implementation would fill in, so the UI and CLI need no change when one arrives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from clipforge.errors import ClipforgeError


class Publisher(Protocol):
    name: str

    def publish(self, video: Path, title: str, description: str, schedule: str | None) -> str: ...


class NotAvailable(ClipforgeError):
    code = "publisher_not_available"


class _Stub:
    name = ""
    reason = ""

    def publish(
        self, video: Path, title: str, description: str, schedule: str | None = None
    ) -> str:
        raise NotAvailable(
            f"{self.name} publishing is not available from a local tool.", self.reason
        )


class InstagramPublisher(_Stub):
    name = "Instagram"
    reason = "Use the ready-to-upload package: it needs a public video URL and a Meta app review. See docs/decisions/ADR-010."


class TikTokPublisher(_Stub):
    name = "TikTok"
    reason = "Use the ready-to-upload package: unaudited TikTok apps can only post private videos. See docs/decisions/ADR-010."
