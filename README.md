# Clipforge

Local-first AI clipping studio: one long video in, ready-to-post vertical shorts out.
Your video never leaves this machine. In the default (clips) mode, only transcript text and light
metadata go to the Anthropic API for the editorial step (Phase 2), and the UI says so. In
character-edit mode (see below), small still-frame thumbnails — not the video itself — go to the
API so it can tell whether the named character is on screen in each shot.

**Status: all six phases built.** See `docs/checkpoints/phase-6.md` for the final report (measured performance, quality, cost, gaps). See `docs/PLAN.md` and `docs/checkpoints/`.

## Setup

**Fastest:** double-click `install_and_run.command` (or run `./install_and_run.command`). It installs everything, asks for your keys, and opens the app.
Manual steps:

```
make setup      # installs uv, deno and ffmpeg-full (Homebrew), Python deps, web + captions deps, builds the UI
cp .env.example .env   # then add ANTHROPIC_API_KEY (needed from Phase 2)
make doctor     # checks hardware, ffmpeg, yt-dlp + JS runtime, keys, disk
make start      # UI + API on http://127.0.0.1:8765 (localhost only)
```

CLI: `clipforge run <url|path> --clips 8 --duration 30-60 --preset balanced --steer "..."`, `clipforge curate <project> --mode shorter`,
`clipforge render <project> --clip c001 --template karaoke-pop --brand acme [--renderer ass] [--fast] [--debug]`, `clipforge templates`, `clipforge brand create`, `clipforge eval core [--live]`, `clipforge doctor`.

## What works today

- Two equal inputs: paste a link (any yt-dlp site; YouTube is the primary target) or upload a file
  (chunked, resumable, streamed to disk; or import by path with no copy). Both become the same `Source`.
- ffprobe validation (rejects non-media, corrupt files, no audio), quality report, rotation/VFR/multi-audio
  handling, 16 kHz WAV and a 720p proxy.
- Word-level transcription (mlx-whisper on Apple Silicon, faster-whisper elsewhere), chunked with overlap,
  resumable, with hallucination guards (Whisper scores, VAD, silence, repetition, stock phrases).
- Signals (energy, speech rate, laughter/applause, shot changes, motion, platform heatmap) and two-stage LLM curation
  (cheap scan, stronger curator) with deterministic boundary snapping, a live cost meter and a hard cost cap.
  Clip score is labelled "heuristic", not a virality prediction.
- `clipforge render`: frame-accurate cuts through an EDL, speaker-aware reframing to 1080x1920 (single, two-shot,
  stacked, screen+cam, blurred fit), filler/pause cleanup, click-free audio at -14 LUFS, one H.264 encode, debug render.
- Designer captions: 8 templates, word-by-word highlight, hook overlay, brand kits (logo, intro/outro cards), rendered by Remotion
  (default) or libass (fast fallback), composited inside the single encode; SRT/VTT/ASS sidecars, thumbnail and `metadata.json`.
- Review UI (`#/p/<id>/c/<clip>`): video preview with the exact caption composition overlaid, transcript with click-to-seek, text-based
  cutting (select words, X), draggable trim, filler restore, per-segment layout override, live template switch, hook/title/hashtag editing,
  autosave. Keyboard: Space play, J/K/L, `[` `]` in/out, A/R approve/reject, X/U cut/restore, arrows step by word.
- Render queue with cancel and download; export as MP4 folder/zip, OpenTimelineIO, FCPXML, FCP7 XML or CMX 3600 EDL (cuts as edits on the source).
- Settings: keys (masked, written to `.env` mode 600), preferences, health check, storage cleanup, brand kits.
- Audio-only sources (podcasts, .mp3/.m4a/.wav/.ogg/.flac) render as an audiogram: designed canvas, title, audio-reactive spectrum, same captions and loudness.
- Publishing: upload to YouTube from the editor (OAuth, resumable, scheduling) or make a ready-to-upload package with copy for YouTube Shorts,
  Instagram Reels and TikTok.
- Hands-off clip selection: the best-ranked clip from every run renders and packages itself in the background (`AUTO_RENDER_TOP=off` to disable),
  so a TikTok-ready folder is usually waiting by the time you look at the results. See ADR-012.
- **Character-edit mode:** upload a full episode or movie, name a character, and get a short vertical
  edit of their scenes — no transcript, dialogue or ASR involved. See "Character edit mode" below and ADR-013.
- Live progress over SSE, cancel (kills the whole process tree) and resume.

## Character edit mode

Pick "Character edit" instead of "Clips" on the upload screen, name the character (reference images
optional but help), and Clipforge finds their scenes by vision instead of transcript: it splits the
video into shots (the same shot-detection signal the clips pipeline uses), sends small thumbnails of
each shot to Claude in batches asking whether that character is on screen, then cuts the matched
scenes into one montage with a colour grade and a per-shot push-in. No dialogue analysis, no ASR —
this mode skips straight from ingest to the scan. One source file and one assembled edit per project
for now; approve/reject and everything under "Publishing" below work the same as any other clip once
it's rendered.

## Publishing

YouTube: create a **Desktop app** OAuth client in Google Cloud Console (enable YouTube Data API v3), put the client id and secret in Settings,
then choose Connect YouTube in a clip's Publish panel. Limits, all enforced by Google: about 6 uploads per day on the default quota; API projects
that have not passed YouTube's compliance audit can only upload private videos; custom thumbnails need a verified channel. Scheduling keeps the
video private until the chosen time. Instagram and TikTok have no upload API usable by a local app (ADR-010), so use the package: every clip that
finishes rendering builds its package automatically, and the clip card's "Reveal in Finder" button gets it to your phone (AirDrop, cable, etc.)
in one click, caption already on the clipboard.

## Hardware notes

Target: Apple Silicon (M-series) with 16 GB. See `docs/decisions/ADR-001-asr-backend-and-model.md` for
measured ASR speeds. Default ASR model is `large-v3-turbo`; set `ASR_MODEL=large-v3` for maximum accuracy.

## Fonts and licences
Bundled fonts are SIL OFL 1.1 (`captions/fonts/LICENSES.md`); run `uv run python scripts/fetch_fonts.py` once. Remotion is free for individuals and
small teams; larger companies need a licence (`captions/README.md`).

## Costs

Curation is the only step that calls the API (transcript text only). Measured from the API `usage` fields: about $0.11 per hour of video in
Balanced mode (Haiku scan, Sonnet 5 curator), with a hard per-job cap (`MAX_JOB_COST_USD`, default $1). Everything else runs on your machine.

## Licences and terms

- Remotion (captions) is free for individuals and small companies; larger for-profit companies need
  a company licence. See `captions/README.md`.
- Models: Whisper (MIT), Silero VAD (MIT), pyannote community-1 (CC-BY-4.0, gated: accept the terms on Hugging Face).
- **Downloading:** only process videos you own or have permission to reuse. Downloading may violate a site's
  terms of service. Clipforge is a tool for your own content.

## Known gaps

- Speaker diarization needs your Hugging Face account to accept the pyannote terms; until then it degrades to
  single-speaker mode with a visible warning.
- YouTube upload is tested against a protocol-level mock, not Google's live servers (needs your OAuth client).
- Word-onset accuracy of +/-50 ms is not met (27%); mitigated by acoustic snapping and a caption lead (ADR-003).
- Active speaker is only verified on synthetic multi-person scenes; see `docs/checkpoints/phase-3.md`.
- No SQLite index yet: the filesystem manifests are the source of truth; the index arrives with clips in Phase 2.
- FCPXML cannot express NTSC frame rates in the bundled adapter: exports fall back to an integer rate and say so.
- The video preview is a caption-free proxy from the last render; after edits the captions update live, the picture after a re-render.
- Live-chat replay download is not implemented yet (the flag is harvested; rate extraction is Phase 2).
