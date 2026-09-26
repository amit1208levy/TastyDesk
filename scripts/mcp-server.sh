#!/usr/bin/env bash
# Start the Tasty Desk MCP server for Claude.
#
# This exists so .mcp.json holds no absolute path: it is registered as
# ./scripts/mcp-server.sh and works in any clone, under any username, without
# being edited. Everything machine-specific is worked out here at run time.
set -euo pipefail

# The project root is wherever this script lives, not wherever Claude was
# started from.
cd "$(dirname "$0")/.."

# A GUI-launched app does not inherit a login shell's PATH, so uv's usual home
# is added explicitly. Same reason run.sh does it.
export PATH="$HOME/.local/bin:$PATH"

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH. Install it: https://docs.astral.sh/uv/" >&2
  exit 1
fi

exec uv run python -m tastydesk.mcp.server
