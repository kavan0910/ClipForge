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


class AudioTrackChoice(ClipforgeError):
    """The file has several audio tracks and the user has not said which to use."""

    code = "choose_audio_track"

    def __init__(self, message: str, action: str, tracks: list[dict]) -> None:
        super().__init__(message, action)
        self.tracks = tracks

    def to_dict(self) -> dict:  # type: ignore[override]
        return {**super().to_dict(), "tracks": self.tracks}
