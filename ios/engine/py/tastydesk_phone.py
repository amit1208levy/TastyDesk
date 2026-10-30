"""The Tasty Desk engine inside the iPhone app.

The phone runs the Mac app's own Python, unchanged, in Pyodide. The pages call
``/api/...`` exactly as they do on the Mac; the app hands each call to
:func:`request`, which runs it through the same FastAPI app the Mac serves.
Nothing here computes a number: every figure comes from ``tastydesk``.

What differs on the phone is only the plumbing around it:

* the secrets come from the iOS Keychain (:func:`set_credentials`), not the
  macOS one;
* the database lives in Pyodide's in-memory disk while the app runs, and the
  app keeps the real copy in its own storage (:func:`export_db`,
  :func:`restore_db`, :func:`import_db`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import time
from collections import deque
from contextlib import AbstractAsyncContextManager
from typing import Any

import keyring

from tastydesk.core import auth
from tastydesk.core.db import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

__all__ = [
    "DB_PATH",
    "set_credentials",
    "clear_credentials",
    "has_credentials",
    "start",
    "stop",
    "request",
    "export_db",
    "restore_db",
    "import_db",
    "check_db",
    "restart",
    "build_info",
    "status",
]

DB_PATH = DEFAULT_DB_PATH

# Tables a Tasty Desk database must have. An import missing any of them is not
# a Tasty Desk database (or is one from a version too old to read).
_REQUIRED_TABLES = ("strategies", "named_strategies", "settings")

# What an import reports, so the counts can be checked against the Mac.
_COUNTED_TABLES = (
    "strategies",
    "named_strategies",
    "named_strategy_members",
    "trade_plans",
    "settings",
    "transactions",
)

_lifespan: AbstractAsyncContextManager[Any] | None = None
_exported_changes: int | None = None


# --------------------------------------------------------------------------- #
# What the engine is doing (for the app to show when a page will not load)
# --------------------------------------------------------------------------- #

# Page requests and tastytrade calls still running, and the last calls made.
# Labels and timings only: never a URL with parameters, a body or a key.
_pending_requests: dict[int, tuple[str, float]] = {}
_pending_calls: dict[int, tuple[str, float]] = {}
_recent_calls: deque[dict] = deque(maxlen=20)
_starting_since: float | None = None


def _install_call_tracker() -> None:
    """Wrap TastyClient._guard, the one door every tastytrade call goes through."""
    from tastydesk.core.client import TastyClient

    original = TastyClient._guard  # noqa: SLF001
    if getattr(original, "_phone_tracked", False):
        return

    async def tracked(self, label, call):  # noqa: ANN001, ANN202
        key = object()
        started = time.time()
        _pending_calls[id(key)] = (label, started)
        try:
            result = await original(self, label, call)
        except Exception as exc:
            reason = (str(exc).strip().splitlines() or [""])[0][:160]
            _recent_calls.append(
                {
                    "call": label,
                    "seconds": round(time.time() - started, 1),
                    "error": reason or type(exc).__name__,
                }
            )
            raise
        else:
            _recent_calls.append({"call": label, "seconds": round(time.time() - started, 1), "error": None})
            return result
        finally:
            _pending_calls.pop(id(key), None)

    tracked._phone_tracked = True  # type: ignore[attr-defined]
    TastyClient._guard = tracked  # noqa: SLF001


def status() -> dict:
    """What is running now, and how long for. Shown by the app, never logged."""
    now = time.time()

    def running(items: dict[int, tuple[str, float]]) -> list[dict]:
        return [
            {"what": w, "seconds": round(now - t, 1)} for w, t in sorted(items.values(), key=lambda v: v[1])
        ]

    return {
        "starting_for": None if _starting_since is None else round(now - _starting_since, 1),
        "requests": running(_pending_requests),
        "calls": running(_pending_calls),
        "recent": list(_recent_calls)[::-1],
    }


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


def set_credentials(client_secret: str, refresh_token: str) -> None:
    """Hold the two secrets in memory for ``core/auth.py``. Never logged."""
    keyring.set_password(auth.SERVICE_CLIENT_SECRET, auth.KEYCHAIN_ACCOUNT, client_secret)
    keyring.set_password(auth.SERVICE_REFRESH_TOKEN, auth.KEYCHAIN_ACCOUNT, refresh_token)


def clear_credentials() -> None:
    keyring.delete_password(auth.SERVICE_CLIENT_SECRET, auth.KEYCHAIN_ACCOUNT)
    keyring.delete_password(auth.SERVICE_REFRESH_TOKEN, auth.KEYCHAIN_ACCOUNT)


def has_credentials() -> bool:
    return auth.credentials_present()


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


async def start() -> None:
    """Start the same service the Mac's server starts (its FastAPI lifespan)."""
    global _lifespan, _exported_changes, _starting_since
    if _lifespan is not None:
        return
    from tastydesk.api.main import app

    _install_call_tracker()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    lifespan = app.router.lifespan_context(app)
    _starting_since = time.time()
    try:
        await lifespan.__aenter__()
    finally:
        _starting_since = None
    _lifespan = lifespan
    # Whatever was just loaded is already saved; only later changes need saving.
    _exported_changes = _raw_connection().total_changes


async def stop() -> None:
    global _lifespan
    if _lifespan is None:
        return
    lifespan, _lifespan = _lifespan, None
    await lifespan.__aexit__(None, None, None)


async def restart() -> None:
    """Pick up new credentials: the session is opened once and kept."""
    await stop()
    await start()


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def _as_bytes(data: Any) -> bytes:
    """Bytes from Python or from JavaScript (a Uint8Array arrives as a JsProxy)."""
    if isinstance(data, str):
        return data.encode()
    if hasattr(data, "to_bytes") and not isinstance(data, (bytes, bytearray, memoryview)):
        return data.to_bytes()
    return bytes(data)


async def request(method: str, path: str, query: str, headers: str | list, body: Any = b"") -> dict:
    """Run one HTTP request through the FastAPI app, in process.

    A minimal ASGI client: one request, the whole body at once, the whole
    response collected. Returns ``{"status", "headers", "body"}`` with the body
    as text (every Tasty Desk response is JSON or plain text). Headers come as
    ``[[name, value], ...]``, or that list as JSON.
    """
    from tastydesk.api.main import app

    if isinstance(headers, str):
        headers = json.loads(headers)
    payload = _as_bytes(body)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "state": {},
    }
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    status = 500
    out_headers: list[list[str]] = []
    chunks: list[bytes] = []

    async def send(message: dict) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            out_headers.extend([k.decode("latin-1"), v.decode("latin-1")] for k, v in message["headers"])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    key = object()
    _pending_requests[id(key)] = (f"{method.upper()} {path}", time.time())
    try:
        await app(scope, receive, send)
    except Exception as exc:
        # On the Mac, uvicorn turns an unhandled error into a 500 for the page.
        # Here nothing would: Starlette sends its 500 and then re-raises, and a
        # raise reaching the page reads as "backend not reachable", hiding what
        # actually went wrong. Answer with the error's name and first line.
        logger.exception("Unhandled error in %s %s", method, path)
        reason = (str(exc).strip().splitlines() or [""])[0][:300]
        detail = f"{type(exc).__name__}: {reason}" if reason else type(exc).__name__
        return {
            "status": 500,
            "headers": [["content-type", "application/json"]],
            "body": json.dumps({"detail": detail}),
        }
    finally:
        _pending_requests.pop(id(key), None)
    return {"status": status, "headers": out_headers, "body": b"".join(chunks).decode("utf-8")}


# --------------------------------------------------------------------------- #
# The database file
# --------------------------------------------------------------------------- #


def _raw_connection() -> sqlite3.Connection:
    from tastydesk.api.main import svc

    # The aiosqlite stand-in wraps a plain sqlite3 connection.
    return svc()._db.connection._conn  # noqa: SLF001


def export_db(force: bool = False) -> bytes | None:
    """A consistent copy of the live database, or None if nothing changed.

    Also None while a write is half done; the app asks again shortly.
    """
    global _exported_changes
    if _lifespan is None:
        return None
    conn = _raw_connection()
    if conn.in_transaction:
        return None
    if not force and conn.total_changes == _exported_changes:
        return None
    data = _copy_of(conn)
    _exported_changes = conn.total_changes
    return data


def restore_db(data: Any) -> None:
    """Put the saved copy back on the in-memory disk, before :func:`start`."""
    data = _as_bytes(data)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        DB_PATH.with_name(DB_PATH.name + suffix).unlink(missing_ok=True)
    DB_PATH.write_bytes(data)


def _copy_of(conn: sqlite3.Connection) -> bytes:
    """The database as one self-contained file, in rollback-journal mode.

    A WAL-mode file can't be checked or restored on its own, so the copy is
    switched out of WAL; the app switches it back when it opens it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/copy.db"
        copy = sqlite3.connect(path)
        try:
            conn.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
        finally:
            copy.close()
        with open(path, "rb") as f:
            return f.read()


def check_db(data: Any) -> dict:
    """Is this a whole, readable Tasty Desk database? Counts its rows if so."""
    data = _as_bytes(data)
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/check.db"
        with open(path, "wb") as f:
            f.write(data)
        conn = sqlite3.connect(path)
        try:
            report = _check(conn)
        except sqlite3.DatabaseError:
            return {"ok": False, "reason": "This file is not a SQLite database."}
        finally:
            conn.close()
    # A WAL-mode file copied without its -wal sidecar is missing its latest
    # writes, sometimes all of them. The Mac's export makes a whole copy.
    if not report["ok"] and data[18:20] == b"\x02\x02":
        report["reason"] = (
            "This copy is missing recent changes. Make it with scripts/export-for-phone.sh on the Mac."
        )
    return report


def _check(conn: sqlite3.Connection) -> dict:
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        return {"ok": False, "reason": "The database file is damaged."}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if any(t not in tables for t in _REQUIRED_TABLES):
        return {"ok": False, "reason": "This is not a Tasty Desk database."}
    counts = {
        t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in _COUNTED_TABLES if t in tables
    }
    return {"ok": True, "counts": counts}


async def import_db(data: Any) -> dict:
    """Replace the phone's database with one brought over from the Mac.

    Checked before anything is replaced; a bad file leaves the current one
    untouched.
    """
    data = _as_bytes(data)
    report = check_db(data)
    if not report["ok"]:
        return report
    await stop()
    restore_db(data)
    # The copy came from the Mac with the Mac's device id for sharing; this
    # phone is a device of its own, or the two would write the same file.
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM settings WHERE key = 'sync_device'")
    await start()
    # Saved straight away: the app must not lose an import to a quick close.
    report["saved"] = export_db(force=True)
    return report


# --------------------------------------------------------------------------- #
# Other devices
# --------------------------------------------------------------------------- #


async def device_sync(files: Any) -> dict:
    """One step of keeping hand-set data the same as on the user's other devices.

    ``files`` is the shared folder's sync files, ``{name: bytes}``, as the app
    read them from iCloud Drive. Returns this phone's file to write back
    (``outgoing`` is None when nothing changed) and the status to show.
    """
    from tastydesk.api.main import svc

    if hasattr(files, "to_py"):
        files = files.to_py()
    incoming = {str(name): _as_bytes(data) for name, data in dict(files).items()}
    step = await svc().device_sync(incoming, label="iPhone")
    return {"filename": step.filename, "outgoing": step.outgoing, "report": svc().device_sync_status()}


def build_info() -> dict:
    """Which Tasty Desk commit this engine was built from."""
    try:
        from tastydesk_build import COMMIT
    except ImportError:
        COMMIT = "unknown"
    return {"commit": COMMIT}
