"""YouTube publisher: OAuth 2.0 (installed app, loopback + PKCE), resumable upload, scheduling, thumbnail.

API facts this relies on (YouTube Data API v3 docs):
- videos.insert with uploadType=resumable: POST metadata -> `Location` header is the session URI; PUT chunks
  (multiples of 256 KiB) with Content-Range; 308 + Range means "send the rest", 200/201 is the finished video;
  after an interruption, PUT an empty body with `Content-Range: bytes */<total>` to learn the offset.
- Scheduling: privacyStatus must be "private" with status.publishAt (RFC 3339, in the future).
- videos.insert costs 1600 quota units of the default 10,000 per day (about 6 uploads a day).
- Uploads from API projects that have not passed YouTube's compliance audit are locked to private.
The refresh token is stored owner-only (0600) under the data dir; the client secret lives in .env.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from clipforge.errors import ClipforgeError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB
RETRIES = 5


class PublishError(ClipforgeError):
    code = "publish"


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def clean_title(title: str) -> str:
    """YouTube rejects < and > in titles and caps them at 100 characters."""
    return re.sub(r"[<>]", "", title).strip()[:100] or "Untitled"


def clean_tags(tags: list[str]) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = re.sub(r"[<>#,]", "", t).strip()
        if t and total + len(t) + 1 <= 480:
            out.append(t)
            total += len(t) + 1
    return out


def parse_schedule(when: str | None, now: datetime | None = None) -> str | None:
    """ISO-8601 (naive = local time) -> RFC 3339 UTC, and it must be in the future."""
    if not when:
        return None
    dt = datetime.fromisoformat(when)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    dt = dt.astimezone(UTC)
    if dt <= (now or datetime.now(UTC)):
        raise PublishError("The scheduled time is in the past.", "Pick a time in the future.")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class YouTubeAuth:
    def __init__(
        self, client_id: str, client_secret: str, token_path: Path, http: httpx.Client | None = None
    ) -> None:
        self.client_id, self.client_secret, self.token_path = client_id, client_secret, token_path
        self.http = http or httpx.Client(timeout=30)

    def authorization_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        q = {"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
             "access_type": "offline", "prompt": "consent", "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}  # fmt: skip
        return f"{AUTH_URL}?{urlencode(q)}"

    def _save(self, tok: dict) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(tok))
        self.token_path.chmod(0o600)

    def _load(self) -> dict | None:
        return json.loads(self.token_path.read_text()) if self.token_path.exists() else None

    def _token_request(self, data: dict) -> dict:
        r = self.http.post(
            TOKEN_URL,
            data={"client_id": self.client_id, "client_secret": self.client_secret, **data},
        )
        if r.status_code != 200:
            raise PublishError(
                "Google rejected the sign-in.",
                _err(r) + " Check the OAuth client id and secret in Settings.",
            )
        return r.json()

    def exchange(self, code: str, verifier: str, redirect_uri: str) -> None:
        t = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        )
        t["expires_at"] = time.time() + int(t.get("expires_in", 3600)) - 60
        self._save(t)

    def connected(self) -> bool:
        t = self._load()
        return bool(t and t.get("refresh_token"))

    def access_token(self) -> str:
        t = self._load()
        if not t or not t.get("refresh_token"):
            raise PublishError("YouTube is not connected.", "Choose Connect YouTube in Settings.")
        if t.get("expires_at", 0) <= time.time():
            fresh = self._token_request(
                {"grant_type": "refresh_token", "refresh_token": t["refresh_token"]}
            )
            t.update(
                access_token=fresh["access_token"],
                expires_at=time.time() + int(fresh.get("expires_in", 3600)) - 60,
            )
            self._save(t)
        return t["access_token"]

    def disconnect(self) -> None:
        self.token_path.unlink(missing_ok=True)

    def login_interactive(self, timeout: float = 180.0, open_browser: bool = True) -> None:
        """Loopback redirect on 127.0.0.1 (never a public address); the state and PKCE verifier are checked."""
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(16)
        got: dict[str, str] = {}

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                if q.get("state") == state and "code" in q:
                    got["code"] = q["code"]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<p>Clipforge is connected to YouTube. You can close this tab.</p>"
                )

            def log_message(self, format: str, *args: object) -> None:
                return

        srv = HTTPServer(("127.0.0.1", 0), H)
        srv.timeout = 2
        redirect = f"http://127.0.0.1:{srv.server_port}/"
        url = self.authorization_url(redirect, state, challenge)
        if open_browser:
            webbrowser.open(url)
        end = time.time() + timeout
        while "code" not in got and time.time() < end:
            srv.handle_request()
        srv.server_close()
        if "code" not in got:
            raise PublishError(
                "Sign-in was not completed.",
                f"Open this link and approve access, then try again: {url}",
            )
        self.exchange(got["code"], verifier, redirect)


def _err(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        if isinstance(e, dict):
            reasons = ",".join(x.get("reason", "") for x in e.get("errors", []))
            return (
                f"{e.get('message', r.text[:200])} ({reasons})"
                if reasons
                else str(e.get("message", r.text[:200]))
            )
        return str(e)
    except ValueError:
        return r.text[:200]


class YouTubePublisher:
    name = "YouTube"

    def __init__(
        self,
        auth: YouTubeAuth,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.auth, self.http, self.sleep = (
            auth,
            http or httpx.Client(timeout=httpx.Timeout(60, read=300)),
            sleep,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.auth.access_token()}"}

    def _explain(self, r: httpx.Response) -> PublishError:
        reason = _err(r)
        if r.status_code == 403 and "quota" in reason.lower():
            return PublishError(
                "YouTube's daily upload quota is used up.",
                "The default quota allows about 6 uploads a day. Try again tomorrow (Pacific time).",
            )
        if r.status_code in (401, 403) and "uploadLimit" in reason:
            return PublishError(
                "This channel reached its upload limit.", "Wait a while or verify the channel."
            )
        if r.status_code == 401:
            return PublishError(
                "YouTube rejected the saved sign-in.", "Choose Connect YouTube in Settings again."
            )
        return PublishError(f"YouTube returned an error ({r.status_code}).", reason)

    def publish(
        self, video: Path, title: str, description: str, tags: list[str] | None = None, schedule: str | None = None,
        thumbnail: Path | None = None, privacy: str = "private", on_progress: Callable[[float], None] | None = None,
    ) -> dict:  # fmt: skip
        publish_at = parse_schedule(schedule)
        status = {
            "privacyStatus": "private" if publish_at else privacy,
            "selfDeclaredMadeForKids": False,
        }
        if publish_at:
            status["publishAt"] = publish_at
        body = {"snippet": {"title": clean_title(title), "description": description[:4900], "tags": clean_tags(tags or []), "categoryId": "22"},
                "status": status}  # fmt: skip
        size = video.stat().st_size
        init = self.http.post(
            UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
            headers={**self._headers(), "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}, json=body,
        )  # fmt: skip
        if init.status_code != 200 or "location" not in init.headers:
            raise self._explain(init)
        session = init.headers["location"]
        result = self._upload(session, video, size, on_progress)
        vid = result["id"]
        warnings: list[str] = []
        if thumbnail and thumbnail.exists():
            t = self.http.post(
                THUMB_URL,
                params={"videoId": vid},
                headers={**self._headers(), "Content-Type": "image/jpeg"},
                content=thumbnail.read_bytes(),
            )
            if (
                t.status_code != 200
            ):  # custom thumbnails need a verified channel; the video itself is fine
                warnings.append(f"The thumbnail was not set: {_err(t)}")
        if (
            result.get("status", {}).get("privacyStatus") == "private"
            and not publish_at
            and privacy != "private"
        ):
            warnings.append(
                "YouTube kept the video private. API projects that have not passed YouTube's audit can only upload private videos."
            )
        return {
            "id": vid,
            "url": f"https://youtu.be/{vid}",
            "scheduled_for": publish_at,
            "warnings": warnings,
        }

    def _upload(
        self, session: str, video: Path, size: int, on_progress: Callable[[float], None] | None
    ) -> dict:
        offset, fails = 0, 0
        with video.open("rb") as f:
            while True:
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                try:
                    r = self.http.put(
                        session,
                        content=chunk,
                        headers={
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                            "Content-Type": "video/mp4",
                        },
                    )
                except httpx.TransportError:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    if on_progress:
                        on_progress(1.0)
                    return r.json()
                if r is not None and r.status_code == 308:
                    rng = r.headers.get("range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    fails = 0
                    if on_progress:
                        on_progress(offset / size)
                    continue
                if r is not None and r.status_code < 500 and r.status_code != 429:
                    raise self._explain(r)
                fails += 1
                if fails > RETRIES:
                    raise PublishError(
                        "The upload kept failing.",
                        "Check your connection and try again; it resumes where it stopped.",
                    )
                self.sleep(min(2**fails, 30))
                offset = self._resume_offset(session, size)

    def _resume_offset(self, session: str, size: int) -> int:
        try:
            r = self.http.put(session, content=b"", headers={"Content-Range": f"bytes */{size}"})
        except httpx.TransportError:
            return 0
        if r.status_code == 308:
            rng = r.headers.get("range")
            return int(rng.split("-")[1]) + 1 if rng else 0
        return 0
