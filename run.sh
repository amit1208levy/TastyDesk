#!/usr/bin/env bash
# Start Tasty Desk. Builds the web app if needed, then serves everything from
# one local process on 127.0.0.1 — nothing is exposed to the network.
set -euo pipefail

cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

PORT="${TASTYDESK_PORT:-8787}"

# This project sits on an iCloud-synced folder. iCloud rewrites files it is
# syncing and leaves "name 2.ext" duplicates behind, which silently corrupts a
# virtualenv and node_modules. These directories are machine-local build output,
# so mark them as things iCloud must leave alone.
for d in .venv web/node_modules logs; do
  [[ -d "$d" ]] && xattr -w "com.apple.fileprovider.ignore#P" 1 "$d" 2>/dev/null || true
done

if [[ ! -d web/dist ]] || [[ -n "$(find web/src -newer web/dist -type f -print -quit 2>/dev/null)" ]]; then
  echo "Building the web app…"
  (cd web && npm run build)
fi

echo "Tasty Desk → http://127.0.0.1:${PORT}"
exec uv run uvicorn tastydesk.api.main:app --host 127.0.0.1 --port "$PORT"
