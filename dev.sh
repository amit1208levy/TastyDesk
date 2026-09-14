#!/usr/bin/env bash
# Development mode: Vite on 5273 with hot reload, FastAPI on 8787 behind its
# proxy. Open http://127.0.0.1:5273. Ctrl-C stops both.
set -euo pipefail

cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

uv run uvicorn tastydesk.api.main:app --host 127.0.0.1 --port 8787 --reload &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT INT TERM

cd web && npm run dev
