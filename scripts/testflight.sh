#!/usr/bin/env bash
# Build the Tasty Desk iPhone app and upload it to TestFlight.
#
# Runs on a Mac with Xcode. Signing is automatic: Xcode makes or reuses the
# certificate and profile for your team. It signs in one of two ways:
#
#   * the Apple ID added in Xcode → Settings → Accounts (nothing to set), or
#   * an App Store Connect API key, for running without Xcode's UI:
#       ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_PATH (the .p8 file, kept out of
#       this folder and readable only by you: chmod 600).
#
#   APPLE_TEAM_ID=ABCDE12345 ./scripts/testflight.sh
#
# See web/ios/README.md for the one-time setup in App Store Connect.
set -euo pipefail

cd "$(dirname "$0")/../web"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID to your 10-character Team ID (developer.apple.com → Membership)}"
command -v xcodebuild >/dev/null || { echo "Xcode is not installed." >&2; exit 1; }

# Build numbers only ever go up, and a timestamp always does.
BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"
OUT="ios/App/build"

AUTH=()
if [[ -n "${ASC_KEY_ID:-}" ]]; then
  : "${ASC_ISSUER_ID:?Set ASC_ISSUER_ID along with ASC_KEY_ID}"
  : "${ASC_KEY_PATH:?Set ASC_KEY_PATH to the .p8 file along with ASC_KEY_ID}"
  AUTH=(-authenticationKeyPath "$ASC_KEY_PATH" -authenticationKeyID "$ASC_KEY_ID"
        -authenticationKeyIssuerID "$ASC_ISSUER_ID")
fi

echo "→ Building the pages"
npm ci --no-audit --no-fund
npm run build
npx cap sync ios

rm -rf "$OUT"
mkdir -p "$OUT"

echo "→ Archiving build ${BUILD_NUMBER}"
xcodebuild \
  -project ios/App/App.xcodeproj \
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

echo "→ Uploading to App Store Connect"
xcodebuild \
  -exportArchive \
  -archivePath "$OUT/App.xcarchive" \
  -exportOptionsPlist "$OUT/ExportOptions.plist" \
  -exportPath "$OUT/export" \
  -allowProvisioningUpdates ${AUTH[@]+"${AUTH[@]}"}

echo
echo "Uploaded build ${BUILD_NUMBER}. It appears in App Store Connect → TestFlight"
echo "once Apple finishes processing it (usually 5–30 minutes)."
