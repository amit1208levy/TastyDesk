"""Local persistence for Tasty Desk.

Everything the dashboard knows lives in one SQLite file in the user's
Application Support directory. There is no server, no cloud copy, and no
telemetry: the file holds the user's complete trading history, so it is created
inside a 0o700 directory and chmod'ed to 0o600.

Money is stored as TEXT and converted through :class:`~decimal.Decimal` on the
way in and on the way out. SQLite's REAL type is IEEE-754 binary floating
point, which cannot represent 0.1 or 1234.56 exactly; a few thousand round
trips through it silently turns a P&L report into fiction. TEXT plus Decimal
round-trips bit-for-bit, including the number of decimal places, which also
keeps "$3.00 credit" from becoming "$3".

The consequence is that money columns must never be compared or aggregated by
SQL: ``MIN(open_pnl)`` on TEXT sorts lexicographically, so "-90" would beat
"-150". Every aggregate over money in this module is therefore reduced in
Python, in Decimal space.

Why snapshots exist
-------------------
tastytrade tells you where a position is now; it will not tell you how bad it
got last Thursday. The ``snapshots`` table is the only record of the path a
trade took, and it is what makes max adverse excursion — and therefore the
"did I actually honour my 2x stop?" report — computable at all. Nothing else
in the system can reconstruct it after the fact.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite

from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "Database",
    "SCHEMA_VERSION",
]

DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "TastyDesk" / "tastydesk.db"


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #
# An ordered list of (version, script). Migrations are applied in order and
# recorded, so a schema change in a later release never costs the user their
# history — the one thing in this application that cannot be re-downloaded in
# full from tastytrade (the API's transaction window is not infinite, and
# snapshots exist nowhere but here).

logger = logging.getLogger(__name__)


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS transactions (
    id                    INTEGER PRIMARY KEY,
    account_number        TEXT NOT NULL,
    transaction_type      TEXT,
    transaction_sub_type  TEXT,
    description           TEXT,
    executed_at           TEXT,
    transaction_date      TEXT,
    value                 TEXT,
    net_value             TEXT,
    symbol                TEXT,
    instrument_type       TEXT,
    underlying_symbol     TEXT,
    action                TEXT,
    quantity              TEXT,
    price                 TEXT,
    regulatory_fees       TEXT,
    clearing_fees         TEXT,
    commission            TEXT,
    order_id              INTEGER,
    leg_count             INTEGER,
    lots                  TEXT,
    reverses_id           INTEGER
);
CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions (transaction_date);
CREATE INDEX IF NOT EXISTS ix_transactions_order ON transactions (order_id);
CREATE INDEX IF NOT EXISTS ix_transactions_underlying ON transactions (underlying_symbol);

CREATE TABLE IF NOT EXISTS strategies (
    id                        TEXT PRIMARY KEY,
    account_number            TEXT NOT NULL,
    underlying                TEXT NOT NULL,
    strategy_type             TEXT NOT NULL,
    risk_profile              TEXT NOT NULL,
    legs                      TEXT NOT NULL,
    opened_at                 TEXT NOT NULL,
    closed_at                 TEXT,
    net_credit                TEXT NOT NULL,
    closing_cash_flow         TEXT NOT NULL,
    fees                      TEXT NOT NULL,
    order_ids                 TEXT NOT NULL,
    roll_count                INTEGER NOT NULL DEFAULT 0,
    iv_rank_at_entry          TEXT,
    underlying_price_at_entry TEXT,
    dte_at_entry              INTEGER,
    short_delta_at_entry      TEXT,
    buying_power_used         TEXT,
    notes                     TEXT,
    manual_group              INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_strategies_open ON strategies (closed_at);
CREATE INDEX IF NOT EXISTS ix_strategies_underlying ON strategies (underlying);

CREATE TABLE IF NOT EXISTS snapshots (
    strategy_id       TEXT NOT NULL REFERENCES strategies (id) ON DELETE CASCADE,
    as_of             TEXT NOT NULL,
    mark_value        TEXT,
    open_pnl          TEXT,
    pct_of_credit     TEXT,
    underlying_price  TEXT,
    worst_short_delta TEXT,
    PRIMARY KEY (strategy_id, as_of)
);
CREATE INDEX IF NOT EXISTS ix_snapshots_as_of ON snapshots (as_of);

CREATE TABLE IF NOT EXISTS manual_overrides (
    strategy_id TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS underlying_metrics (
    symbol            TEXT PRIMARY KEY,
    as_of             TEXT NOT NULL,
    price             TEXT,
    iv                TEXT,
    iv_rank           TEXT,
    iv_percentile     TEXT,
    hv_30_day         TEXT,
    beta              TEXT,
    liquidity_rating  INTEGER,
    earnings_date     TEXT,
    ex_dividend_date  TEXT
);
"""

_MIGRATION_2 = """
-- The tastytrade Transaction model requires is-estimated-fee, so a row stored
-- without it can never be rehydrated: model_validate rejects every one. That
-- turned each incremental sync into a rebuild from a few days of history, which
-- silently emptied the whole journal. The two fee columns are read by
-- grouping._fee_total and were being lost the same way -- index-option fees
-- matter to anyone trading SPX.
ALTER TABLE transactions ADD COLUMN is_estimated_fee INTEGER;
ALTER TABLE transactions ADD COLUMN proprietary_index_option_fees TEXT;
ALTER TABLE transactions ADD COLUMN other_charge TEXT;
ALTER TABLE transactions ADD COLUMN other_charge_description TEXT;
"""

_MIGRATION_3 = """
-- A trade taken away by assignment captured nothing, and analytics needs to
-- know that to stop counting an assigned put as a full-credit win.
ALTER TABLE strategies ADD COLUMN closed_by_assignment INTEGER NOT NULL DEFAULT 0;
"""

_MIGRATION_4 = """
-- Questions typed into the dashboard, and the answers written back.
--
-- A queue rather than a live call: the app holds no Anthropic key, so there is
-- nothing for it to ask. A Claude session picks these up, reads the same fact
-- sheet the brief uses, and writes the answer here. Asynchronous is the honest
-- shape, and the UI says so rather than pretending to be a chat window.
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at    TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT,
    answered_at TEXT,
    context     TEXT
);
CREATE INDEX IF NOT EXISTS ix_questions_open ON questions (answered_at, asked_at);
"""

_MIGRATION_5 = """
-- What actually happened in this application, in order.
--
-- Without this, the only way to know whether a sync failed, whether a position
-- crossed a rule, or whether a question went unanswered is to be watching at
-- the moment it happens. The log is also how "has this already been noticed?"
-- is answered: a crossing emits one event and never nags again.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'info',
    summary     TEXT NOT NULL,
    strategy_id TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_at ON events (at DESC);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events (kind, at DESC);
CREATE INDEX IF NOT EXISTS ix_events_strategy ON events (strategy_id, kind);
"""

_MIGRATION_6 = """
-- Closed by expiry with no closing transaction to confirm the outcome. The
-- recorded cash flows may be missing an exercise, so these are kept out of
-- realized totals rather than letting one guess move a year's figures.
ALTER TABLE strategies ADD COLUMN outcome_unverified INTEGER NOT NULL DEFAULT 0;
"""

_MIGRATION_7 = """
-- Answers about which legs belong to the same trade, kept as patterns rather
-- than as one-offs. Confirming "an IWM long LEAP call with a short near-dated
-- call is one diagonal" settles every pair shaped like it, which is what keeps
-- a few hundred trades down to a handful of questions.
CREATE TABLE IF NOT EXISTS pairing_rules (
    pattern    TEXT PRIMARY KEY,
    decision   TEXT NOT NULL CHECK (decision IN ('merge', 'separate')),
    decided_at TEXT NOT NULL,
    note       TEXT
);
"""

_MIGRATION_8 = """
-- Strategies the user defined by hand and named, plus the trades they hold.
--
-- The broker knows which legs shared an order. It does not know that you call
-- four of them "my /ZB 112 straddle" and have put the same trade on eleven
-- times since March. This is where that knowledge lives.
CREATE TABLE IF NOT EXISTS named_strategies (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    product    TEXT NOT NULL,
    signature  TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS named_strategy_members (
    strategy_id TEXT NOT NULL REFERENCES named_strategies (id) ON DELETE CASCADE,
    trade_id    TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    confirmed   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (strategy_id, trade_id)
);
CREATE INDEX IF NOT EXISTS ix_named_members ON named_strategy_members (trade_id);
"""

_MIGRATION_9 = """
-- Preferences the user sets once and expects to stay set.
--
-- The confidence bar for counting an old trade into a strategy lives here
-- rather than in the page, because it is a decision about how his journal is
-- read, not a control to be nudged while looking at a chart.
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    set_at  TEXT NOT NULL
);
"""

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
    (3, _MIGRATION_3),
    (4, _MIGRATION_4),
    (5, _MIGRATION_5),
    (6, _MIGRATION_6),
    (7, _MIGRATION_7),
    (8, _MIGRATION_8),
    (9, _MIGRATION_9),
)

SCHEMA_VERSION = _MIGRATIONS[-1][0]


# --------------------------------------------------------------------------- #
# Conversion helpers
# --------------------------------------------------------------------------- #


def _money_out(value: Decimal | int | str | float | None) -> str | None:
    """Decimal -> TEXT. ``str(Decimal)`` is exact, including trailing zeros."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    # Anything else is coerced via str() first: Decimal(float) would drag the
    # float's binary error into storage, which is the failure this whole
    # TEXT-for-money scheme exists to prevent.
    return str(Decimal(str(value)))


def _money_in(value: str | None) -> Decimal | None:
    """TEXT -> Decimal."""
    if value is None or value == "":
        return None
    return Decimal(value)


def _enum_out(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _dt_out(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_in(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _date_out(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_in(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _leg_to_dict(leg: Leg) -> dict[str, Any]:
    """Serialise a leg for the strategies.legs JSON blob.

    Decimals become strings here too: json.dumps would otherwise refuse them,
    and float(leg.strike) would quietly turn a 2.5 strike into 2.4999999.
    """
    return {
        "symbol": leg.symbol,
        "instrument_type": leg.instrument_type,
        "underlying": leg.underlying,
        "direction": leg.direction.value,
        "quantity": _money_out(leg.quantity),
        "multiplier": _money_out(leg.multiplier),
        "option_type": leg.option_type.value if leg.option_type is not None else None,
        "strike": _money_out(leg.strike),
        "expiration": _date_out(leg.expiration),
        "open_price": _money_out(leg.open_price),
        "mark": _money_out(leg.mark),
        "bid": _money_out(leg.bid),
        "ask": _money_out(leg.ask),
        "delta": _money_out(leg.delta),
        "gamma": _money_out(leg.gamma),
        "theta": _money_out(leg.theta),
        "vega": _money_out(leg.vega),
        "iv": _money_out(leg.iv),
    }


def _leg_from_dict(raw: dict[str, Any]) -> Leg:
    option_type = raw.get("option_type")
    return Leg(
        symbol=raw["symbol"],
        instrument_type=raw["instrument_type"],
        underlying=raw["underlying"],
        direction=Direction(raw["direction"]),
        quantity=_money_in(raw["quantity"]) or Decimal(0),
        multiplier=_money_in(raw["multiplier"]) or Decimal(100),
        option_type=OptionType(option_type) if option_type else None,
        strike=_money_in(raw.get("strike")),
        expiration=_date_in(raw.get("expiration")),
        open_price=_money_in(raw.get("open_price")) or Decimal(0),
        mark=_money_in(raw.get("mark")),
        bid=_money_in(raw.get("bid")),
        ask=_money_in(raw.get("ask")),
        delta=_money_in(raw.get("delta")),
        gamma=_money_in(raw.get("gamma")),
        theta=_money_in(raw.get("theta")),
        vega=_money_in(raw.get("vega")),
        iv=_money_in(raw.get("iv")),
    )


def _lots_out(lots: Any) -> str | None:
    """Lots are pydantic models; keep them as opaque JSON for the audit trail."""
    if not lots:
        return None
    plain = [lot.model_dump(mode="json") if hasattr(lot, "model_dump") else lot for lot in lots]
    return json.dumps(plain, default=str)


# Columns in ``transactions`` that hold money or quantities and therefore have
# to come back out as Decimal rather than str.
_TX_DECIMAL_COLUMNS = (
    "value",
    "net_value",
    "quantity",
    "price",
    "regulatory_fees",
    "clearing_fees",
    "commission",
    "proprietary_index_option_fees",
    "other_charge",
)

_TX_COLUMNS = (
    "id",
    "account_number",
    "transaction_type",
    "transaction_sub_type",
    "description",
    "executed_at",
    "transaction_date",
    "value",
    "net_value",
    "symbol",
    "instrument_type",
    "underlying_symbol",
    "action",
    "quantity",
    "price",
    "regulatory_fees",
    "clearing_fees",
    "commission",
    "order_id",
    "leg_count",
    "lots",
    "reverses_id",
    "is_estimated_fee",
    "proprietary_index_option_fees",
    "other_charge",
    "other_charge_description",
)


def _bool_out(value: Any) -> int | None:
    """SQLite has no boolean type; store 0/1 and keep None distinguishable."""
    if value is None:
        return None
    return 1 if value else 0


def _tx_row(tx: Any) -> tuple[Any, ...]:
    """Flatten one tastytrade ``Transaction`` into a storable row.

    Read with ``getattr`` defaults rather than attribute access: the SDK adds
    and renames optional fields between releases, and one unknown attribute
    must never abort a whole history sync.
    """
    return (
        int(tx.id),
        str(tx.account_number),
        _enum_out(getattr(tx, "transaction_type", None)),
        _enum_out(getattr(tx, "transaction_sub_type", None)),
        getattr(tx, "description", None),
        _dt_out(getattr(tx, "executed_at", None)),
        _date_out(getattr(tx, "transaction_date", None)),
        _money_out(getattr(tx, "value", None)),
        _money_out(getattr(tx, "net_value", None)),
        getattr(tx, "symbol", None),
        _enum_out(getattr(tx, "instrument_type", None)),
        getattr(tx, "underlying_symbol", None),
        _enum_out(getattr(tx, "action", None)),
        _money_out(getattr(tx, "quantity", None)),
        _money_out(getattr(tx, "price", None)),
        _money_out(getattr(tx, "regulatory_fees", None)),
        _money_out(getattr(tx, "clearing_fees", None)),
        _money_out(getattr(tx, "commission", None)),
        getattr(tx, "order_id", None),
        getattr(tx, "leg_count", None),
        _lots_out(getattr(tx, "lots", None)),
        getattr(tx, "reverses_id", None),
        _bool_out(getattr(tx, "is_estimated_fee", None)),
        _money_out(getattr(tx, "proprietary_index_option_fees", None)),
        _money_out(getattr(tx, "other_charge", None)),
        getattr(tx, "other_charge_description", None),
    )


def _upsert_sql(table: str, columns: Sequence[str], key: Sequence[str]) -> str:
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in key)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
    )


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


class Database:
    """Async SQLite store. One instance per process; call :meth:`connect` first.

    ``save_snapshot`` requires the strategy row to exist (a foreign key), so a
    sync should always persist strategies before snapshotting them.
    """

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self._in_memory = str(path).startswith(":memory:")
        self._conn: aiosqlite.Connection | None = None

    # -- lifecycle --------------------------------------------------------- #

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; await connect() first")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        if not self._in_memory:
            # 0o700 on the directory as well as 0o600 on the file: the WAL and
            # shared-memory sidecars carry the same trade data as the database.
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = await aiosqlite.connect(str(self.path))
        conn.row_factory = aiosqlite.Row
        # WAL lets the web UI read while a sync writes, instead of throwing
        # "database is locked" at whichever one lost the race.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        self._conn = conn
        self._harden_permissions()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Database:
        await self.connect()
        await self.migrate()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _harden_permissions(self) -> None:
        if self._in_memory:
            return
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                try:
                    os.chmod(candidate, 0o600)
                except OSError:
                    # A file on a filesystem without POSIX modes is not worth
                    # failing a sync over; the directory mode still applies.
                    pass

    # -- schema ------------------------------------------------------------ #

    async def migrate(self) -> None:
        """Apply any migrations this database has not seen. Safe to call twice."""
        conn = self.connection
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        await conn.commit()
        async with conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cur:
            row = await cur.fetchone()
        current = int(row[0]) if row else 0

        for version, script in _MIGRATIONS:
            if version <= current:
                continue
            await conn.executescript(script)
            await conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now().astimezone().isoformat()),
            )
            await conn.commit()
        self._harden_permissions()

    async def schema_version(self) -> int:
        async with self.connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # -- transactions ------------------------------------------------------ #

    async def upsert_transactions(self, rows: Sequence[Any]) -> int:
        """Insert or refresh transactions, keyed on the tastytrade id.

        Returns the number of rows that were *new*, not the number written.
        Every sync fetches the same ninety-day window, so counting writes meant
        reporting "31 new transactions" on a sync that had found nothing --
        harmless when syncing was a button the user pressed, a lie once the app
        started syncing on its own every few minutes.
        """
        if not rows:
            return 0
        conn = self.connection
        ids = [_tx_row(tx)[0] for tx in rows]
        known: set[Any] = set()
        # Chunked: the window can hold thousands of rows and SQLite has a cap
        # on how many variables one statement may bind.
        for start in range(0, len(ids), 400):
            chunk = ids[start : start + 400]
            marks = ",".join("?" * len(chunk))
            async with conn.execute(
                f"SELECT id FROM transactions WHERE id IN ({marks})", chunk
            ) as cur:
                async for row in cur:
                    known.add(row[0])
        fresh = sum(1 for i in ids if i not in known)

        sql = _upsert_sql("transactions", _TX_COLUMNS, ("id",))
        await conn.executemany(sql, [_tx_row(tx) for tx in rows])
        await conn.commit()
        return fresh

    async def get_transactions(
        self, since: date | None = None, account_number: str | None = None
    ) -> list[dict[str, Any]]:
        """Stored transactions, oldest first, with money back in Decimal form.

        Filter by account when rebuilding: a trade is reconstructed within one
        account, and feeding two accounts' fills to the same pass would let a
        close in one attach to an open in the other.
        """
        sql = f"SELECT {', '.join(_TX_COLUMNS)} FROM transactions"
        clauses: list[str] = []
        params_list: list[Any] = []
        if since is not None:
            # transaction_date is stored ISO, so lexical >= is chronological >=.
            clauses.append("transaction_date >= ?")
            params_list.append(since.isoformat())
        if account_number is not None:
            clauses.append("account_number = ?")
            params_list.append(account_number)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        params: tuple[Any, ...] = tuple(params_list)
        sql += " ORDER BY transaction_date, executed_at, id"

        out: list[dict[str, Any]] = []
        async with self.connection.execute(sql, params) as cur:
            async for row in cur:
                item = dict(row)
                for col in _TX_DECIMAL_COLUMNS:
                    item[col] = _money_in(item[col])
                item["executed_at"] = _dt_in(item["executed_at"])
                item["transaction_date"] = _date_in(item["transaction_date"])
                item["lots"] = json.loads(item["lots"]) if item["lots"] else None
                # The SDK's Transaction model requires this field, so it has to
                # come back as a real bool rather than SQLite's 0/1.
                flag = item.get("is_estimated_fee")
                item["is_estimated_fee"] = bool(flag) if flag is not None else False
                out.append(item)
        return out

    async def last_transaction_date(self, account_number: str | None = None) -> date | None:
        """Newest stored transaction date — the watermark an incremental sync resumes from.

        Per account: one dormant account would otherwise hold the watermark back
        to its last trade years ago, or a busy one would push it past an account
        that has not traded since.
        """
        sql = "SELECT MAX(transaction_date) FROM transactions"
        params: tuple[Any, ...] = ()
        if account_number is not None:
            sql += " WHERE account_number = ?"
            params = (account_number,)
        async with self.connection.execute(sql, params) as cur:
            row = await cur.fetchone()
        return _date_in(row[0]) if row and row[0] else None

    # -- named strategies --------------------------------------------------- #

    async def save_named_strategy(self, row: dict[str, Any]) -> None:
        conn = self.connection
        await conn.execute(
            "INSERT INTO named_strategies (id, name, product, signature, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name, product = excluded.product, "
            "signature = excluded.signature, note = excluded.note",
            (
                row["id"],
                row["name"],
                row["product"],
                json.dumps(row["signature"]),
                row.get("note"),
                _dt_out(datetime.now(UTC)),
            ),
        )
        await conn.commit()

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self.connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.connection.execute(
            "INSERT INTO settings (key, value, set_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, set_at = excluded.set_at",
            (key, value, _dt_out(datetime.now(UTC))),
        )
        await self.connection.commit()

    async def all_settings(self) -> dict[str, str]:
        async with self.connection.execute("SELECT key, value FROM settings") as cur:
            return {row["key"]: row["value"] async for row in cur}

    async def set_named_members(
        self, strategy_id: str, trade_ids: Sequence[str], confirmed: bool = True
    ) -> None:
        conn = self.connection
        now = _dt_out(datetime.now(UTC))
        await conn.executemany(
            "INSERT INTO named_strategy_members (strategy_id, trade_id, added_at, confirmed) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (strategy_id, trade_id) DO UPDATE SET "
            "confirmed = excluded.confirmed",
            [(strategy_id, tid, now, 1 if confirmed else 0) for tid in trade_ids],
        )
        await conn.commit()

    async def remove_named_member(self, strategy_id: str, trade_id: str) -> bool:
        conn = self.connection
        cur = await conn.execute(
            "DELETE FROM named_strategy_members WHERE strategy_id = ? AND trade_id = ?",
            (strategy_id, trade_id),
        )
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def get_named_strategies(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async with self.connection.execute(
            "SELECT id, name, product, signature, note, created_at FROM named_strategies ORDER BY created_at"
        ) as cur:
            async for row in cur:
                item = dict(row)
                item["signature"] = json.loads(item["signature"])
                item["created_at"] = _dt_in(item["created_at"])
                item["member_ids"] = []
                out.append(item)

        members: dict[str, list[str]] = {}
        async with self.connection.execute(
            "SELECT strategy_id, trade_id FROM named_strategy_members ORDER BY added_at"
        ) as cur:
            async for row in cur:
                members.setdefault(row["strategy_id"], []).append(row["trade_id"])

        for item in out:
            item["member_ids"] = members.get(item["id"], [])
        return out

    async def delete_named_strategy(self, strategy_id: str) -> bool:
        conn = self.connection
        await conn.execute("DELETE FROM named_strategy_members WHERE strategy_id = ?", (strategy_id,))
        cur = await conn.execute("DELETE FROM named_strategies WHERE id = ?", (strategy_id,))
        await conn.commit()
        return (cur.rowcount or 0) > 0

    # -- pairing rules ------------------------------------------------------ #

    async def set_pairing_rule(self, pattern: str, decision: str, note: str | None = None) -> None:
        if decision not in ("merge", "separate"):
            raise ValueError(f"decision must be 'merge' or 'separate', got {decision!r}")
        conn = self.connection
        await conn.execute(
            "INSERT INTO pairing_rules (pattern, decision, decided_at, note) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (pattern) DO UPDATE SET decision = excluded.decision, "
            "decided_at = excluded.decided_at, note = excluded.note",
            (pattern, decision, _dt_out(datetime.now(UTC)), note),
        )
        await conn.commit()

    async def get_pairing_rules(self) -> dict[str, str]:
        out: dict[str, str] = {}
        async with self.connection.execute("SELECT pattern, decision FROM pairing_rules") as cur:
            async for row in cur:
                out[row["pattern"]] = row["decision"]
        return out

    async def get_pairing_decisions(self) -> list[dict[str, Any]]:
        """Answers already given, newest first, with what was decided about."""
        out: list[dict[str, Any]] = []
        async with self.connection.execute(
            "SELECT pattern, decision, decided_at, note FROM pairing_rules ORDER BY decided_at DESC"
        ) as cur:
            async for row in cur:
                item = dict(row)
                item["decided_at"] = _dt_in(item["decided_at"])
                out.append(item)
        return out

    async def clear_pairing_rule(self, pattern: str) -> bool:
        conn = self.connection
        cur = await conn.execute("DELETE FROM pairing_rules WHERE pattern = ?", (pattern,))
        await conn.commit()
        return (cur.rowcount or 0) > 0

    # -- events ------------------------------------------------------------ #

    async def record(
        self,
        kind: str,
        summary: str,
        *,
        severity: str = "info",
        strategy_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Append one event. Never raises into the caller.

        Recording must not be able to break the thing it is recording, so a
        failure here is logged and swallowed — an application that crashes
        while writing its own audit trail is worse than one with a gap in it.
        """
        try:
            conn = self.connection
            cur = await conn.execute(
                "INSERT INTO events (at, kind, severity, summary, strategy_id, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _dt_out(datetime.now(UTC)),
                    kind,
                    severity,
                    summary,
                    strategy_id,
                    json.dumps(detail) if detail else None,
                ),
            )
            await conn.commit()
            return int(cur.lastrowid or 0)
        except Exception:
            logger.warning("Could not record event %s", kind, exc_info=True)
            return 0

    async def events(
        self,
        *,
        limit: int = 100,
        since: datetime | None = None,
        kinds: Sequence[str] | None = None,
        min_severity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first."""
        sql = "SELECT id, at, kind, severity, summary, strategy_id, detail FROM events WHERE 1=1"
        params: list[Any] = []
        if since is not None:
            sql += " AND at > ?"
            params.append(_dt_out(since))
        if kinds:
            sql += f" AND kind IN ({', '.join('?' for _ in kinds)})"
            params.extend(kinds)
        if min_severity:
            rank = {"info": 0, "notable": 1, "warning": 2, "error": 3}
            allowed = [k for k, v in rank.items() if v >= rank.get(min_severity, 0)]
            sql += f" AND severity IN ({', '.join('?' for _ in allowed)})"
            params.extend(allowed)
        sql += " ORDER BY at DESC, id DESC LIMIT ?"
        params.append(limit)

        out: list[dict[str, Any]] = []
        async with self.connection.execute(sql, tuple(params)) as cur:
            async for row in cur:
                item = dict(row)
                item["at"] = _dt_in(item["at"])
                item["detail"] = json.loads(item["detail"]) if item["detail"] else None
                out.append(item)
        return out

    async def has_noticed(self, kind: str, strategy_id: str) -> bool:
        """True when this crossing has already been logged for this strategy.

        The reason a position that hits its profit target is announced once
        rather than on every refresh for the next three weeks.
        """
        async with self.connection.execute(
            "SELECT 1 FROM events WHERE kind = ? AND strategy_id = ? LIMIT 1",
            (kind, strategy_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def forget_notices(self, strategy_id: str) -> int:
        """Clear a strategy's crossings, so a reopened position can cross again."""
        conn = self.connection
        cur = await conn.execute(
            "DELETE FROM events WHERE strategy_id = ? AND kind LIKE 'position.%'", (strategy_id,)
        )
        await conn.commit()
        return cur.rowcount or 0

    async def event_counts(self, since: datetime | None = None) -> dict[str, int]:
        sql = "SELECT kind, COUNT(*) AS n FROM events"
        params: tuple[Any, ...] = ()
        if since is not None:
            sql += " WHERE at > ?"
            params = (_dt_out(since),)
        sql += " GROUP BY kind ORDER BY n DESC"
        out: dict[str, int] = {}
        async with self.connection.execute(sql, params) as cur:
            async for row in cur:
                out[row["kind"]] = int(row["n"])
        return out

    # -- questions --------------------------------------------------------- #

    async def ask(self, question: str, context: str | None = None) -> int:
        """Queue a question. Returns its id."""
        conn = self.connection
        cur = await conn.execute(
            "INSERT INTO questions (asked_at, question, context) VALUES (?, ?, ?)",
            (_dt_out(datetime.now(UTC)), question.strip(), context),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)

    async def pending_questions(self) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, asked_at, question, context FROM questions "
            "WHERE answered_at IS NULL ORDER BY asked_at"
        )
        out: list[dict[str, Any]] = []
        async with self.connection.execute(sql) as cur:
            async for row in cur:
                item = dict(row)
                item["asked_at"] = _dt_in(item["asked_at"])
                out.append(item)
        return out

    async def answer_question(self, question_id: int, answer: str) -> bool:
        conn = self.connection
        cur = await conn.execute(
            "UPDATE questions SET answer = ?, answered_at = ? WHERE id = ? AND answered_at IS NULL",
            (answer, _dt_out(datetime.now(UTC)), question_id),
        )
        await conn.commit()
        return (cur.rowcount or 0) > 0

    async def question_thread(self, limit: int = 30) -> list[dict[str, Any]]:
        """Newest first, so the dashboard shows the latest exchange at the top."""
        sql = (
            "SELECT id, asked_at, question, answer, answered_at FROM questions ORDER BY asked_at DESC LIMIT ?"
        )
        out: list[dict[str, Any]] = []
        async with self.connection.execute(sql, (limit,)) as cur:
            async for row in cur:
                item = dict(row)
                item["asked_at"] = _dt_in(item["asked_at"])
                item["answered_at"] = _dt_in(item["answered_at"]) if item["answered_at"] else None
                out.append(item)
        return out

    async def delete_question(self, question_id: int) -> bool:
        conn = self.connection
        cur = await conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        await conn.commit()
        return (cur.rowcount or 0) > 0

    # -- strategies -------------------------------------------------------- #

    _STRATEGY_COLUMNS = (
        "id",
        "account_number",
        "underlying",
        "strategy_type",
        "risk_profile",
        "legs",
        "opened_at",
        "closed_at",
        "net_credit",
        "closing_cash_flow",
        "fees",
        "order_ids",
        "roll_count",
        "iv_rank_at_entry",
        "underlying_price_at_entry",
        "dte_at_entry",
        "short_delta_at_entry",
        "buying_power_used",
        "notes",
        "manual_group",
        "closed_by_assignment",
        "outcome_unverified",
    )

    async def save_strategies(
        self, strategies: Sequence[Strategy], *, reconcile_account: str | None = None
    ) -> None:
        """Upsert whole strategies, legs and all, in one transaction.

        Pass ``reconcile_account`` after a full rebuild to also delete that
        account's rows that are no longer in the set. Without it, a strategy
        that later gets absorbed into another -- which is exactly what happens
        every time a roll is matched -- survives as an orphan row and is counted
        a second time on the next start, inflating realized P&L by the absorbed
        child's credit. The rebuild is authoritative, so the table must end up
        matching it rather than accumulating history's earlier guesses.
        """
        conn = self.connection

        if strategies:
            sql = _upsert_sql("strategies", self._STRATEGY_COLUMNS, ("id",))
            await conn.executemany(sql, [self._strategy_row(s) for s in strategies])

        if reconcile_account is not None:
            keep = [s.id for s in strategies if s.account_number == reconcile_account]
            if keep:
                placeholders = ", ".join("?" for _ in keep)
                await conn.execute(
                    f"DELETE FROM strategies WHERE account_number = ? "  # noqa: S608 - ids are parameterised
                    f"AND id NOT IN ({placeholders})",
                    (reconcile_account, *keep),
                )
            else:
                await conn.execute("DELETE FROM strategies WHERE account_number = ?", (reconcile_account,))

        await conn.commit()

    @staticmethod
    def _strategy_row(s: Strategy) -> tuple[Any, ...]:
        return (
            s.id,
            s.account_number,
            s.underlying,
            s.strategy_type.value,
            s.risk_profile.value,
            json.dumps([_leg_to_dict(leg) for leg in s.legs]),
            _dt_out(s.opened_at),
            _dt_out(s.closed_at),
            _money_out(s.net_credit),
            _money_out(s.closing_cash_flow),
            _money_out(s.fees),
            json.dumps(list(s.order_ids)),
            int(s.roll_count),
            _money_out(s.iv_rank_at_entry),
            _money_out(s.underlying_price_at_entry),
            s.dte_at_entry,
            _money_out(s.short_delta_at_entry),
            _money_out(s.buying_power_used),
            s.notes,
            1 if s.manual_group else 0,
            1 if s.closed_by_assignment else 0,
            1 if s.outcome_unverified else 0,
        )

    async def load_strategies(self, include_closed: bool = True) -> list[Strategy]:
        """Rebuild Strategy objects. Open trades first is the dashboard's order,
        so sort by open date and let the caller filter."""
        sql = f"SELECT {', '.join(self._STRATEGY_COLUMNS)} FROM strategies"
        if not include_closed:
            sql += " WHERE closed_at IS NULL"
        sql += " ORDER BY opened_at, id"

        out: list[Strategy] = []
        async with self.connection.execute(sql) as cur:
            async for row in cur:
                out.append(self._strategy_from_row(row))
        return out

    async def get_strategy(self, strategy_id: str) -> Strategy | None:
        sql = f"SELECT {', '.join(self._STRATEGY_COLUMNS)} FROM strategies WHERE id = ?"
        async with self.connection.execute(sql, (strategy_id,)) as cur:
            row = await cur.fetchone()
        return self._strategy_from_row(row) if row else None

    @staticmethod
    def _strategy_from_row(row: aiosqlite.Row) -> Strategy:
        opened_at = _dt_in(row["opened_at"])
        assert opened_at is not None  # NOT NULL in the schema
        return Strategy(
            id=row["id"],
            account_number=row["account_number"],
            underlying=row["underlying"],
            strategy_type=StrategyType(row["strategy_type"]),
            risk_profile=RiskProfile(row["risk_profile"]),
            legs=[_leg_from_dict(raw) for raw in json.loads(row["legs"])],
            opened_at=opened_at,
            closed_at=_dt_in(row["closed_at"]),
            net_credit=_money_in(row["net_credit"]) or Decimal(0),
            closing_cash_flow=_money_in(row["closing_cash_flow"]) or Decimal(0),
            fees=_money_in(row["fees"]) or Decimal(0),
            order_ids=list(json.loads(row["order_ids"])),
            roll_count=int(row["roll_count"]),
            iv_rank_at_entry=_money_in(row["iv_rank_at_entry"]),
            underlying_price_at_entry=_money_in(row["underlying_price_at_entry"]),
            dte_at_entry=row["dte_at_entry"],
            short_delta_at_entry=_money_in(row["short_delta_at_entry"]),
            buying_power_used=_money_in(row["buying_power_used"]),
            notes=row["notes"],
            manual_group=bool(row["manual_group"]),
            closed_by_assignment=bool(row["closed_by_assignment"]),
            outcome_unverified=bool(row["outcome_unverified"]),
        )

    # -- snapshots --------------------------------------------------------- #

    async def save_snapshot(
        self,
        strategy_id: str,
        as_of: datetime,
        mark_value: Decimal,
        open_pnl: Decimal,
        pct_of_credit: Decimal | None = None,
        underlying_price: Decimal | None = None,
        worst_short_delta: Decimal | None = None,
    ) -> None:
        """Record where a strategy stood at one instant.

        ``open_pnl`` follows the house convention: positive is winning.
        ``pct_of_credit`` is the fraction of the credit taken in, so -2.0 is the
        classic 2x-loss stop. Re-saving the same instant overwrites it, so a
        retried poll cannot double-count.
        """
        await self.connection.execute(
            "INSERT INTO snapshots (strategy_id, as_of, mark_value, open_pnl, pct_of_credit,"
            " underlying_price, worst_short_delta) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (strategy_id, as_of) DO UPDATE SET"
            " mark_value = excluded.mark_value, open_pnl = excluded.open_pnl,"
            " pct_of_credit = excluded.pct_of_credit, underlying_price = excluded.underlying_price,"
            " worst_short_delta = excluded.worst_short_delta",
            (
                strategy_id,
                _dt_out(as_of),
                _money_out(mark_value),
                _money_out(open_pnl),
                _money_out(pct_of_credit),
                _money_out(underlying_price),
                _money_out(worst_short_delta),
            ),
        )
        await self.connection.commit()

    async def get_snapshots(self, strategy_id: str) -> list[dict[str, Any]]:
        """The full path of one trade, oldest first."""
        out: list[dict[str, Any]] = []
        async with self.connection.execute(
            "SELECT strategy_id, as_of, mark_value, open_pnl, pct_of_credit, underlying_price,"
            " worst_short_delta FROM snapshots WHERE strategy_id = ? ORDER BY as_of",
            (strategy_id,),
        ) as cur:
            async for row in cur:
                item = dict(row)
                item["as_of"] = _dt_in(item["as_of"])
                for col in (
                    "mark_value",
                    "open_pnl",
                    "pct_of_credit",
                    "underlying_price",
                    "worst_short_delta",
                ):
                    item[col] = _money_in(item[col])
                out.append(item)
        return out

    async def _extremum(self, column: str, worst: bool) -> dict[str, Decimal]:
        """Per-strategy min (worst=True) or max of a money column.

        Reduced in Python on purpose: the column is TEXT, and SQL MIN/MAX on
        TEXT compares lexicographically, where "-90" sorts below "-150".
        """
        found: dict[str, Decimal] = {}
        async with self.connection.execute(
            f"SELECT strategy_id, {column} FROM snapshots WHERE {column} IS NOT NULL"
        ) as cur:
            async for row in cur:
                value = _money_in(row[1])
                if value is None:
                    continue
                current = found.get(row[0])
                if current is None or (value < current if worst else value > current):
                    found[row[0]] = value
        return found

    async def open_pnl_before(self, day: date) -> dict[str, Decimal]:
        """The last open P&L recorded before ``day``, per strategy.

        This is what "P&L today" is measured against: the mark the position
        carried at the close of the previous session it was snapshotted on. A
        strategy with no earlier snapshot is absent rather than zero — a
        position opened this morning has not moved since yesterday because it
        did not exist yesterday, and printing 0 would claim otherwise.
        """
        found: dict[str, Decimal] = {}
        async with self.connection.execute(
            "SELECT strategy_id, open_pnl, as_of FROM snapshots "
            "WHERE open_pnl IS NOT NULL AND date(as_of) < ? ORDER BY as_of",
            (day.isoformat(),),
        ) as cur:
            async for row in cur:
                value = _money_in(row[1])
                if value is not None:
                    found[row[0]] = value  # later rows win: the most recent one
        return found

    async def last_snapshot_day_before(self, day: date) -> date | None:
        """The day the marks behind "P&L today" were taken.

        Usually yesterday. After a long weekend, or a stretch with the app
        closed, it is not — and a change measured over four days labelled as
        today's would be a lie by omission.
        """
        async with self.connection.execute(
            "SELECT max(date(as_of)) FROM snapshots WHERE open_pnl IS NOT NULL AND date(as_of) < ?",
            (day.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    async def max_adverse_excursion(self) -> dict[str, Decimal]:
        """Worst open P&L ever recorded per strategy (most negative), in dollars.

        A value of -450 means the trade was once $450 underwater, whatever it
        eventually closed for. Strategies with no snapshot are absent.
        """
        return await self._extremum("open_pnl", worst=True)

    async def max_favourable_excursion(self) -> dict[str, Decimal]:
        """Best open P&L ever recorded per strategy — how much was left on the table."""
        return await self._extremum("open_pnl", worst=False)

    async def worst_pct_of_credit(self) -> dict[str, Decimal]:
        """Worst ``pct_of_credit`` per strategy: the 2x-stop adherence number.

        -2.0 means the loss reached twice the credit taken in, the point at
        which the trade should already have been closed.
        """
        return await self._extremum("pct_of_credit", worst=True)

    # -- manual overrides -------------------------------------------------- #

    async def set_manual_override(self, strategy_id: str, group_id: str) -> None:
        """Pin a position to a group the auto-grouper would not have chosen.

        The user is the authority on what belongs to what: a leg they consider
        part of a rolled strangle stays part of it, however the heuristics feel.
        """
        await self.connection.execute(
            "INSERT INTO manual_overrides (strategy_id, group_id, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT (strategy_id) DO UPDATE SET group_id = excluded.group_id,"
            " created_at = excluded.created_at",
            (strategy_id, group_id, datetime.now().astimezone().isoformat()),
        )
        await self.connection.commit()

    async def get_manual_overrides(self) -> dict[str, str]:
        out: dict[str, str] = {}
        async with self.connection.execute("SELECT strategy_id, group_id FROM manual_overrides") as cur:
            async for row in cur:
                out[row[0]] = row[1]
        return out

    async def clear_manual_override(self, strategy_id: str) -> None:
        await self.connection.execute("DELETE FROM manual_overrides WHERE strategy_id = ?", (strategy_id,))
        await self.connection.commit()

    # -- underlying metrics ------------------------------------------------ #

    _METRIC_COLUMNS = (
        "symbol",
        "as_of",
        "price",
        "iv",
        "iv_rank",
        "iv_percentile",
        "hv_30_day",
        "beta",
        "liquidity_rating",
        "earnings_date",
        "ex_dividend_date",
    )

    async def save_underlying_metrics(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
        price: Decimal | None = None,
        iv: Decimal | None = None,
        iv_rank: Decimal | None = None,
        iv_percentile: Decimal | None = None,
        hv_30_day: Decimal | None = None,
        beta: Decimal | None = None,
        liquidity_rating: int | None = None,
        earnings_date: date | None = None,
        ex_dividend_date: date | None = None,
    ) -> None:
        """Cache one underlying's metrics. Latest wins; there is one row per symbol.

        ``iv_rank`` is stored exactly as tastytrade gives it — a fraction, where
        0.35 means 35% — so nothing downstream has to guess which scale it is on.
        The ex-dividend date is here because it is half of the only legitimate
        leg-level alarm: a short call in the money before the underlying goes ex.
        """
        await self.connection.execute(
            _upsert_sql("underlying_metrics", self._METRIC_COLUMNS, ("symbol",)),
            (
                symbol,
                _dt_out(as_of or datetime.now().astimezone()),
                _money_out(price),
                _money_out(iv),
                _money_out(iv_rank),
                _money_out(iv_percentile),
                _money_out(hv_30_day),
                _money_out(beta),
                liquidity_rating,
                _date_out(earnings_date),
                _date_out(ex_dividend_date),
            ),
        )
        await self.connection.commit()

    async def get_underlying_metrics(self, symbols: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
        """Cached metrics keyed by symbol. Unknown symbols are simply absent."""
        sql = f"SELECT {', '.join(self._METRIC_COLUMNS)} FROM underlying_metrics"
        params: tuple[Any, ...] = ()
        wanted = list(symbols) if symbols is not None else None
        if wanted is not None:
            if not wanted:
                return {}
            sql += f" WHERE symbol IN ({', '.join('?' for _ in wanted)})"
            params = tuple(wanted)

        out: dict[str, dict[str, Any]] = {}
        async with self.connection.execute(sql, params) as cur:
            async for row in cur:
                item = dict(row)
                item["as_of"] = _dt_in(item["as_of"])
                for col in ("price", "iv", "iv_rank", "iv_percentile", "hv_30_day", "beta"):
                    item[col] = _money_in(item[col])
                item["earnings_date"] = _date_in(item["earnings_date"])
                item["ex_dividend_date"] = _date_in(item["ex_dividend_date"])
                out[item["symbol"]] = item
        return out
