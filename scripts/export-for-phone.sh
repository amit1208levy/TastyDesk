#!/usr/bin/env bash
# Copy the Tasty Desk database into one file for the iPhone app.
#
# Uses SQLite's backup, so the copy is whole even while the Mac app is running
# (a plain file copy can miss the latest writes, which sit in a -wal file).
# The copy holds your trade history: it is readable by you only, and the app
# deletes nothing on the Mac. Send it to the phone by AirDrop, then in the app:
# Settings -> Import database.
#
#   ./scripts/export-for-phone.sh            # -> ~/Desktop/TastyDesk-for-iPhone.db
#   ./scripts/export-for-phone.sh out.db
set -euo pipefail

SRC="$HOME/Library/Application Support/TastyDesk/tastydesk.db"
OUT="${1:-$HOME/Desktop/TastyDesk-for-iPhone.db}"

[[ -f "$SRC" ]] || { echo "No database at $SRC" >&2; exit 1; }
[[ -e "$OUT" ]] && { echo "$OUT already exists; move it away first." >&2; exit 1; }

umask 077
python3 - "$SRC" "$OUT" <<'PY'
import sqlite3
import sys

src, out = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
copy = sqlite3.connect(out)
source.backup(copy)
# One self-contained file: the phone opens it without a -wal sidecar.
copy.execute("PRAGMA journal_mode=DELETE")
ok = copy.execute("PRAGMA integrity_check").fetchone()[0]
print(f"Integrity check: {ok}")
print("Rows, to compare with the phone after import:")
for table in ("strategies", "named_strategies", "named_strategy_members", "trade_plans", "settings", "transactions"):
    n = copy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    print(f"  {table:<24} {n}")
copy.close()
source.close()
PY
chmod 600 "$OUT"
echo
echo "Saved $OUT"
echo "AirDrop it to your iPhone, then delete it here once it is imported."
