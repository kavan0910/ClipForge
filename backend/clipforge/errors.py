"""User-facing errors. Every error says what happened and what to do about it."""

from __future__ import annotations


class ClipforgeError(Exception):
    code = "error"

    def __init__(self, message: str, action: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.action = action

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "action": self.action}


class MediaError(ClipforgeError):
    code = "media"


class DownloadError(ClipforgeError):
    code = "download"


class DiskSpaceError(ClipforgeError):
    code = "disk_space"


class ASRError(ClipforgeError):
    code = "asr"
