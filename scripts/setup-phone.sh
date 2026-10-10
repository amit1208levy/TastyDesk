#!/usr/bin/env bash
# Let the Tasty Desk iPhone app read this Mac.
#
#   1. Tailscale forwards https://<this-mac>.<tailnet>.ts.net to the phone
#      listener on 127.0.0.1:8788. Only devices signed in to your tailnet can
#      reach that address; nothing is opened to the internet or the local network.
#   2. A fresh pairing token is made. Its hash goes into the Keychain; the code
#      the phone needs goes onto the clipboard, never onto the screen.
#
# Run it again to replace the phone's pairing (the old code stops working).
# `uv run tastydesk unpair` and `tailscale serve reset` undo it.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PHONE_PORT="${TASTYDESK_PHONE_PORT:-8788}"

TS="$(command -v tailscale || true)"
[[ -z "$TS" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]] &&
  TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
if [[ -z "$TS" ]]; then
  echo "Tailscale is not installed. Get it from https://tailscale.com/download/mac," >&2
  echo "sign in, then run this again." >&2
  exit 1
fi

HOST="$("$TS" status --json 2>/dev/null |
  uv run --no-project python -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
  2>/dev/null || true)"
if [[ -z "$HOST" ]]; then
  echo "Tailscale is not connected on this Mac. Open it, sign in, and run this again." >&2
  exit 1
fi

echo "Forwarding https://${HOST} → 127.0.0.1:${PHONE_PORT} (your tailnet only)…"
if ! "$TS" serve --bg --https=443 "http://127.0.0.1:${PHONE_PORT}"; then
  echo >&2
  echo "Tailscale would not serve HTTPS. In the admin console (login.tailscale.com/admin/dns)" >&2
  echo "turn on MagicDNS and HTTPS Certificates, then run this again." >&2
  exit 1
fi

uv run tastydesk pair --url "https://${HOST}"

echo
echo "Next:"
echo "  • Restart Tasty Desk on this Mac so it opens the phone listener."
echo "  • On the iPhone: Tailscale on, open Tasty Desk, tap Paste, then Connect."
