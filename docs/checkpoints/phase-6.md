# Phase 6 checkpoint and final report

**Status: built. YouTube upload is verified against a protocol-level mock only (needs the user's Google OAuth client for a live run).**

## What was built
- YouTube publisher (OAuth PKCE, resumable upload with resume, scheduling, thumbnail), API routes, Publish panel in the editor, key fields in Settings. ADR-010.
- Ready-to-upload package: video, thumbnail, SRT/VTT, copy and limits for YouTube Shorts, Instagram Reels, TikTok. Instagram/TikTok publishers are documented stubs.
- Audiogram layout for audio-only sources: gradient canvas, title, 44-band audio-reactive spectrum, same captions/EDL/loudness/single encode.
  Verified on a real 5-minute audio-only project (.m4a): 1080x1920 H.264/AAC, -14.2 LUFS, -3.0 dBTP, captions and hook legible. 26.4 s clip rendered in 37.7 s.
- More audio formats accepted (.ogg .opus .flac .aac).

## Final report

### Performance (all measured on an M1, 16 GB; the target machine is an M5, expect faster)
| Item | Measured |
|---|---|
| Transcription (ADR-001) | mlx-whisper large-v3-turbo, see ADR-001 (well above real time) |
| Render, video clip | 1.47 s per clip-second with libx264 slow CRF 17 (about 88 s for a 60 s clip); 27.8 s clip: 46 s with Remotion captions, 40 s with libass |
| Render, audiogram | 1.4 s per clip-second including captions |
| Decode + compose | 119 fps (cv2.resize Lanczos; warpAffine was 6x slower) |
| Editor render via UI (keyboard e2e) | 51 s for one real clip |
| Curation cost | $0.063 for a 33.7 min video (about $0.11 per hour) |
| Test suites | 236 backend, 6 web unit, 7 Playwright |

### Quality (14 real clips, Phase 3/4)
0 mid-word cuts; A/V drift <= 0.5 frame; face in crop >= 98.5%; eye line in band 98.8%; -14.0 to -14.3 LUFS; true peak <= -2.4 dBTP;
captions 2 lines max and inside safe zones on every template; in-app preview equals export (0.0000 pixel mismatch). Word onsets within +/-50 ms: **not met** (27%, ADR-003).

### LLM spend for all fixture runs and tests (from recorded usage)
Eval suite prompt v1 $0.288 + prompt v2 $0.098 + live two-stage run $0.063 + live API tests about $0.01 + audio-only runs $0.02 = **about $0.48 in total**.

### Prioritised improvements
1. Live-test the YouTube uploader with a real OAuth client (needs the user), then apply for the audit so public uploads stick.
2. Word-onset accuracy: a wav2vec2-CTC aligner tuned per language, or a onset-aware post-filter (ADR-003).
3. Verify active-speaker detection on real multi-speaker footage and add a facecam fixture.
4. Speech enhancement (noise reduction) for poor audio; deferred upscaler (ADR-008).
5. Timeline drag handles and an in-editor re-preview after layout or trim edits (today the picture updates after re-render).
6. Emoji from the LLM instead of a keyword table; 3-hour source stress run; SQLite index; diarization speed on CPU.
7. Audiogram preview in the editor (audio-only clips have no caption-free proxy yet, so the editor shows a "render to preview" state).

### Known gaps carried forward
Active speaker unverified on real multi-speaker footage; no real facecam fixture; no speech enhancement; AI upscaler deferred; 3-hour source not run;
"open eyes" is a heuristic; recall labels are my own and subjective; all performance numbers are M1.
