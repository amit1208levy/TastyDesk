#!/usr/bin/env bash
# Install a launchd job that records a position snapshot every weekday at 16:15
# New York time (just after the close).
#
# Snapshots are what make max adverse excursion knowable, and that is what lets
# the app answer "did I actually respect my 2x stop?". A day not snapshotted is
# a day that question can never be answered for.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="$(pwd)"
LABEL="com.tastydesk.snapshot"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/logs"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${HOME}/.local/bin/uv</string>
    <string>run</string>
    <string>--directory</string>
    <string>${PROJECT}</string>
    <string>tastydesk</string>
    <string>snapshot</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
  </array>
  <key>StandardOutPath</key><string>${PROJECT}/logs/snapshot.log</string>
  <key>StandardErrorPath</key><string>${PROJECT}/logs/snapshot.err</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed ${LABEL}."
echo "Times are your Mac's local clock — adjust the Hour in ${PLIST} if you are not on New York time."
echo "Remove it with: launchctl unload '${PLIST}' && rm '${PLIST}'"
