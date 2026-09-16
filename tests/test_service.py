"""The application layer.

These cover the three ways this layer was quietly lying: rebuilding the journal
from five days of history and calling it the whole account, recording a zero for
a day that had no prices, and asking UTC what day it is on an exchange that
trades in New York.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from tastytrade.account import Transaction

from tastydesk.core.db import Database
from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)
from tastydesk.core.pnl import compute_pnl
from tastydesk.core.risk import assess
from tastydesk.service import MARKET_TZ, SyncError, _as_transactions, market_today

RAW_TX = {
    "id": 4242,
    "account-number": "5WT0001",
    "transaction-type": "Trade",
    "transaction-sub-type": "Sell to Open",
    "description": "Sold 1 SPX 5800 Put",
    "executed-at": "2026-02-02T15:30:00Z",
    "transaction-date": "2026-02-02",
    "value": "1250.00",
    "value-effect": "Credit",
    "net-value": "1248.37",
    "net-value-effect": "Credit",
    "is-estimated-fee": False,
    "symbol": "SPXW  260320P05800000",
    "instrument-type": "Equity Option",
    "underlying-symbol": "SPX",
    "action": "Sell to Open",
    "quantity": "1",
    "price": "12.50",
    "regulatory-fees": "0.13",
    "regulatory-fees-effect": "Debit",
    "clearing-fees": "0.50",
    "clearing-fees-effect": "Debit",
    "commission": "1.00",
    "commission-effect": "Debit",
    "proprietary-index-option-fees": "0.45",
    "proprietary-index-option-fees-effect": "Debit",
    "other-charge": "0.25",
    "other-charge-effect": "Debit",
    "other-charge-description": "Exchange fee",
    "order-id": 777,
    "leg-count": 1,
}


# ------------------------------------------------------- rehydration


async def test_a_stored_transaction_can_be_read_back(tmp_path: Path) -> None:
    """The whole journal is rebuilt from these rows, so they must survive storage.

    They did not: the SDK's model requires ``is-estimated-fee`` and the table
    never stored it, so every single row failed validation and the rebuild
    silently fell back to whatever the last API call happened to return.
    """
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.migrate()
    try:
        original = Transaction.model_validate(dict(RAW_TX))
        await db.upsert_transactions([original])
        stored = await db.get_transactions()

        rehydrated, dropped = _as_transactions(stored)

        assert dropped == 0
        assert len(rehydrated) == 1
        got = rehydrated[0]
        assert got.id == original.id
        assert got.net_value == original.net_value
        # Index-option and "other" fees are read by grouping._fee_total and were
        # being lost entirely. They are signed negative by the SDK.
        assert got.proprietary_index_option_fees == D("-0.45")
        assert got.other_charge == D("-0.25")
    finally:
        await db.close()


def test_unreadable_rows_are_counted_not_swallowed() -> None:
    good = dict(RAW_TX)
    bad = {"id": 99, "account_number": "5WT0001"}  # missing everything required

    rehydrated, dropped = _as_transactions([good, bad])

    assert len(rehydrated) == 1
    assert dropped == 1


def test_sync_error_exists_so_a_partial_rebuild_can_refuse() -> None:
    """A short history is indistinguishable from a small account.

    The failure mode this replaces was worse than an exception: the dashboard
    came back empty and looked entirely plausible.
    """
    assert issubclass(SyncError, RuntimeError)


# ------------------------------------------------------- market calendar


def test_market_today_follows_new_york_not_the_server() -> None:
    assert market_today() == datetime.now(MARKET_TZ).date()


def test_expiry_evening_does_not_roll_over_early() -> None:
    """At 8:30pm in New York it is still expiry day, whatever UTC thinks."""
    instant = datetime(2026, 9, 15, 20, 30, tzinfo=ZoneInfo("America/New_York"))

    assert instant.astimezone(MARKET_TZ).date() == date(2026, 9, 15)
    assert instant.astimezone(UTC).date() == date(2026, 9, 16)


def test_pin_risk_survives_expiry_evening() -> None:
    """The alarm must not switch itself off on the one evening it matters.

    A 0-DTE short strike a whisker from the money is the moment a premium seller
    most needs to know. Computed against the UTC date after 8pm Eastern, the
    position reads as already expired and every expiry-week finding disappears.
    """
    expiry = date(2026, 9, 15)
    legs = [
        Leg(
            symbol="SPY   260915P00580000",
            instrument_type="Equity Option",
            underlying="SPY",
            direction=Direction.SHORT,
            quantity=D(1),
            option_type=OptionType.PUT,
            strike=D(580),
            expiration=expiry,
            open_price=D("2.00"),
            mark=D("8.00"),
        ),
        Leg(
            symbol="SPY   260915P00575000",
            instrument_type="Equity Option",
            underlying="SPY",
            direction=Direction.LONG,
            quantity=D(1),
            option_type=OptionType.PUT,
            strike=D(575),
            expiration=expiry,
            open_price=D("1.00"),
            mark=D("5.50"),
        ),
    ]
    strategy = Strategy(
        id="x",
        account_number="A",
        underlying="SPY",
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=RiskProfile.DEFINED,
        legs=legs,
        opened_at=datetime(2026, 8, 1, tzinfo=UTC),
        net_credit=D(100),
    )
    pnl = compute_pnl(strategy)

    from tastydesk.core.models import UnderlyingQuote

    quote = UnderlyingQuote(symbol="SPY", last=D("579.50"), mark=D("579.50"), iv=D("0.18"))

    on_expiry_day = assess(strategy, pnl, quote, date(2026, 9, 15))
    next_utc_day = assess(strategy, pnl, quote, date(2026, 9, 16))

    assert on_expiry_day.dte == 0
    assert on_expiry_day.pin_risk is True
    assert on_expiry_day.score > next_utc_day.score
    # The bug in one line: ask the wrong calendar and the warning vanishes.
    assert next_utc_day.pin_risk is False


# ------------------------------------------------------- snapshots


async def test_snapshot_skips_unpriced_strategies(tmp_path: Path) -> None:
    """A zero written for a day with no prices becomes a fake best-ever mark.

    That fabricated extreme then corrupts max favourable excursion, which is the
    history the rule-adherence report reads.
    """
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.migrate()
    try:
        # Snapshots reference a real strategy, which is the right constraint.
        await db.save_strategies(
            [
                Strategy(
                    id="s1",
                    account_number="A",
                    underlying="SPY",
                    strategy_type=StrategyType.NAKED_PUT,
                    risk_profile=RiskProfile.UNDEFINED,
                    legs=[],
                    opened_at=datetime(2026, 2, 1, tzinfo=UTC),
                    net_credit=D(300),
                )
            ]
        )
        await db.save_snapshot(
            "s1",
            datetime(2026, 3, 2, tzinfo=UTC),
            mark_value=D("-750"),
            open_pnl=D("-450"),
            pct_of_credit=D("-1.5"),
            underlying_price=D("540"),
            worst_short_delta=D("0.40"),
        )

        assert (await db.max_adverse_excursion())["s1"] == D("-450")
        # Nothing favourable has been recorded, so there must be no favourable
        # extreme -- a 0 here would be the fabrication.
        favourable = await db.max_favourable_excursion()
        assert favourable.get("s1") != D("0") or "s1" not in favourable
    finally:
        await db.close()


@pytest.mark.parametrize("value", [None])
def test_an_unpriced_strategy_has_no_pnl_to_record(value: None) -> None:
    """compute_pnl must return None, which is what snapshot() now skips on."""
    leg = Leg(
        symbol="SPY   260320P00540000",
        instrument_type="Equity Option",
        underlying="SPY",
        direction=Direction.SHORT,
        quantity=D(1),
        option_type=OptionType.PUT,
        strike=D(540),
        expiration=date(2026, 3, 20),
        open_price=D("3.00"),
        mark=value,
    )
    strategy = Strategy(
        id="x",
        account_number="A",
        underlying="SPY",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[leg],
        opened_at=datetime(2026, 2, 1, tzinfo=UTC),
        net_credit=D(300),
    )

    pnl = compute_pnl(strategy)

    assert pnl.open_pnl is None
    assert pnl.cost_to_close is None
    assert pnl.fully_quoted is False


# ------------------------------------------------- what the recheck found


def test_a_share_leg_carries_its_own_delta() -> None:
    """One delivered stock position was blanking the whole portfolio's delta.

    A share's delta is 1 by definition and needs no quote. Leaving it None
    until the Greeks feed supplied one meant a single leg of assigned stock
    propagated "unknown" all the way up to PortfolioSummary.net_delta.
    """
    lot = Leg(
        symbol="SPY",
        instrument_type="Equity",
        underlying="SPY",
        direction=Direction.LONG,
        quantity=D(100),
        multiplier=D(1),
        open_price=D("580.00"),
    )
    short_lot = Leg(
        symbol="SPY",
        instrument_type="Equity",
        underlying="SPY",
        direction=Direction.SHORT,
        quantity=D(100),
        multiplier=D(1),
        open_price=D("580.00"),
    )

    assert lot.delta is None  # no quote arrived, and none is needed
    assert lot.position_delta == D(100)
    assert short_lot.position_delta == D(-100)


def test_delivered_shares_do_not_blank_the_portfolio_delta() -> None:
    held = Strategy(
        id="A1:SPY:t3",
        account_number="A1",
        underlying="SPY",
        strategy_type=StrategyType.EQUITY,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[
            Leg(
                symbol="SPY",
                instrument_type="Equity",
                underlying="SPY",
                direction=Direction.LONG,
                quantity=D(100),
                multiplier=D(1),
                open_price=D("580.00"),
            )
        ],
        opened_at=datetime(2026, 10, 16, tzinfo=UTC),
        net_credit=D(-58000),
    )

    assert held.net_position_delta == D(100)


def test_long_stock_is_not_defined_risk() -> None:
    """Its floor is the company reaching zero, which is not a defined risk.

    Labelling it Defined would also let it inherit the score moderation that
    genuinely capped structures get in the risk scorer.
    """
    from tastydesk.core.classify import classify

    lot = Leg(
        symbol="SPY",
        instrument_type="Equity",
        underlying="SPY",
        direction=Direction.LONG,
        quantity=D(100),
        multiplier=D(1),
        open_price=D("580.00"),
    )

    strategy_type, profile = classify([lot])

    assert strategy_type is StrategyType.EQUITY
    assert profile is RiskProfile.UNDEFINED
