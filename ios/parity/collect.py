"""Sync the fake account and read back what the screens show, as JSON.

Run on the Mac's Python (``uv run python ios/parity/collect.py``) and inside
the phone engine (``run.mjs``); ``compare.py`` then checks the two agree.
Every request goes through the same FastAPI app the Mac serves.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fake_tastytrade  # noqa: E402

# The Positions screen first: everything it asks for. Then the rest of the
# money pages, which ride along on the same engine.
POSITIONS = [
    "/api/portfolio/summary",
    "/api/portfolio/greeks",
    "/api/strategies/open",
    "/api/legs/open",
    "/api/settings",
    "/api/fields",
]
OTHERS = [
    "/api/strategies/closed",
    "/api/performance",
    "/api/performance/by-strategy",
    "/api/performance/by-underlying",
    "/api/performance/loss-shape",
    "/api/strategies/named",
    "/api/needs-review",
    "/api/scenario/curve?iv=0&days=0",
]


def _bridge():
    """tastydesk_phone, loaded by path so the Mac run keeps its own libraries."""
    path = HERE.parent / "engine" / "py" / "tastydesk_phone.py"
    spec = importlib.util.spec_from_file_location("tastydesk_phone", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def collect() -> dict:
    fake_tastytrade.install()

    from tastydesk.api import main
    from tastydesk.core.auth import SessionManager
    from tastydesk.core.client import TastyClient
    from tastydesk.core.db import Database
    from tastydesk.service import DeskService

    tmp = tempfile.mkdtemp(prefix="tastydesk-parity-")
    main.build_service = lambda: DeskService(Database(Path(tmp) / "parity.db"), TastyClient(SessionManager()))

    phone = _bridge()
    await phone.start()
    out: dict = {"_runtime": sys.platform, "_python": sys.version.split()[0]}
    try:
        sync = await phone.request("POST", "/api/sync", "", [], b"")
        out["POST /api/sync"] = {"status": sync["status"], "body": json.loads(sync["body"])}
        for target in POSITIONS + OTHERS:
            path, _, query = target.partition("?")
            res = await phone.request("GET", path, query, [["accept", "application/json"]], b"")
            try:
                body = json.loads(res["body"])
            except ValueError:
                body = res["body"]
            out[target] = {"status": res["status"], "body": body}
    finally:
        await phone.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


async def main_json() -> str:
    return fake_tastytrade.dumps(await collect())


if __name__ == "__main__":
    print(asyncio.run(main_json()))
