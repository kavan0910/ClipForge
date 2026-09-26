# ADR-012: Auto-render the top clip and auto-package every render, for a manual TikTok post

**Status:** accepted.

## Context
The user asked for upload -> clip -> approve -> auto-post to TikTok. TikTok's Content Posting API needs
a developer app audit; until it passes, direct-posted videos are forced to `SELF_ONLY` (private, visible
only to the poster) (ADR-010). Given the choice between building that half-automated official-API path now,
waiting for the audit first, or keeping TikTok manual, the user chose to keep it manual rather than take on
either audit path. TikTok/Instagram publishing stays the documented stub in `publish/social.py`.

## Decision
Automate everything up to the point that requires the user's own TikTok account, and make the remaining
manual step (get the file onto the phone and tap post) as close to one click as the rest of the app:

- **Auto-render the top clip:** right after clip selection (fresh curate or any re-curate) finishes, the
  single best-ranked clip that is not rejected and not already rendered starts rendering in the background
  with the editor's default options (`stages._auto_render_top`, gated by `AUTO_RENDER_TOP`, default on).
  It runs as an ordinary render job (same `renderjob` subprocess the "Render clip" button starts), so it
  shows up in the render queue and can be cancelled like any other render.
- **Auto-package every render:** right after any render finishes (auto or manual), `renderjob._auto_package`
  builds the existing "ready to upload" package (`publish/package.py`) unconditionally — it costs a file copy
  and a JSON write, nothing worth gating.
- **Surface it in the UI:** `GET /api/projects/{id}/clips` now reports `rendered` and `package_ready` per
  clip. The clip card shows a "Ready to post" banner once `package_ready` is true, with a "Copy TikTok
  caption" button and a "Reveal in Finder" button (`POST /api/reveal`, path-confined to the data dir, macOS
  `open -R` — a no-op on other platforms).

Both hooks are best-effort: a failure is logged and swallowed, never surfaces as a job or render error, since
the user's actual selection or render outcome must never be blocked by a convenience step.

## Why not build the TikTok API path now
Recorded here so the tradeoff is not re-litigated without new information: the official Content Posting API
would let this go fully automatic once TikTok audits the app, but until then every post lands private and the
user must still open the TikTok app to make it public — not meaningfully less manual than today's package, for
a meaningfully larger surface (OAuth, token storage, TikTok's own upload protocol). Revisit if the user gets
their developer app audited, or decides the private-post-then-flip-public flow is worth it before that.

## Verification
`backend/tests/test_auto_publish.py`: unit tests over `_auto_render_top` (picks the highest `rank_score`,
skips a rejected or already-rendered top clip, never raises if starting the render fails) and
`_auto_package` (writes the package including TikTok's caption, never raises if the render output is
missing). Full backend suite (254 tests) and frontend typecheck/lint pass.
