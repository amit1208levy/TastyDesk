"""The Mac's half of the two-device check (run.mjs), on the Mac's own Python.

    mac.py write <folder> <db>   name a strategy and share it
    mac.py read  <folder> <db>   take in what the phone shared; print it as JSON

The secret is a made-up one for this check; the account number is the fake
parity account's.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from tastydesk.core import devicesync
from tastydesk.core.db import Database

SECRET = "device-check-secret"
ACCOUNT = "5WX00001"
SIGNATURE = {
    "product": "/ZB",
    "legs": [{"right": "C", "side": "short", "ratio": 1}, {"right": "P", "side": "short", "ratio": 1}],
    "expiry_pattern": "single",
    "window_minutes": 5,
}


async def _step(db: Database, folder: Path) -> dict:
    incoming = {p.name: p.read_bytes() for p in folder.glob(f"*{devicesync.SUFFIX}")}
    step = await devicesync.step(db, secret=SECRET, label="Mac", incoming=incoming)
    if step.outgoing is not None:
        (folder / step.filename).write_bytes(step.outgoing)
    return step.report


async def main(action: str, folder: Path, path: Path) -> None:
    db = Database(path)
    await db.connect()
    await db.migrate()
    try:
        if action == "write":
            await db.connection.execute(
                "INSERT INTO transactions (id, account_number) VALUES (1, ?)", (ACCOUNT,)
            )
            await db.connection.commit()
            await db.save_named_strategy(
                {"id": "zb", "name": "/ZB 112 straddle", "product": "/ZB", "signature": SIGNATURE}
            )
            await db.set_named_members("zb", ["t1", "t2"])
            await db.set_setting("match_threshold", "0.9")
            await db.set_manual_override("s2", "s1")
            report = await _step(db, folder)
        else:
            report = await _step(db, folder)
        named = await db.get_named_strategies()
        print(
            json.dumps(
                {
                    "report": {k: report.get(k) for k in ("state", "sent", "received", "problems")},
                    "named": {n["id"]: [n["name"], n["member_ids"]] for n in named},
                    "match_threshold": await db.get_setting("match_threshold"),
                    "overrides": await db.get_manual_overrides(),
                }
            )
        )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])))
