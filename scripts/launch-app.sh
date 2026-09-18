#!/bin/bash
# Launcher used by "Tasty Desk.app" on the Desktop.
#
# Always serves the newest code: if any source file changed since the running
# server started, it rebuilds the web app and restarts the server. Otherwise it
# just opens the browser at the already-running instance.

REPO="${TASTYDESK_HOME:-$HOME/Desktop/DashboardV3}"
PORT="${TASTYDESK_PORT:-8787}"
URL="http://127.0.0.1:${PORT}/"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

notify() { osascript -e "display notification \"$1\" with title \"Tasty Desk\"" >/dev/null 2>&1; }
fail() {
  osascript -e "display dialog \"$1\" with title \"Tasty Desk\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

[ -d "$REPO" ] || fail "Can't find the Tasty Desk folder at:\n$REPO"
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
  open "$URL"
  exit 0
fi

if healthy; then
  notify "New changes found — rebuilding…"
else
  notify "Starting…"
fi

stop_server

: > "$LOG"
nohup ./run.sh >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

for _ in $(seq 1 120); do
  if healthy; then
    touch "$STAMP"
    open "$URL"
    exit 0
  fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    break
  fi
  sleep 1
done

TAIL="$(tail -n 12 "$LOG" 2>/dev/null | tr '"' "'" | tr '\\' '/')"
fail "Tasty Desk did not start.\n\nLast lines of the log:\n\n${TAIL}"
