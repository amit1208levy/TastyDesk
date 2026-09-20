#!/bin/bash
# Launcher used by "Tasty Desk.app" on the Desktop.
#
# Always serves the newest code: if any source file changed since the running
# server started, it rebuilds the web app and restarts the server. Otherwise it
# just opens the browser at the already-running instance.

REPO="${TASTYDESK_HOME:-$HOME/TastyDesk}"
PORT="${TASTYDESK_PORT:-8787}"
URL="http://127.0.0.1:${PORT}/"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Every run leaves a trace. When someone says "the shortcut isn't working",
# the only useful thing is a record of what happened the last time it ran.
TRACE="${TASTYDESK_HOME:-$HOME/TastyDesk}/logs/launcher.log"
trace() {
  mkdir -p "$(dirname "$TRACE")" 2>/dev/null
  printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$TRACE" 2>/dev/null
}

notify() { osascript -e "display notification \"$1\" with title \"Tasty Desk\"" >/dev/null 2>&1; }
fail() {
  trace "FAILED: $1"
  osascript -e "display dialog \"$1\" with title \"Tasty Desk\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

trace "launch requested (user=$(id -un) pwd=$PWD)"

[ -d "$REPO" ] || fail "Can't find the Tasty Desk folder at:\n$REPO"
# Reading the folder can fail on its own even when it exists: an app launched
# from Finder needs permission for the Desktop, and a refusal there looks
# exactly like nothing happening.
ls "$REPO" >/dev/null 2>&1 || fail "macOS is not letting Tasty Desk read:\n$REPO\n\nGive it access in System Settings > Privacy & Security > Files and Folders, then try again."
cd "$REPO" || fail "Can't open $REPO"
mkdir -p logs

LOG="logs/app.log"
PIDFILE="logs/server.pid"
STAMP="logs/server.started"

healthy() { curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; }

# Any source file newer than the moment the running server came up?
stale() {
  [ -f "$STAMP" ] || return 0
  [ ! -d web/dist ] && return 0
  [ -n "$(find src web/src web/index.html web/package.json run.sh -newer "$STAMP" -type f -print -quit 2>/dev/null)" ]
}

stop_server() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
    sleep 1
  fi
  local pids
  pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null)"
  [ -n "$pids" ] && kill $pids 2>/dev/null && sleep 1
  pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null)"
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  rm -f "$PIDFILE"
  return 0
}

if healthy && ! stale; then
  trace "already running and current; opening $URL"
  open "$URL" || fail "Tasty Desk is running at $URL but macOS could not open your browser."
  notify "Open at 127.0.0.1:${PORT}"
  exit 0
fi

if healthy; then
  trace "changes found; rebuilding"
  notify "New changes found — rebuilding…"
else
  trace "not running; starting"
  notify "Starting…"
fi

stop_server

# Keep the previous run's log. Truncating it on every launch means that by the
# time anyone asks what happened, the answer has already been thrown away.
[[ -f "$LOG" ]] && mv "$LOG" "${LOG%.log}.previous.log"
nohup ./run.sh >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

for _ in $(seq 1 120); do
  if healthy; then
    touch "$STAMP"
    trace "started; opening $URL"
    open "$URL" || fail "Tasty Desk started at $URL but macOS could not open your browser."
    exit 0
  fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    break
  fi
  sleep 1
done

trace "FAILED to start; see logs/app.log"
TAIL="$(tail -n 12 "$LOG" 2>/dev/null | tr '"' "'" | tr '\\' '/')"
fail "Tasty Desk did not start.\n\nLast lines of the log:\n\n${TAIL}"
