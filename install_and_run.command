#!/bin/bash
# Clipforge: one-go setup and launch for a Mac (Apple Silicon recommended). Safe to re-run.
set -e
cd "$(dirname "$0")"

echo "== Clipforge setup =="
if ! command -v brew >/dev/null; then
  echo "Homebrew is required. Install it from https://brew.sh (one command), then run this file again."
  read -r -p "Press Enter to close." _; exit 1
fi
command -v node >/dev/null || brew install node

make setup   # uv, deno, ffmpeg-full, Python deps, web + captions deps, fonts, Remotion browser, builds the UI

# Keys go in .env (owner-only). Skip anything you already have.
touch .env; chmod 600 .env
if ! grep -q '^ANTHROPIC_API_KEY=.\+' .env; then
  read -r -s -p "Anthropic API key (needed to pick clips; Enter to skip): " K; echo
  [ -n "$K" ] && sed -i '' '/^ANTHROPIC_API_KEY=/d' .env && echo "ANTHROPIC_API_KEY=$K" >> .env
fi
if ! grep -q '^HF_TOKEN=.\+' .env; then
  read -r -s -p "Hugging Face token (optional, for speaker detection; Enter to skip): " H; echo
  [ -n "$H" ] && sed -i '' '/^HF_TOKEN=/d' .env && echo "HF_TOKEN=$H" >> .env
fi

echo "== Starting Clipforge at http://127.0.0.1:8765 (Ctrl+C to stop) =="
( sleep 4; open "http://127.0.0.1:8765" ) &
exec make start
