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
import os
from collections.abc import Iterable, Sequence
from datetime import date, datetime
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

_MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _MIGRATION_1),)

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
)


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

        Returns the number of rows written. Re-running a sync over the same
        window rewrites identical rows rather than duplicating them, which is
        what makes "fetch the last 90 days every time" a safe default.
        """
        if not rows:
            return 0
        conn = self.connection
        sql = _upsert_sql("transactions", _TX_COLUMNS, ("id",))
        before = conn.total_changes
        await conn.executemany(sql, [_tx_row(tx) for tx in rows])
        await conn.commit()
        return conn.total_changes - before

    async def get_transactions(self, since: date | None = None) -> list[dict[str, Any]]:
        """Stored transactions, oldest first, with money back in Decimal form."""
        sql = f"SELECT {', '.join(_TX_COLUMNS)} FROM transactions"
        params: tuple[Any, ...] = ()
        if since is not None:
            # transaction_date is stored ISO, so lexical >= is chronological >=.
            sql += " WHERE transaction_date >= ?"
            params = (since.isoformat(),)
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
                out.append(item)
        return out

    async def last_transaction_date(self) -> date | None:
        """Newest stored transaction date — the watermark an incremental sync resumes from."""
        async with self.connection.execute("SELECT MAX(transaction_date) FROM transactions") as cur:
            row = await cur.fetchone()
        return _date_in(row[0]) if row and row[0] else None

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
    )

    async def save_strategies(self, strategies: Sequence[Strategy]) -> None:
        """Upsert whole strategies, legs and all, in one transaction."""
        if not strategies:
            return
        conn = self.connection
        sql = _upsert_sql("strategies", self._STRATEGY_COLUMNS, ("id",))
        await conn.executemany(sql, [self._strategy_row(s) for s in strategies])
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
