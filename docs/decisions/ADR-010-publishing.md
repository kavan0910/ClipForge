# ADR-010: Publishing (YouTube API; Instagram and TikTok via a package)

**Status:** accepted.

## Decision
- **YouTube:** implemented with the Data API v3: OAuth 2.0 for installed apps (loopback redirect on 127.0.0.1, PKCE, offline access),
  resumable upload in 8 MiB chunks with offset recovery, `publishAt` scheduling (forces `private` until then), and `thumbnails.set`.
  The user supplies their own Google OAuth client (id in `.env`, secret in `.env` mode 600). The refresh token is stored 0600 under the data dir.
- **Instagram Reels and TikTok:** a documented stub (`publish/social.py`) plus a **ready-to-upload package** (video, thumbnail, SRT/VTT,
  per-platform copy and limits).

## Why
- Instagram's content-publishing API needs a professional account, a Meta app with App Review, and a public HTTPS URL that Meta fetches.
  A local-first tool has no public URL by design and cannot ship an approved Meta app on the user's behalf.
- TikTok's Content Posting API requires an app audit; unaudited clients can only post private videos.
- YouTube is the one platform where a personal OAuth client works end to end, with these documented limits, all surfaced in the UI:
  default quota 10,000 units/day at 1,600 per upload (about 6 uploads a day); API projects that have not passed YouTube's audit get
  uploads locked to private; custom thumbnails need a verified channel.

## Verification status
The upload protocol (init, chunk, 308 resume, transient 5xx recovery, scheduling, quota errors, token refresh, PKCE URL) is tested against
a mock transport that asserts headers and byte ranges. **It has not been exercised against Google's live servers**: that needs the user's OAuth client.
