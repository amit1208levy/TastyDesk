#!/usr/bin/env bash
# Store Tasty Desk credentials in the macOS Keychain.
#
# Prompts with the screen hidden. Nothing is echoed, written to a file, or kept
# in shell history. Run this yourself; never paste these values into a chat.
set -euo pipefail

ACCOUNT="tastydesk"

echo "Tasty Desk — credential setup"
echo
echo "From my.tastytrade.com → Manage → My Profile → API → OAuth Applications."
echo "Use an application whose grant has the 'read' scope ONLY."
echo

read -r -s -p "Client secret: " CLIENT_SECRET; echo
read -r -s -p "Refresh token: " REFRESH_TOKEN; echo

if [[ -z "$CLIENT_SECRET" || -z "$REFRESH_TOKEN" ]]; then
  echo "Both values are required. Nothing was saved." >&2
  exit 1
fi

security add-generic-password -U -a "$ACCOUNT" -s tastydesk-client-secret -w "$CLIENT_SECRET"
security add-generic-password -U -a "$ACCOUNT" -s tastydesk-refresh-token -w "$REFRESH_TOKEN"

unset CLIENT_SECRET REFRESH_TOKEN

echo
echo "Saved to the login Keychain."
echo "To remove them later:"
echo "  security delete-generic-password -a $ACCOUNT -s tastydesk-client-secret"
echo "  security delete-generic-password -a $ACCOUNT -s tastydesk-refresh-token"
