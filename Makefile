.PHONY: setup dev start test slow e2e lint fmt update-ytdlp doctor fixtures bench-asr ffmpeg-full

setup:
	@command -v uv >/dev/null || brew install uv
	@command -v deno >/dev/null || brew install deno
	@ffmpeg -hide_banner -filters 2>/dev/null | grep -q zscale || (brew uninstall --ignore-dependencies ffmpeg 2>/dev/null; brew install ffmpeg-full)
	uv sync --extra diarization
	uv run python -c "from clipforge.signals.audio import ensure_panns_files; from clipforge.vision.models import ensure_face_models; ensure_panns_files(); ensure_face_models()"
	cd web && npm install
	cd captions && npm install && npx remotion browser ensure
	uv run python scripts/fetch_fonts.py
	cd web && npm run build
	@test -f .env || cp .env.example .env

dev:
	CLIPFORGE_TOKEN=dev-token uv run uvicorn clipforge.api.app:app --app-dir backend --reload \
		--host 127.0.0.1 --port 8765 & \
	cd web && VITE_CLIPFORGE_TOKEN=dev-token npm run dev

start:
	uv run uvicorn clipforge.api.app:app --app-dir backend --host 127.0.0.1 --port 8765

test:
	uv run pytest -q
	cd web && npm test --silent

slow:
	uv run pytest -q -m slow

e2e:
	cd web && npx playwright install chromium && npx playwright test

bench-asr:
	uv run python scripts/bench_asr.py 180

lint:
	uv run ruff check backend scripts
	uv run ruff format --check backend scripts
	uv run pyright
	cd web && npm run lint && npm run typecheck

fmt:
	uv run ruff check --fix backend scripts
	uv run ruff format backend scripts

update-ytdlp:
	uv lock --upgrade-package yt-dlp
	uv sync
	@uv run python -c "import yt_dlp.version as v; print('yt-dlp', v.__version__)"

doctor:
	uv run clipforge doctor

fixtures:
	uv run python scripts/fetch_fixtures.py

ffmpeg-full:
	brew uninstall --ignore-dependencies ffmpeg; brew install ffmpeg-full
