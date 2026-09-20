# Phase 5 checkpoint: review UI, render queue, export, settings

**Status: built and verified end to end on a real clip (no API spend).**

## Acceptance
| Requirement | Result |
|---|---|
| Keyboard-only flow link/file -> approve -> render -> export | Playwright `editor.spec.ts` drives a curated real project with keyboard only: open editor, select a word, cut it (X), approve (A), render, export EDL. Passed with a real render in 51 s. Link and upload flows are covered by `flows.spec.ts` (all pass) |
| Text-based trimming | Selecting words and cutting them changes the EDL server-side (duration shown drops; verified in the e2e test); cuts land in pauses around words |
| Live template switch with export parity | Editor uses the same Remotion composition as the export; parity test 0.0000 mismatch over 31k-69k caption pixels |
| Export formats | MP4 folder/zip, OTIO, FCPXML, FCP7 XML, CMX EDL; OTIO round-trip check on export; unit tests |
| Settings | Keys masked and written to `.env` (mode 600), preferences, doctor, storage cleanup and delete, brand kits with logo |

## Tests
227 backend, 6 web unit, 7 Playwright. Lint, pyright and typecheck clean (one oxlint warning: setState in an effect for the caption timeline fetch).
Fixed on the way: FCPXML NTSC rate fallback, a race where the render button did not show progress until the job wrote its first event,
and an EDL property test that hypothesis broke at the shared boundary of two abutting kept ranges (test now models that ambiguity).

## Known gaps
- Preview video is the last render's caption-free proxy; picture edits (layout, trim) show after re-render.
- No drag handles on a timeline; trim uses range sliders plus `[`/`]`.
- No visual regression on the editor layout; accessibility checked by role/label queries only.
- All timings are M1 measurements.
