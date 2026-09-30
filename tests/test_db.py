"""Tests for the SQLite persistence layer.

The point of most of these is money integrity: if a Decimal does not survive a
round trip exactly, every P&L number the dashboard shows is wrong by an amount
nobody can see.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from tastytrade.account import Transaction

from tastydesk.core.db import DEFAULT_DB_PATH, Database
from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "nested" / "tastydesk.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


def make_transaction(tx_id: int, *, net_value: str = "-450.13", day: int = 5) -> Transaction:
    return Transaction(
        id=tx_id,
        account_number="5WX00001",
        transaction_type="Trade",
        transaction_sub_type="Sell to Open",
        description="Sold 1 SPY 12/19/25 Put",
        executed_at=datetime(2025, 3, day, 14, 30, tzinfo=UTC),
        transaction_date=date(2025, 3, day),
        value=Decimal("300.00"),
        net_value=Decimal(net_value),
        is_estimated_fee=False,
        symbol="SPY   251219P00580000",
        underlying_symbol="SPY",
        action="Sell to Open",
        quantity=Decimal("1"),
        price=Decimal("3.00"),
        commission=Decimal("-1.00"),
        regulatory_fees=Decimal("-0.13"),
        order_id=987654,
        leg_count=2,
    )


def make_strategy(strategy_id: str = "str-1") -> Strategy:
    short_put = Leg(
        symbol="SPY   251219P00580000",
        instrument_type="Equity Option",
        underlying="SPY",
        direction=Direction.SHORT,
        quantity=Decimal("2"),
        option_type=OptionType.PUT,
        strike=Decimal("580.00"),
        expiration=date(2025, 12, 19),
        open_price=Decimal("6.25"),
        mark=Decimal("7.10"),
        delta=Decimal("-0.31"),
    )
    long_put = Leg(
        symbol="SPY   251219P00570000",
        instrument_type="Equity Option",
        underlying="SPY",
        direction=Direction.LONG,
        quantity=Decimal("2"),
        option_type=OptionType.PUT,
        strike=Decimal("570.50"),
        expiration=date(2025, 12, 19),
        open_price=Decimal("3.75"),
        mark=Decimal("4.20"),
        delta=Decimal("-0.19"),
    )
    return Strategy(
        id=strategy_id,
        account_number="5WX00001",
        underlying="SPY",
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=RiskProfile.DEFINED,
        legs=[short_put, long_put],
        opened_at=datetime(2025, 3, 5, 14, 30, tzinfo=UTC),
        net_credit=Decimal("498.70"),
        closing_cash_flow=Decimal("0"),
        fees=Decimal("-1.30"),
        order_ids=[987654, 987655],
        iv_rank_at_entry=Decimal("0.3500"),
        underlying_price_at_entry=Decimal("601.23"),
        dte_at_entry=45,
        short_delta_at_entry=Decimal("-0.31"),
        buying_power_used=Decimal("1901.30"),
    )


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_default_path_is_in_application_support() -> None:
    assert DEFAULT_DB_PATH.parent.name == "TastyDesk"
    assert DEFAULT_DB_PATH.name == "tastydesk.db"


async def test_migrate_twice_is_a_noop(tmp_path: Path) -> None:
    database = Database(tmp_path / "twice.db")
    await database.connect()
    await database.migrate()
    first = await database.schema_version()
    await database.save_strategies([make_strategy()])

    await database.migrate()  # second run must not re-apply anything
    assert await database.schema_version() == first
    async with database.connection.execute("SELECT COUNT(*) FROM schema_version") as cur:
        rows = await cur.fetchone()
    assert rows[0] == first  # one row per applied migration, no duplicates
    assert len(await database.load_strategies()) == 1  # data survived
    await database.close()


async def test_wal_and_foreign_keys_enabled(db: Database) -> None:
    async with db.connection.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0].lower() == "wal"
    async with db.connection.execute("PRAGMA foreign_keys") as cur:
        assert (await cur.fetchone())[0] == 1


async def test_database_file_is_private(tmp_path: Path) -> None:
    database = Database(tmp_path / "priv" / "tastydesk.db")
    await database.connect()
    await database.migrate()
    assert (database.path.stat().st_mode & 0o777) == 0o600
    await database.close()


async def test_money_columns_are_text_not_real(db: Database) -> None:
    for table, column in (
        ("transactions", "net_value"),
        ("strategies", "net_credit"),
        ("snapshots", "open_pnl"),
    ):
        async with db.connection.execute(f"PRAGMA table_info({table})") as cur:
            info = {row["name"]: row["type"] for row in await cur.fetchall()}
        assert info[column] == "TEXT", f"{table}.{column} must be TEXT, got {info[column]}"


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #


async def test_decimal_round_trips_exactly(db: Database) -> None:
    await db.upsert_transactions([make_transaction(1, net_value="1234.56")])
    rows = await db.get_transactions()

    assert rows[0]["net_value"] == Decimal("1234.56")
    assert isinstance(rows[0]["net_value"], Decimal)
    # Exponent survives too: a $3.00 credit must not come back as Decimal("3").
    assert str(rows[0]["price"]) == "3.00"
    assert rows[0]["executed_at"] == datetime(2025, 3, 5, 14, 30, tzinfo=UTC)
    assert rows[0]["transaction_date"] == date(2025, 3, 5)

    # And the value is physically stored as text, never as a float.
    async with db.connection.execute("SELECT typeof(net_value), net_value FROM transactions") as cur:
        kind, raw = await cur.fetchone()
    assert kind == "text"
    assert raw == "1234.56"


async def test_upsert_is_idempotent_on_repeated_ids(db: Database) -> None:
    batch = [make_transaction(1), make_transaction(2), make_transaction(3)]
    assert await db.upsert_transactions(batch) == 3
    await db.upsert_transactions(batch)  # same window synced again

    rows = await db.get_transactions()
    assert len(rows) == 3
    assert [r["id"] for r in rows] == [1, 2, 3]


async def test_upsert_refreshes_a_corrected_row(db: Database) -> None:
    await db.upsert_transactions([make_transaction(7, net_value="-450.13")])
    await db.upsert_transactions([make_transaction(7, net_value="-451.00")])

    rows = await db.get_transactions()
    assert len(rows) == 1
    assert rows[0]["net_value"] == Decimal("-451.00")


async def test_get_transactions_since_and_watermark(db: Database) -> None:
    await db.upsert_transactions(
        [make_transaction(1, day=1), make_transaction(2, day=5), make_transaction(3, day=9)]
    )
    assert await db.last_transaction_date() == date(2025, 3, 9)

    recent = await db.get_transactions(since=date(2025, 3, 5))
    assert [r["id"] for r in recent] == [2, 3]


async def test_empty_database_has_no_watermark(db: Database) -> None:
    assert await db.last_transaction_date() is None
    assert await db.get_transactions() == []
    assert await db.upsert_transactions([]) == 0


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


async def test_strategy_with_legs_round_trips(db: Database) -> None:
    original = make_strategy()
    await db.save_strategies([original])

    loaded = (await db.load_strategies())[0]
    assert loaded.id == original.id
    assert loaded.strategy_type is StrategyType.PUT_CREDIT_SPREAD
    assert loaded.risk_profile is RiskProfile.DEFINED
    assert loaded.opened_at == original.opened_at
    assert loaded.closed_at is None
    assert loaded.net_credit == Decimal("498.70")
    assert loaded.order_ids == [987654, 987655]
    assert loaded.iv_rank_at_entry == Decimal("0.3500")
    assert loaded.dte_at_entry == 45
    assert loaded.manual_group is False

    short, long = loaded.legs
    assert short.direction is Direction.SHORT
    assert short.option_type is OptionType.PUT
    assert short.strike == Decimal("580.00")
    assert short.expiration == date(2025, 12, 19)
    assert short.open_price == Decimal("6.25")
    assert short.mark == Decimal("7.10")
    assert short.delta == Decimal("-0.31")
    assert long.direction is Direction.LONG
    assert long.strike == Decimal("570.50")
    assert long.quantity == Decimal("2")
    assert long.multiplier == Decimal("100")

    # The derived numbers still agree after the trip through SQLite.
    assert loaded.realized_pnl == original.realized_pnl
    assert loaded.net_position_delta == original.net_position_delta


async def test_save_strategies_upserts_and_filters_closed(db: Database) -> None:
    open_trade = make_strategy("open-1")
    closed_trade = make_strategy("closed-1")
    closed_trade.closed_at = datetime(2025, 4, 1, 15, 0, tzinfo=UTC)
    closed_trade.closing_cash_flow = Decimal("-210.40")
    await db.save_strategies([open_trade, closed_trade])

    assert len(await db.load_strategies()) == 2
    only_open = await db.load_strategies(include_closed=False)
    assert [s.id for s in only_open] == ["open-1"]

    # Re-saving the same ids updates in place rather than duplicating.
    open_trade.roll_count = 1
    open_trade.notes = "rolled out to May"
    await db.save_strategies([open_trade, closed_trade])
    again = await db.load_strategies()
    assert len(again) == 2
    refreshed = await db.get_strategy("open-1")
    assert refreshed is not None
    assert refreshed.roll_count == 1
    assert refreshed.notes == "rolled out to May"
    reloaded_closed = await db.get_strategy("closed-1")
    assert reloaded_closed is not None
    assert reloaded_closed.closing_cash_flow == Decimal("-210.40")


async def test_equity_leg_without_option_fields(db: Database) -> None:
    shares = Leg(
        symbol="SPY",
        instrument_type="Equity",
        underlying="SPY",
        direction=Direction.LONG,
        quantity=Decimal("100"),
        multiplier=Decimal("1"),
        open_price=Decimal("601.23"),
    )
    strategy = make_strategy("equity-1")
    strategy.strategy_type = StrategyType.EQUITY
    strategy.legs = [shares]
    await db.save_strategies([strategy])

    leg = (await db.load_strategies())[0].legs[0]
    assert leg.option_type is None
    assert leg.strike is None
    assert leg.expiration is None
    assert leg.mark is None
    assert leg.multiplier == Decimal("1")


# --------------------------------------------------------------------------- #
# Snapshots and excursions
# --------------------------------------------------------------------------- #


async def test_snapshots_give_max_adverse_excursion(db: Database) -> None:
    await db.save_strategies([make_strategy("str-1"), make_strategy("str-2")])
    start = datetime(2025, 3, 6, 20, 0, tzinfo=UTC)

    # str-1 dips to -900 (about 1.8x the credit) and recovers to a winner.
    path = [
        (Decimal("-420.00"), Decimal("78.70"), Decimal("0.16")),
        (Decimal("-1398.70"), Decimal("-900.00"), Decimal("-1.80")),
        (Decimal("-150.00"), Decimal("348.70"), Decimal("0.70")),
    ]
    for i, (mark, pnl, pct) in enumerate(path):
        await db.save_snapshot(
            "str-1",
            start + timedelta(days=i),
            mark_value=mark,
            open_pnl=pnl,
            pct_of_credit=pct,
            underlying_price=Decimal("598.10"),
            worst_short_delta=Decimal("-0.44"),
        )
    await db.save_snapshot(
        "str-2",
        start,
        mark_value=Decimal("-90.00"),
        open_pnl=Decimal("-90.00"),
        pct_of_credit=Decimal("-0.18"),
    )

    mae = await db.max_adverse_excursion()
    # Lexical ordering would call "-90" worse than "-900"; Decimal must win.
    assert mae["str-1"] == Decimal("-900.00")
    assert mae["str-2"] == Decimal("-90.00")

    mfe = await db.max_favourable_excursion()
    assert mfe["str-1"] == Decimal("348.70")

    # The 2x-stop report reads this: the trade reached 1.8x, never 2x.
    assert (await db.worst_pct_of_credit())["str-1"] == Decimal("-1.80")

    history = await db.get_snapshots("str-1")
    assert len(history) == 3
    assert history[0]["as_of"] == start
    assert history[1]["open_pnl"] == Decimal("-900.00")
    assert history[2]["worst_short_delta"] == Decimal("-0.44")


async def test_repeated_snapshot_at_same_instant_overwrites(db: Database) -> None:
    await db.save_strategies([make_strategy("str-1")])
    when = datetime(2025, 3, 6, 20, 0, tzinfo=UTC)
    await db.save_snapshot("str-1", when, Decimal("-420.00"), Decimal("78.70"))
    await db.save_snapshot("str-1", when, Decimal("-500.00"), Decimal("-1.30"))

    history = await db.get_snapshots("str-1")
    assert len(history) == 1
    assert history[0]["open_pnl"] == Decimal("-1.30")
    assert history[0]["pct_of_credit"] is None


async def test_excursions_are_empty_without_snapshots(db: Database) -> None:
    assert await db.max_adverse_excursion() == {}
    assert await db.max_favourable_excursion() == {}


async def test_snapshot_requires_a_known_strategy(db: Database) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        await db.save_snapshot("ghost", datetime(2025, 3, 6, 20, 0, tzinfo=UTC), Decimal("-1"), Decimal("-1"))


# --------------------------------------------------------------------------- #
# Manual overrides and metrics
# --------------------------------------------------------------------------- #


async def test_manual_overrides(db: Database) -> None:
    assert await db.get_manual_overrides() == {}
    await db.set_manual_override("str-1", "group-a")
    await db.set_manual_override("str-2", "group-a")
    assert await db.get_manual_overrides() == {"str-1": "group-a", "str-2": "group-a"}

    await db.set_manual_override("str-2", "group-b")  # user changed their mind
    assert (await db.get_manual_overrides())["str-2"] == "group-b"

    await db.clear_manual_override("str-2")
    assert await db.get_manual_overrides() == {"str-1": "group-a"}


async def test_underlying_metrics_round_trip(db: Database) -> None:
    await db.save_underlying_metrics(
        "SPY",
        as_of=datetime(2025, 3, 6, 20, 0, tzinfo=UTC),
        price=Decimal("601.23"),
        iv=Decimal("0.1425"),
        iv_rank=Decimal("0.3500"),
        hv_30_day=Decimal("0.1100"),
        liquidity_rating=4,
        earnings_date=date(2025, 4, 24),
        ex_dividend_date=date(2025, 3, 21),
    )
    await db.save_underlying_metrics("QQQ", iv_rank=Decimal("0.6200"))

    all_metrics = await db.get_underlying_metrics()
    assert set(all_metrics) == {"SPY", "QQQ"}
    spy = all_metrics["SPY"]
    assert spy["iv_rank"] == Decimal("0.3500")  # a fraction, not a percent
    assert spy["price"] == Decimal("601.23")
    assert spy["liquidity_rating"] == 4
    assert spy["ex_dividend_date"] == date(2025, 3, 21)
    assert spy["as_of"] == datetime(2025, 3, 6, 20, 0, tzinfo=UTC)

    # Newer data replaces the row rather than piling up.
    await db.save_underlying_metrics("SPY", price=Decimal("610.00"))
    assert (await db.get_underlying_metrics(["SPY"]))["SPY"]["price"] == Decimal("610.00")
    assert await db.get_underlying_metrics([]) == {}
    assert await db.get_underlying_metrics(["NOPE"]) == {}


async def test_using_before_connect_is_a_clear_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "cold.db")
    with pytest.raises(RuntimeError, match="not connected"):
        await database.last_transaction_date()


async def test_context_manager_connects_and_migrates(tmp_path: Path) -> None:
    async with Database(tmp_path / "ctx.db") as database:
        await database.save_strategies([make_strategy()])
        assert len(await database.load_strategies()) == 1
    assert database._conn is None


async def test_a_roll_answered_once_stays_answered(tmp_path: Path) -> None:
    """The backlog came back every time because the answer lived in the page.

    Eighty-nine pairs worked through last week reappeared in full on the next
    visit: "Separate" was React state and died with the component. An answer
    given once is an answer, so it is written down.
    """
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.migrate()
    try:
        assert await db.roll_decisions() == {}

        await db.set_roll_decision("a", "b", "separate")
        await db.set_roll_decisions([("c", "d", "separate"), ("e", "f", "linked")])

        answered = await db.roll_decisions()
        assert answered == {("a", "b"): "separate", ("c", "d"): "separate", ("e", "f"): "linked"}

        # Changing an answer replaces it rather than doubling it up.
        await db.set_roll_decision("a", "b", "linked")
        assert (await db.roll_decisions())[("a", "b")] == "linked"
    finally:
        await db.close()


async def test_iv_range_width_from_two_readings(db: Database) -> None:
    """IWM, as tastytrade published it half an hour apart on 30 Sep 2026."""
    t = datetime(2026, 9, 30, 17, 53, tzinfo=UTC)
    assert await db.iv_range_width("IWM") is None
    await db.record_iv_reading("IWM", t, Decimal("0.212925332"), Decimal("0.137137533"))
    await db.record_iv_reading("IWM", t, Decimal("0.212925332"), Decimal("0.137137533"))
    assert await db.iv_range_width("IWM") is None
    later = t + timedelta(minutes=30)
    await db.record_iv_reading("IWM", later, Decimal("0.213619418"), Decimal("0.140654353"))
    width = await db.iv_range_width("IWM")
    assert width is not None and abs(width - Decimal("0.1974")) < Decimal("0.001")


async def test_iv_range_width_rejects_opposite_moves(db: Database) -> None:
    t = datetime(2026, 9, 30, tzinfo=UTC)
    await db.record_iv_reading("SPY", t, Decimal("0.157"), Decimal("0.335"))
    await db.record_iv_reading("SPY", t + timedelta(hours=1), Decimal("0.158"), Decimal("0.330"))
    assert await db.iv_range_width("SPY") is None
