#!/usr/bin/env bash
# Build the Tasty Desk iPhone app and upload it to TestFlight.
#
# Runs on macOS with Xcode: on GitHub (.github/workflows/ios.yml, the usual
# way) or on a Mac. Signing is automatic, with an App Store Connect API key:
#
#   APPLE_TEAM_ID   10-character Team ID (developer.apple.com -> Membership)
#   ASC_KEY_ID      the key's ID
#   ASC_ISSUER_ID   the issuer ID shown above the keys list
#   ASC_KEY_PATH    the .p8 file, readable only by you (chmod 600)
#
# On a Mac with the team's Apple ID added in Xcode -> Settings -> Accounts,
# APPLE_TEAM_ID alone is enough.
#
# See ios/README.md for the one-time setup in App Store Connect.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID to your 10-character Team ID (developer.apple.com -> Membership)}"
command -v xcodebuild >/dev/null || { echo "Xcode is not installed." >&2; exit 1; }

# Build numbers only ever go up, and a timestamp always does.
BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"
OUT="web/ios/App/build"

AUTH=()
if [[ -n "${ASC_KEY_ID:-}" ]]; then
  : "${ASC_ISSUER_ID:?Set ASC_ISSUER_ID along with ASC_KEY_ID}"
  : "${ASC_KEY_PATH:?Set ASC_KEY_PATH to the .p8 file along with ASC_KEY_ID}"
  AUTH=(-authenticationKeyPath "$ASC_KEY_PATH" -authenticationKeyID "$ASC_KEY_ID"
        -authenticationKeyIssuerID "$ASC_ISSUER_ID")
fi

echo "-> Bundling the engine (the Mac's Python, for the phone)"
(cd ios && npm ci --no-audit --no-fund && npm run bundle)

echo "-> Building the pages"
(cd web && npm ci --no-audit --no-fund && npm run build && npx cap sync ios)

rm -rf "$OUT"
mkdir -p "$OUT"

echo "-> Archiving build ${BUILD_NUMBER}"
xcodebuild \
  -project web/ios/App/App.xcodeproj \
  -scheme App \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$OUT/App.xcarchive" \
  -allowProvisioningUpdates ${AUTH[@]+"${AUTH[@]}"} \
  DEVELOPMENT_TEAM="$APPLE_TEAM_ID" \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  archive

cat > "$OUT/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>signingStyle</key><string>automatic</string>
  <key>teamID</key><string>${APPLE_TEAM_ID}</string>
  <key>uploadSymbols</key><true/>
  <key>testFlightInternalTestingOnly</key><true/>
</dict>
</plist>
PLIST

echo "-> Uploading to App Store Connect"
xcodebuild \
  -exportArchive \
  -archivePath "$OUT/App.xcarchive" \
  -exportOptionsPlist "$OUT/ExportOptions.plist" \
  -exportPath "$OUT/export" \
  -allowProvisioningUpdates ${AUTH[@]+"${AUTH[@]}"}

echo
echo "Uploaded build ${BUILD_NUMBER}. It appears in App Store Connect -> TestFlight"
echo "once Apple finishes processing it (usually 5-30 minutes)."
