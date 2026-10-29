"""What you set by hand, the same on every device.

Positions, prices and P&L come from tastytrade on each device on its own. What
does not is everything typed into the app: named strategies and the trades in
them, links and pairing answers, roll decisions, trade plans, settings and the
questions thread. Those live in each device's own database, so without this a
strategy named on the Mac was a stranger on the iPhone.

How it works:

- Each device keeps one file in a shared folder (iCloud Drive), named by a
  random device id, holding every record it knows. A device only ever writes
  its own file, so two devices can never overwrite each other.
- A record is one row of one of the tables below, with the time it last
  changed and the device that changed it. A deleted row stays as a record
  with no row (a tombstone), so a deletion travels like an edit.
- A sync step finds what changed here since the last step (by comparing the
  tables with what was recorded), reads the other devices' files, keeps the
  latest version of each record, writes the result into the tables, and hands
  back this device's new file. The same code runs on the Mac and in the phone
  engine; only who reads and writes the folder differs.
- The files are encrypted (AES-256-GCM) with a key derived from the tastytrade
  client secret, which both devices already hold and nobody else does. Inside,
  each file names its tastytrade account numbers, and a device only takes
  records from a file that shares an account with its own database.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

if TYPE_CHECKING:
    from tastydesk.core.db import Database

FORMAT = 1
SUFFIX = ".tdsync"
DEVICE_SETTING = "sync_device"

_SALT = b"tastydesk-device-sync"
_INFO = b"file key v1"
_AAD = b"tastydesk-device-sync-v1:"


@dataclass(frozen=True, slots=True)
class Kind:
    """One table that is shared, and how its rows are told apart."""

    name: str
    table: str
    key: tuple[str, ...]
    #: Every column that travels, the key included. An autoincrement id does
    #: not: each device numbers its own rows.
    columns: tuple[str, ...]
    #: The row's own record of when it was set, for a device's first step.
    stamp: str = ""
    #: Whether the key is the table's own primary key (so a row can be updated
    #: in place). Plans and questions are keyed by an id each device assigns,
    #: so they are matched on what they say and replaced whole.
    unique: bool = True


KINDS: tuple[Kind, ...] = (
    Kind("setting", "settings", ("key",), ("key", "value", "set_at"), stamp="set_at"),
    # Before its members: a member needs its strategy to exist.
    Kind(
        "named",
        "named_strategies",
        ("id",),
        ("id", "name", "product", "signature", "note", "created_at"),
        stamp="created_at",
    ),
    Kind(
        "member",
        "named_strategy_members",
        ("strategy_id", "trade_id"),
        ("strategy_id", "trade_id", "added_at", "confirmed"),
        stamp="added_at",
    ),
    Kind(
        "link",
        "manual_overrides",
        ("strategy_id",),
        ("strategy_id", "group_id", "created_at"),
        stamp="created_at",
    ),
    Kind(
        "pairing",
        "pairing_rules",
        ("pattern",),
        ("pattern", "decision", "decided_at", "note"),
        stamp="decided_at",
    ),
    Kind(
        "roll",
        "roll_decisions",
        ("closed_id", "opened_id"),
        ("closed_id", "opened_id", "decision", "decided_at", "note"),
        stamp="decided_at",
    ),
    Kind(
        "plan",
        "trade_plans",
        ("strategy_id", "saved_at"),
        ("strategy_id", "saved_at", "plan", "while_open"),
        stamp="saved_at",
        unique=False,
    ),
    Kind(
        "question",
        "questions",
        ("asked_at", "question"),
        ("asked_at", "question", "answer", "answered_at", "context"),
        stamp="asked_at",
        unique=False,
    ),
)
_BY_NAME = {k.name: k for k in KINDS}

#: Kinds whose change means the strategies have to be rebuilt.
REGROUP = frozenset({"link", "pairing"})


def _device_local(kind: Kind, row: dict[str, Any]) -> bool:
    """Settings that describe this device, not the user: never shared."""
    return kind.name == "setting" and (row["key"] == "last_sync" or str(row["key"]).startswith("sync_"))


_EPOCH = "1970-01-01T00:00:00.000000+00:00"


def _utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _set_at(kind: Kind, row: dict[str, Any]) -> str:
    """When the row says it was set, in the form records are compared in."""
    raw = row.get(kind.stamp)
    if kind.name == "question" and row.get("answered_at"):
        raw = row["answered_at"]
    try:
        return _utc(datetime.fromisoformat(str(raw)))
    except (TypeError, ValueError):
        return _EPOCH


def _record_key(kind: Kind, row: dict[str, Any]) -> str:
    return json.dumps([kind.name, *(row[c] for c in kind.key)], separators=(",", ":"))


def _kind_of(key: str) -> Kind | None:
    try:
        return _BY_NAME.get(json.loads(key)[0])
    except (ValueError, IndexError, TypeError):
        return None


@dataclass(slots=True)
class Record:
    row: dict[str, Any] | None  # None: deleted
    at: str
    by: str

    def newer_than(self, other: Record) -> bool:
        return (self.at, self.by) > (other.at, other.by)

    def to_json(self) -> dict[str, Any]:
        return {"row": self.row, "at": self.at, "by": self.by}

    @staticmethod
    def from_json(raw: Any) -> Record | None:
        if not isinstance(raw, dict):
            return None
        if not isinstance(raw.get("at"), str) or not isinstance(raw.get("by"), str):
            return None
        row = raw.get("row")
        if row is not None and not isinstance(row, dict):
            return None
        return Record(row, raw["at"], raw["by"])


# --------------------------------------------------------------------------- #
# Encryption
# --------------------------------------------------------------------------- #


class SyncFileError(ValueError):
    """A file in the folder that this device cannot use. The message is safe to show."""


def _key(secret: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=_INFO).derive(secret.encode())


def seal(payload: dict[str, Any], secret: str, device: str) -> bytes:
    nonce = os.urandom(12)
    plain = zlib.compress(json.dumps(payload, separators=(",", ":")).encode())
    data = AESGCM(_key(secret)).encrypt(nonce, plain, _AAD + device.encode())
    envelope = {
        "tastydesk_sync": FORMAT,
        "device": device,
        "nonce": base64.b64encode(nonce).decode(),
        "data": base64.b64encode(data).decode(),
    }
    return json.dumps(envelope).encode()


def unseal(blob: bytes, secret: str) -> dict[str, Any]:
    try:
        envelope = json.loads(blob)
        device = str(envelope["device"])
        version = int(envelope["tastydesk_sync"])
        nonce = base64.b64decode(envelope["nonce"])
        data = base64.b64decode(envelope["data"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SyncFileError("not a Tasty Desk sync file") from exc
    if version > FORMAT:
        raise SyncFileError("written by a newer Tasty Desk; update this one")
    try:
        plain = AESGCM(_key(secret)).decrypt(nonce, data, _AAD + device.encode())
    except InvalidTag as exc:
        raise SyncFileError("locked with a different client secret") from exc
    payload = json.loads(zlib.decompress(plain))
    if payload.get("device") != device:
        raise SyncFileError("its contents do not match its name")
    return payload


# --------------------------------------------------------------------------- #
# The tables
# --------------------------------------------------------------------------- #


async def device_id(db: Database) -> str:
    current = await db.get_setting(DEVICE_SETTING)
    if current:
        return current
    new = secrets.token_hex(8)
    await db.set_setting(DEVICE_SETTING, new)
    return new


async def accounts(db: Database) -> list[str]:
    sql = (
        "SELECT account_number FROM transactions UNION SELECT account_number FROM strategies"
    )
    async with db.connection.execute(sql) as cur:
        return sorted({str(row[0]) async for row in cur if row[0]})


async def local_records(db: Database) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        sql = f"SELECT {', '.join(kind.columns)} FROM {kind.table}"  # noqa: S608 - names are constants
        async with db.connection.execute(sql) as cur:
            async for raw in cur:
                row = {c: raw[i] for i, c in enumerate(kind.columns)}
                if not _device_local(kind, row):
                    out[_record_key(kind, row)] = row
    return out


async def known_records(db: Database) -> dict[str, Record]:
    out: dict[str, Record] = {}
    async with db.connection.execute("SELECT key, row, at, by FROM sync_records") as cur:
        async for key, row, at, by in cur:
            out[key] = Record(json.loads(row) if row is not None else None, at, by)
    return out


async def _write_known(db: Database, records: dict[str, Record], keys: set[str]) -> None:
    await db.connection.executemany(
        "INSERT INTO sync_records (key, row, at, by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET row = excluded.row, at = excluded.at, by = excluded.by",
        [
            (k, None if r.row is None else json.dumps(r.row), r.at, r.by)
            for k, r in sorted((k, records[k]) for k in keys)
        ],
    )


async def _apply(db: Database, kind: Kind, key: str, row: dict[str, Any] | None) -> bool:
    """Make the table hold ``row`` for this record (or nothing). False if it cannot yet."""
    conn = db.connection
    values = json.loads(key)[1:]
    where = " AND ".join(f"{c} = ?" for c in kind.key)
    if row is None:
        await conn.execute(f"DELETE FROM {kind.table} WHERE {where}", values)  # noqa: S608
        return True
    if kind.name == "member":
        async with conn.execute("SELECT 1 FROM named_strategies WHERE id = ?", (row["strategy_id"],)) as cur:
            if await cur.fetchone() is None:
                return False
    columns = ", ".join(kind.columns)
    marks = ", ".join("?" for _ in kind.columns)
    params = [row.get(c) for c in kind.columns]
    if kind.unique:
        updates = ", ".join(f"{c} = excluded.{c}" for c in kind.columns if c not in kind.key)
        await conn.execute(
            f"INSERT INTO {kind.table} ({columns}) VALUES ({marks}) "  # noqa: S608
            f"ON CONFLICT ({', '.join(kind.key)}) DO UPDATE SET {updates}",
            params,
        )
    else:
        await conn.execute(f"DELETE FROM {kind.table} WHERE {where}", values)  # noqa: S608
        await conn.execute(f"INSERT INTO {kind.table} ({columns}) VALUES ({marks})", params)  # noqa: S608
    return True


# --------------------------------------------------------------------------- #
# One step
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Step:
    #: This device's file, to write as ``filename``; None when nothing changed.
    outgoing: bytes | None
    filename: str
    #: Kinds that arrived from another device and were written here.
    applied: set[str] = field(default_factory=set)
    report: dict[str, Any] = field(default_factory=dict)


async def step(
    db: Database,
    *,
    secret: str,
    label: str,
    incoming: dict[str, bytes],
    now: datetime | None = None,
) -> Step:
    """Record what changed here, take in the other devices' files, and say what to write.

    ``incoming`` is every ``*.tdsync`` file in the folder by name, this
    device's own included or not.
    """
    device = await device_id(db)
    filename = device + SUFFIX
    stamp = _utc(now or datetime.now(UTC))
    mine = await accounts(db)
    report: dict[str, Any] = {"device": device, "label": label, "at": stamp, "devices": [], "problems": []}
    if not mine:
        report["state"] = "waiting"
        report["problems"].append("No tastytrade account yet: sync with tastytrade first.")
        return Step(None, filename, report=report)

    local = await local_records(db)
    known = await known_records(db)

    # 1. What changed on this device since the last step. On its very first
    #    step a device has no last step, so a row counts from when it says it
    #    was set: a phone joining late must not have its old defaults read as
    #    edits made now, over what was set on the Mac since.
    first = not known
    touched: set[str] = set()
    for key in local.keys() | known.keys():
        row = local.get(key)
        before = known.get(key)
        if before is None and row is None:
            continue
        if before is None or before.row != row:
            kind = _kind_of(key)
            at = _set_at(kind, row) if first and row is not None and kind is not None else stamp
            known[key] = Record(row, at, device)
            touched.add(key)
    report["sent"] = len(touched)

    # 2. The latest of every record, from every device on the same account.
    arrived: set[str] = set()
    for name, blob in sorted(incoming.items()):
        if name == filename:
            continue
        try:
            payload = unseal(blob, secret)
        except SyncFileError as exc:
            report["problems"].append(f"{name}: {exc}.")
            continue
        theirs = [str(a) for a in payload.get("accounts", [])]
        entry = {
            "label": str(payload.get("label") or "another device"),
            "written_at": payload.get("written_at"),
            "same_account": bool(set(theirs) & set(mine)),
        }
        report["devices"].append(entry)
        if not entry["same_account"]:
            report["problems"].append(f"{entry['label']}: a different tastytrade account, left alone.")
            continue
        for key, raw in (payload.get("records") or {}).items():
            record = Record.from_json(raw)
            if record is None:
                continue
            current = known.get(key)
            if current is None or record.newer_than(current):
                known[key] = record
                arrived.add(key)

    # 3. Write what arrived into the tables, parents before children and
    #    children's deletions before their parents'.
    order = {k.name: i for i, k in enumerate(KINDS)}
    changes = []
    for key in arrived:
        kind = _kind_of(key)
        if kind is not None and known[key].row != local.get(key):
            changes.append((kind, key, known[key].row))
    changes.sort(key=lambda c: (order[c[0].name] if c[2] is not None else -order[c[0].name], c[1]))
    applied: set[str] = set()
    for kind, key, row in changes:
        if await _apply(db, kind, key, row):
            applied.add(kind.name)
    await _write_known(db, known, touched | arrived)
    await db.connection.commit()
    report["received"] = len(changes)
    report["state"] = "ok"

    # 4. This device's file: everything it knows, so a device that joins later
    #    learns it all from whichever file it reads.
    outgoing = None
    if touched or arrived or filename not in incoming:
        payload = {
            "format": FORMAT,
            "device": device,
            "label": label,
            "accounts": mine,
            "written_at": stamp,
            "records": {k: r.to_json() for k, r in sorted(known.items())},
        }
        outgoing = seal(payload, secret, device)
    return Step(outgoing, filename, applied, report)
