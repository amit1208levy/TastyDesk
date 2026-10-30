#!/usr/bin/env bash
# Store the four App Store Connect values the TestFlight build needs as
# GitHub repository secrets, one question at a time, checking each.
#
# Run it yourself on the Mac. Values are typed with the screen hidden and go
# straight to GitHub (through gh, on standard input): never into a file, the
# shell history, a command line, or a chat.
#
#   bash scripts/set-testflight-secrets.sh
set -euo pipefail

REPO="amit1208levy/TastyDesk"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v gh >/dev/null; then
  echo "GitHub's gh tool is missing. Install it with:  brew install gh" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Signing in to GitHub first (a browser window opens)..."
  gh auth login --web
fi

ask() { # ask NAME "where to find it" REGEX "what it looks like"
  local name=$1 hint=$2 pattern=$3 shape=$4 value
  while true; do
    echo
    echo "$name: $hint"
    read -r -s -p "Paste it here (nothing shows as you paste), then press Return: " value
    echo
    value=$(printf '%s' "$value" | tr -d '[:space:]')
    if [[ "$value" =~ $pattern ]]; then
      printf '%s' "$value" | gh secret set "$name" -R "$REPO" >/dev/null
      echo "  saved."
      REPLY_VALUE=$value
      return
    fi
    echo "  That doesn't look right: it should be $shape. Try again."
  done
}

echo "Four values, about two minutes. Keep App Store Connect open in Chrome."

ask APPLE_TEAM_ID \
  "developer.apple.com/account -> Membership details -> Team ID" \
  '^[A-Z0-9]{10}$' "10 capital letters and digits, like ABCDE12345"

ask ASC_KEY_ID \
  "appstoreconnect.apple.com/access/integrations/api -> the KEY ID column of your key" \
  '^[A-Z0-9]{10}$' "10 capital letters and digits, like 2X9R4HXF34"
KEY_ID=$REPLY_VALUE

ask ASC_ISSUER_ID \
  "same page, 'Issuer ID' above the list of keys" \
  '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' \
  "five groups of letters and digits joined by dashes, like 57246542-96fe-1a63-e053-0824d011072a"

# The key file: Apple names it AuthKey_<KEY ID>.p8.
echo
FILE=$(mdfind -name "AuthKey_${KEY_ID}.p8" 2>/dev/null | head -1 || true)
if [[ -z "$FILE" ]]; then
  FILE=$(ls "$HOME"/Downloads/AuthKey_"${KEY_ID}".p8 "$HOME"/Desktop/AuthKey_"${KEY_ID}".p8 2>/dev/null | head -1 || true)
fi
while [[ -z "$FILE" || ! -f "$FILE" ]]; do
  echo "Could not find AuthKey_${KEY_ID}.p8 on this Mac."
  echo "Find it in Finder and drag it into this window, then press Return."
  echo "(If you never downloaded it: revoke the key in App Store Connect, make a new one,"
  echo " download it, and run this script again.)"
  read -r -p "> " FILE
  FILE=${FILE%"${FILE##*[![:space:]]}"}
  FILE=${FILE#\'}; FILE=${FILE%\'}; FILE=${FILE//\\ / }
done
if ! grep -q "BEGIN PRIVATE KEY" "$FILE"; then
  echo "$FILE is not an App Store Connect key file." >&2
  exit 1
fi
gh secret set ASC_KEY_P8 -R "$REPO" < "$FILE" >/dev/null
echo "ASC_KEY_P8: saved from $FILE"

echo
gh secret list -R "$REPO"
echo
echo "All four saved. Tell Claude 'done'. You can now delete $FILE"
echo "(GitHub keeps its own copy; Apple will not let you download it again)."
