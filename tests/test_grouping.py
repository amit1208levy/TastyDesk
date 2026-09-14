"""Tests for the trade reconstructor.

Every transaction here is built with :meth:`Transaction.model_validate` from
dasherized keys, exactly as the tastytrade API returns them — including the
``-effect`` companions that carry the sign. That matters: the SDK's own
validator is what turns ``{"net-value": "100", "net-value-effect": "Debit"}``
into ``Decimal("-100")``, and grouping.py is built on the assumption that money
arrives already signed. Constructing Transaction objects by keyword would
bypass the very machinery whose behaviour we are relying on.

The money in these fixtures is therefore always written the way a human reads a
trade confirmation — a credit is positive, a debit is negative — and the helper
splits it into amount-plus-effect on the way in.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from tastytrade.account import Transaction

from tastydesk.core.grouping import build_strategies, match_rolls
from tastydesk.core.models import Direction, OptionType, RiskProfile, StrategyType
from tastydesk.core.occ import build_occ_symbol

ACCOUNT = "5WX12345"
OTHER_ACCOUNT = "5WX99999"

D = Decimal
ZERO = D("0")


# --------------------------------------------------------------------------- helpers


def _money(raw: dict[str, Any], key: str, amount: Decimal | None) -> None:
    """Write a signed amount the way the API does: magnitude plus an effect."""
    if amount is None:
        return
    raw[key] = str(abs(amount))
    raw[f"{key}-effect"] = "Debit" if amount < ZERO else "Credit"


def tx(
    *,
    id: int,
    sub_type: str,
    symbol: str,
    net_value: Decimal,
    when: datetime,
    quantity: Decimal | int | None = 1,
    price: str | None = None,
    order_id: int | None = None,
    leg_count: int | None = None,
    transaction_type: str = "Trade",
    underlying: str | None = "SPY",
    instrument_type: str | None = "Equity Option",
    action: str | None = None,
    with_action: bool = True,
    value: Decimal | None = None,
    commission: Decimal | None = None,
    regulatory_fees: Decimal | None = None,
    clearing_fees: Decimal | None = None,
    account: str = ACCOUNT,
) -> Transaction:
    """One API-shaped transaction row.

    ``net_value`` is the account cash flow with its real sign; the helper
    encodes it as the API does. ``value`` defaults to ``net_value`` when the row
    carries no explicit fees, which is the common case in these fixtures.
    """
    raw: dict[str, Any] = {
        "id": id,
        "account-number": account,
        "transaction-type": transaction_type,
        "transaction-sub-type": sub_type,
        "description": f"{sub_type} {symbol}",
        "executed-at": when.isoformat(),
        "transaction-date": when.date().isoformat(),
        "is-estimated-fee": False,
        "symbol": symbol,
    }
    if instrument_type is not None:
        raw["instrument-type"] = instrument_type
    if underlying is not None:
        raw["underlying-symbol"] = underlying
    if with_action:
        raw["action"] = action or sub_type
    if quantity is not None:
        raw["quantity"] = str(quantity)
    if price is not None:
        raw["price"] = price
    if order_id is not None:
        raw["order-id"] = order_id
    if leg_count is not None:
        raw["leg-count"] = leg_count
    _money(raw, "value", value if value is not None else net_value)
    _money(raw, "net-value", net_value)
    _money(raw, "commission", commission)
    _money(raw, "regulatory-fees", regulatory_fees)
    _money(raw, "clearing-fees", clearing_fees)
    return Transaction.model_validate(raw)


def opt(strike: str, option_type: str, expiration: date = date(2025, 2, 21), root: str = "SPY") -> str:
    return build_occ_symbol(root, expiration, option_type, D(strike))


def at(day: int, hour: int = 15, minute: int = 30, month: int = 1) -> datetime:
    return datetime(2025, month, day, hour, minute, tzinfo=UTC)


FEB = date(2025, 2, 21)
MAR = date(2025, 3, 21)

P560 = opt("560", "P")
C600 = opt("600", "C")
P550 = opt("550", "P")
C610 = opt("610", "C")
P550_MAR = opt("550", "P", MAR)
C610_MAR = opt("610", "C", MAR)


def leg_for(strategy: Any, symbol: str) -> Any:
    matches = [leg for leg in strategy.legs if leg.symbol == symbol]
    assert matches, f"{symbol!r} not among {[leg.symbol for leg in strategy.legs]}"
    return matches[0]


# --------------------------------------------------------------------------- fixtures


def strangle_open() -> list[Transaction]:
    """Sell the 560 put for 2.00 and the 600 call for 2.50, one order, one contract."""
    return [
        tx(
            id=1,
            sub_type="Sell to Open",
            symbol=P560,
            quantity=1,
            price="2.00",
            value=D("200"),
            net_value=D("198.86"),
            commission=D("-1.00"),
            regulatory_fees=D("-0.04"),
            clearing_fees=D("-0.10"),
            when=at(2),
            order_id=9001,
            leg_count=2,
        ),
        tx(
            id=2,
            sub_type="Sell to Open",
            symbol=C600,
            quantity=1,
            price="2.50",
            value=D("250"),
            net_value=D("248.86"),
            commission=D("-1.00"),
            regulatory_fees=D("-0.04"),
            clearing_fees=D("-0.10"),
            when=at(2),
            order_id=9001,
            leg_count=2,
        ),
    ]


def strangle_close() -> list[Transaction]:
    """Buy the strangle back for 1.00 + 0.50, one order, at a profit."""
    return [
        tx(
            id=3,
            sub_type="Buy to Close",
            symbol=P560,
            quantity=1,
            price="1.00",
            value=D("-100"),
            net_value=D("-101.14"),
            commission=D("-1.00"),
            regulatory_fees=D("-0.04"),
            clearing_fees=D("-0.10"),
            when=at(20),
            order_id=9500,
            leg_count=2,
        ),
        tx(
            id=4,
            sub_type="Buy to Close",
            symbol=C600,
            quantity=1,
            price="0.50",
            value=D("-50"),
            net_value=D("-51.14"),
            commission=D("-1.00"),
            regulatory_fees=D("-0.04"),
            clearing_fees=D("-0.10"),
            when=at(20),
            order_id=9500,
            leg_count=2,
        ),
    ]


def iron_condor_open() -> list[Transaction]:
    """One four-leg order: short 560/600, long wings at 550/610."""
    return [
        tx(
            id=11,
            sub_type="Sell to Open",
            symbol=P560,
            price="2.00",
            net_value=D("200"),
            when=at(3),
            order_id=9100,
            leg_count=4,
        ),
        tx(
            id=12,
            sub_type="Buy to Open",
            symbol=P550,
            price="1.00",
            net_value=D("-100"),
            when=at(3),
            order_id=9100,
            leg_count=4,
        ),
        tx(
            id=13,
            sub_type="Sell to Open",
            symbol=C600,
            price="2.20",
            net_value=D("220"),
            when=at(3),
            order_id=9100,
            leg_count=4,
        ),
        tx(
            id=14,
            sub_type="Buy to Open",
            symbol=C610,
            price="1.10",
            net_value=D("-110"),
            when=at(3),
            order_id=9100,
            leg_count=4,
        ),
    ]


# --------------------------------------------------------------------------- the basics


def test_two_leg_strangle_opens_as_one_strategy() -> None:
    """The cardinal rule in its simplest form: one order is one trade.

    Two rows, two legs, one strategy — not two naked shorts that a risk engine
    would then alarm on separately.
    """
    strategies = build_strategies(strangle_open(), ACCOUNT)

    assert len(strategies) == 1
    strategy = strategies[0]
    assert strategy.strategy_type is StrategyType.SHORT_STRANGLE
    assert strategy.risk_profile is RiskProfile.UNDEFINED
    assert len(strategy.legs) == 2
    assert strategy.is_open
    assert strategy.opened_at == at(2)
    assert strategy.order_ids == [9001]
    assert strategy.roll_count == 0


def test_strangle_legs_carry_positive_prices_and_direction() -> None:
    """Direction lives in Leg.direction; a price is never negative."""
    strategy = build_strategies(strangle_open(), ACCOUNT)[0]

    put = leg_for(strategy, P560)
    call = leg_for(strategy, C600)
    assert put.direction is Direction.SHORT
    assert call.direction is Direction.SHORT
    assert put.open_price == D("2.00")
    assert call.open_price == D("2.50")
    assert put.option_type is OptionType.PUT
    assert call.option_type is OptionType.CALL
    assert put.strike == D("560")
    assert call.strike == D("610") - D("10")
    assert put.expiration == FEB
    assert put.multiplier == D("100")
    # Cash flow is derived from direction, and selling brings cash in.
    assert put.open_cash_flow == D("200")


def test_strangle_net_credit_is_the_signed_sum_including_fees() -> None:
    """net_value already carries the sign and the fees; grouping just adds."""
    strategy = build_strategies(strangle_open(), ACCOUNT)[0]

    assert strategy.net_credit == D("198.86") + D("248.86")
    assert strategy.closing_cash_flow == ZERO
    # Per leg: 1.00 commission + 0.04 regulatory + 0.10 clearing, all debits.
    assert strategy.fees == D("-1.14") * 2
    # Which is exactly the gap between gross value and net value.
    assert strategy.net_credit == D("450") + strategy.fees


def test_strangle_closes_and_banks_the_difference() -> None:
    strategies = build_strategies(strangle_open() + strangle_close(), ACCOUNT)

    assert len(strategies) == 1
    strategy = strategies[0]
    assert not strategy.is_open
    assert strategy.closed_at == at(20)
    assert strategy.net_credit == D("447.72")
    assert strategy.closing_cash_flow == D("-152.28")
    assert strategy.realized_pnl == D("295.44")
    assert strategy.order_ids == [9001, 9500]


def test_closed_strangle_is_still_named_a_strangle() -> None:
    """A trade is named by what it was, not by whichever leg died last.

    Closing two legs is two rows; between them the position is momentarily a
    lone short call. Recording that would leave the journal full of closed
    "Naked Call" entries that were never managed as naked calls.
    """
    strategy = build_strategies(strangle_open() + strangle_close(), ACCOUNT)[0]

    assert strategy.strategy_type is StrategyType.SHORT_STRANGLE
    assert len(strategy.legs) == 2


def test_closed_strategy_keeps_the_full_leg_history() -> None:
    """Once closed, legs are the record of what was traded, at traded size."""
    strategy = build_strategies(strangle_open() + strangle_close(), ACCOUNT)[0]

    assert {leg.symbol for leg in strategy.legs} == {P560, C600}
    assert all(leg.quantity == D("1") for leg in strategy.legs)


def test_four_leg_iron_condor_is_one_defined_risk_strategy() -> None:
    """The case that makes grouping worth doing: the wings must stay attached.

    Split into four trades, the two shorts would look naked and the risk engine
    would invent danger the long wings were bought to remove.
    """
    strategies = build_strategies(iron_condor_open(), ACCOUNT)

    assert len(strategies) == 1
    condor = strategies[0]
    assert condor.strategy_type is StrategyType.IRON_CONDOR
    assert condor.risk_profile is RiskProfile.DEFINED
    assert len(condor.legs) == 4
    assert condor.net_credit == D("210")
    assert condor.id == f"{ACCOUNT}:SPY:9100"


def test_two_orders_on_the_same_day_stay_two_strategies() -> None:
    """Same underlying, same minute, different orders: two separate trades."""
    rows = strangle_open() + [
        tx(
            id=21,
            sub_type="Sell to Open",
            symbol=P550,
            price="1.00",
            net_value=D("100"),
            when=at(2),
            order_id=9002,
            leg_count=1,
        ),
    ]
    strategies = build_strategies(rows, ACCOUNT)

    assert [s.id for s in strategies] == [f"{ACCOUNT}:SPY:9001", f"{ACCOUNT}:SPY:9002"]
    assert strategies[1].strategy_type is StrategyType.NAKED_PUT


def test_rows_from_another_account_are_ignored() -> None:
    rows = strangle_open() + [
        tx(
            id=31,
            sub_type="Sell to Open",
            symbol=P550,
            price="1.00",
            net_value=D("100"),
            when=at(2),
            order_id=7777,
            account=OTHER_ACCOUNT,
        ),
    ]
    strategies = build_strategies(rows, ACCOUNT)

    assert len(strategies) == 1
    assert strategies[0].account_number == ACCOUNT


def test_multi_leg_order_partially_filled_averages_the_entry_price() -> None:
    """Two fills of one leg on one order are one leg at the blended price."""
    rows = [
        tx(
            id=41,
            sub_type="Sell to Open",
            symbol=P560,
            quantity=1,
            price="2.00",
            net_value=D("200"),
            when=at(2),
            order_id=9001,
            leg_count=1,
        ),
        tx(
            id=42,
            sub_type="Sell to Open",
            symbol=P560,
            quantity=3,
            price="2.20",
            net_value=D("660"),
            when=at(2, minute=31),
            order_id=9001,
            leg_count=1,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert len(strategy.legs) == 1
    leg = strategy.legs[0]
    assert leg.quantity == D("4")
    assert leg.open_price == D("2.15")  # (1*2.00 + 3*2.20) / 4
    assert strategy.net_credit == D("860")


# --------------------------------------------------------------------------- partial closes


def naked_puts(count: int, order_id: int, when: datetime, first_id: int) -> list[Transaction]:
    return [
        tx(
            id=first_id,
            sub_type="Sell to Open",
            symbol=P560,
            quantity=count,
            price="2.00",
            net_value=D("200") * count,
            when=when,
            order_id=order_id,
            leg_count=1,
        ),
    ]


def test_partial_close_of_three_of_five_leaves_two_open() -> None:
    """Closing part of a position shrinks it; it does not close it.

    Leg.quantity is the size still open, because that is the size still at
    risk — and the risk engine reads exactly this list.
    """
    rows = naked_puts(5, 9200, at(2), first_id=51) + [
        tx(
            id=52,
            sub_type="Buy to Close",
            symbol=P560,
            quantity=3,
            price="1.00",
            net_value=D("-300"),
            when=at(15),
            order_id=9600,
            leg_count=1,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert strategy.is_open
    assert strategy.closed_at is None
    assert len(strategy.legs) == 1
    assert strategy.legs[0].quantity == D("2")
    assert strategy.net_credit == D("1000")
    assert strategy.closing_cash_flow == D("-300")
    assert strategy.realized_pnl == D("700")  # banked so far; two contracts still live


def test_close_spanning_two_strategies_allocates_fifo_and_splits_the_cash() -> None:
    """Three sold Monday, two Tuesday, four bought back Friday.

    The oldest lots come off first and the cash follows them in the same
    proportion, so neither strategy shows a gain it did not make.
    """
    rows = (
        naked_puts(3, 9201, at(6), first_id=61)
        + naked_puts(2, 9202, at(7), first_id=62)
        + [
            tx(
                id=63,
                sub_type="Buy to Close",
                symbol=P560,
                quantity=4,
                price="1.00",
                net_value=D("-400"),
                when=at(10),
                order_id=9601,
                leg_count=1,
            ),
        ]
    )
    monday, tuesday = build_strategies(rows, ACCOUNT)

    assert monday.id == f"{ACCOUNT}:SPY:9201"
    assert not monday.is_open  # all three of the oldest lots were taken
    assert monday.closed_at == at(10)
    assert monday.closing_cash_flow == D("-300")

    assert tuesday.id == f"{ACCOUNT}:SPY:9202"
    assert tuesday.is_open  # one of its two contracts survives
    assert tuesday.legs[0].quantity == D("1")
    assert tuesday.closing_cash_flow == D("-100")

    # Nothing invented, nothing lost between the two.
    assert monday.closing_cash_flow + tuesday.closing_cash_flow == D("-400")


def test_split_cash_never_loses_a_penny_to_rounding() -> None:
    """Three contracts out of seven does not divide evenly; it must still add up."""
    rows = (
        naked_puts(3, 9203, at(6), first_id=71)
        + naked_puts(4, 9204, at(7), first_id=72)
        + [
            tx(
                id=73,
                sub_type="Buy to Close",
                symbol=P560,
                quantity=7,
                price="0.43",
                net_value=D("-301.07"),
                when=at(10),
                order_id=9602,
                leg_count=1,
            ),
        ]
    )
    first, second = build_strategies(rows, ACCOUNT)

    assert first.closing_cash_flow + second.closing_cash_flow == D("-301.07")
    assert not first.is_open
    assert not second.is_open


def test_closing_more_than_is_open_warns_and_attributes_only_the_match(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """History that starts mid-trade must not invent a strategy to hang cash on."""
    rows = naked_puts(2, 9205, at(6), first_id=81) + [
        tx(
            id=82,
            sub_type="Buy to Close",
            symbol=P560,
            quantity=5,
            price="1.00",
            net_value=D("-500"),
            when=at(10),
            order_id=9603,
            leg_count=1,
        ),
    ]
    with caplog.at_level(logging.WARNING, logger="tastydesk.core.grouping"):
        strategy = build_strategies(rows, ACCOUNT)[0]

    assert not strategy.is_open
    # Only the two contracts we can actually see get their share of the cash.
    assert strategy.closing_cash_flow == D("-200")
    assert any("only" in record.message for record in caplog.records)


def test_close_with_no_matching_open_is_warned_not_guessed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = [
        tx(
            id=91,
            sub_type="Buy to Close",
            symbol=P560,
            quantity=1,
            price="1.00",
            net_value=D("-100"),
            when=at(10),
            order_id=9604,
            leg_count=1,
        ),
    ]
    with caplog.at_level(logging.WARNING, logger="tastydesk.core.grouping"):
        strategies = build_strategies(rows, ACCOUNT)

    assert strategies == []
    assert any("matches no open position" in record.message for record in caplog.records)


def test_closing_one_side_of_a_strangle_reclassifies_the_remainder() -> None:
    """Buy back the call and what is left really is a naked put. Say so."""
    rows = strangle_open() + [
        tx(
            id=101,
            sub_type="Buy to Close",
            symbol=C600,
            quantity=1,
            price="0.40",
            net_value=D("-40"),
            when=at(15),
            order_id=9605,
            leg_count=1,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert strategy.is_open
    assert strategy.strategy_type is StrategyType.NAKED_PUT
    assert [leg.symbol for leg in strategy.legs] == [P560]


def test_leg_count_mismatch_is_warned(caplog: pytest.LogCaptureFixture) -> None:
    """The broker says four legs; we grouped two. Something is missing — say it.

    Silence here would mean displaying a half-built iron condor as a credit
    spread, with a max loss that does not match the position actually held.
    """
    rows = [
        tx(
            id=111,
            sub_type="Sell to Open",
            symbol=P560,
            price="2.00",
            net_value=D("200"),
            when=at(2),
            order_id=9300,
            leg_count=4,
        ),
        tx(
            id=112,
            sub_type="Buy to Open",
            symbol=P550,
            price="1.00",
            net_value=D("-100"),
            when=at(2),
            order_id=9300,
            leg_count=4,
        ),
    ]
    with caplog.at_level(logging.WARNING, logger="tastydesk.core.grouping"):
        strategy = build_strategies(rows, ACCOUNT)[0]

    assert strategy.strategy_type is StrategyType.PUT_CREDIT_SPREAD
    assert any("leg-count" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- expiry, assignment


def test_worthless_expiration_realizes_exactly_the_credit() -> None:
    """The premium seller's favourite outcome, and an exact-arithmetic check.

    An expiration moves no cash, so realized P&L must equal the credit taken in
    to the cent — no phantom closing cost, no lost fee.
    """
    rows = strangle_open() + [
        tx(
            id=121,
            sub_type="Expiration",
            symbol=P560,
            quantity=1,
            price=None,
            net_value=ZERO,
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            with_action=False,
        ),
        tx(
            id=122,
            sub_type="Expiration",
            symbol=C600,
            quantity=1,
            price=None,
            net_value=ZERO,
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            with_action=False,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert not strategy.is_open
    assert strategy.closing_cash_flow == ZERO
    assert strategy.realized_pnl == strategy.net_credit == D("447.72")
    assert strategy.closed_at == at(21, month=2)
    assert strategy.notes is not None and "expired" in strategy.notes


def test_expiration_without_a_quantity_closes_everything_open() -> None:
    """Expiration rows often omit the size: whatever is open is what expires."""
    rows = naked_puts(5, 9206, at(6), first_id=131) + [
        tx(
            id=132,
            sub_type="Expiration",
            symbol=P560,
            quantity=None,
            price=None,
            net_value=ZERO,
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            with_action=False,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert not strategy.is_open
    assert strategy.realized_pnl == D("1000")


def test_assignment_closes_the_option_and_records_the_shares() -> None:
    """An assigned short put is a closed option plus 100 long shares.

    The share delivery has its own cost basis and is not a cost of the option
    trade: folding its $56,000 into this P&L would turn a winner into a
    catastrophe. It belongs in the notes, so the trade never looks like one
    that simply vanished.
    """
    rows = [
        tx(
            id=141,
            sub_type="Sell to Open",
            symbol=P560,
            quantity=1,
            price="2.00",
            net_value=D("198.86"),
            value=D("200"),
            commission=D("-1.14"),
            when=at(2),
            order_id=9400,
            leg_count=1,
        ),
        tx(
            id=142,
            sub_type="Assignment",
            symbol=P560,
            quantity=1,
            price=None,
            net_value=ZERO,
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            with_action=False,
        ),
        # The stock that showed up. Not a leg of the option trade.
        tx(
            id=143,
            sub_type="Buy to Open",
            symbol="SPY",
            quantity=100,
            price="560.00",
            net_value=D("-56000"),
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            instrument_type="Equity",
            action="Buy to Open",
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert not strategy.is_open
    assert strategy.realized_pnl == strategy.net_credit == D("198.86")
    assert strategy.closing_cash_flow == ZERO
    assert strategy.notes is not None
    assert "assignment" in strategy.notes
    assert "+100 shares of SPY" in strategy.notes


def test_assigned_short_call_records_short_shares() -> None:
    """A short call assigned delivers stock away: -100 shares, not +100."""
    rows = [
        tx(
            id=151,
            sub_type="Sell to Open",
            symbol=C600,
            quantity=1,
            price="2.50",
            net_value=D("250"),
            when=at(2),
            order_id=9401,
            leg_count=1,
        ),
        tx(
            id=152,
            sub_type="Assignment",
            symbol=C600,
            quantity=1,
            price=None,
            net_value=ZERO,
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            with_action=False,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert strategy.notes is not None
    assert "-100 shares of SPY" in strategy.notes


def test_cash_settled_assignment_moves_cash_and_closes() -> None:
    """Index options settle in cash: the debit is a real closing cash flow."""
    rows = [
        tx(
            id=161,
            sub_type="Sell to Open",
            symbol=P560,
            quantity=1,
            price="2.00",
            net_value=D("200"),
            when=at(2),
            order_id=9402,
            leg_count=1,
        ),
        tx(
            id=162,
            sub_type="Cash Settled Assignment",
            symbol=P560,
            quantity=1,
            price=None,
            net_value=D("-350"),
            when=at(21, month=2),
            transaction_type="Receive Deliver",
            with_action=False,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert not strategy.is_open
    assert strategy.closing_cash_flow == D("-350")
    assert strategy.realized_pnl == D("-150")
    assert strategy.notes is not None and "cash settled" in strategy.notes


# --------------------------------------------------------------------------- non-leg cash


def test_dividend_on_a_held_position_joins_that_trade() -> None:
    """A dividend earned while the shares were held is part of that trade's cash.

    Dropping it would understate a covered call, which is precisely the trade
    where the dividend is part of the thesis.
    """
    rows = [
        tx(
            id=171,
            sub_type="Buy to Open",
            symbol="SPY",
            quantity=100,
            price="560.00",
            net_value=D("-56000"),
            when=at(2),
            order_id=9450,
            leg_count=2,
            instrument_type="Equity",
        ),
        tx(
            id=172,
            sub_type="Sell to Open",
            symbol=C600,
            quantity=1,
            price="2.50",
            net_value=D("250"),
            when=at(2),
            order_id=9450,
            leg_count=2,
        ),
        tx(
            id=173,
            sub_type="Dividend",
            symbol="SPY",
            quantity=None,
            price=None,
            net_value=D("176.50"),
            when=at(10, month=2),
            transaction_type="Money Movement",
            instrument_type="Equity",
            with_action=False,
        ),
    ]
    strategy = build_strategies(rows, ACCOUNT)[0]

    assert strategy.strategy_type is StrategyType.COVERED_CALL
    assert strategy.net_credit == D("-56000") + D("250") + D("176.50")
    assert strategy.notes is not None and "Dividend" in strategy.notes


def test_money_movement_that_matches_nothing_stays_at_account_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Interest is not part of any trade, and must not be glued onto one."""
    rows = strangle_open() + [
        tx(
            id=181,
            sub_type="Credit Interest",
            symbol="",
            quantity=None,
            price=None,
            net_value=D("12.34"),
            when=at(31),
            transaction_type="Money Movement",
            underlying=None,
            instrument_type=None,
            with_action=False,
        ),
    ]
    with caplog.at_level(logging.DEBUG, logger="tastydesk.core.grouping"):
        strategy = build_strategies(rows, ACCOUNT)[0]

    assert strategy.net_credit == D("447.72")  # untouched by the interest


def test_ambiguous_dividend_is_not_guessed_at(caplog: pytest.LogCaptureFixture) -> None:
    """Two open trades on the same underlying: refuse to pick one."""
    rows = (
        naked_puts(1, 9207, at(2), first_id=191)
        + naked_puts(1, 9208, at(3), first_id=192)
        + [
            tx(
                id=193,
                sub_type="Dividend",
                symbol="SPY",
                quantity=None,
                price=None,
                net_value=D("50"),
                when=at(10),
                transaction_type="Money Movement",
                instrument_type="Equity",
                with_action=False,
            ),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="tastydesk.core.grouping"):
        strategies = build_strategies(rows, ACCOUNT)

    assert all(s.net_credit == D("200") for s in strategies)
    assert any("left at account level" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- rolls


def roll_order(
    *,
    order_id: int,
    when: datetime,
    close_put: str,
    close_call: str,
    open_put: str,
    open_call: str,
    first_id: int,
) -> list[Transaction]:
    """One order that buys back a strangle and sells a further-out one."""
    return [
        tx(
            id=first_id,
            sub_type="Buy to Close",
            symbol=close_put,
            price="3.00",
            net_value=D("-300"),
            when=when,
            order_id=order_id,
            leg_count=4,
        ),
        tx(
            id=first_id + 1,
            sub_type="Buy to Close",
            symbol=close_call,
            price="1.00",
            net_value=D("-100"),
            when=when,
            order_id=order_id,
            leg_count=4,
        ),
        tx(
            id=first_id + 2,
            sub_type="Sell to Open",
            symbol=open_put,
            price="4.00",
            net_value=D("400"),
            when=when,
            order_id=order_id,
            leg_count=4,
        ),
        tx(
            id=first_id + 3,
            sub_type="Sell to Open",
            symbol=open_call,
            price="2.00",
            net_value=D("200"),
            when=when,
            order_id=order_id,
            leg_count=4,
        ),
    ]


def rolled_history() -> list[Transaction]:
    return strangle_open() + roll_order(
        order_id=9700,
        when=at(10, month=2),
        close_put=P560,
        close_call=C600,
        open_put=P550_MAR,
        open_call=C610_MAR,
        first_id=201,
    )


def test_a_roll_is_two_strategies_before_matching() -> None:
    """build_strategies cannot see the roll: at that moment it is just an open."""
    strategies = build_strategies(rolled_history(), ACCOUNT)

    assert [s.id for s in strategies] == [f"{ACCOUNT}:SPY:9001", f"{ACCOUNT}:SPY:9700"]


def test_match_rolls_merges_the_roll_into_one_trade() -> None:
    """The question the user actually wants answered: does rolling help?

    Left as two records the chain reads as one tidy winner plus a fresh trade.
    Merged, it reads as one position whose cumulative credit is what it is —
    the only number that can honestly answer the question.
    """
    merged = match_rolls(build_strategies(rolled_history(), ACCOUNT))

    assert len(merged) == 1
    trade = merged[0]
    assert trade.roll_count == 1
    assert trade.id == f"{ACCOUNT}:SPY:9001"
    assert trade.opened_at == at(2)  # time in trade runs from the original entry
    assert trade.is_open
    # 447.72 in, 400 out to close, 600 in for the new strangle.
    assert trade.net_credit == D("447.72") + D("600")
    assert trade.closing_cash_flow == D("-400")
    assert trade.realized_pnl == D("647.72")
    assert trade.order_ids == [9001, 9700]


def test_rolled_trade_keeps_only_the_live_legs() -> None:
    """The dead strikes must not stay in the leg list.

    Two reasons, both fatal. An expired leg cannot be quoted, and
    pnl.cost_to_close goes None the moment one leg is unquoted, so a live
    position would show no P&L at all. And four strikes across two expirations
    classify as CUSTOM, hiding a plainly recognisable short strangle.
    """
    trade = match_rolls(build_strategies(rolled_history(), ACCOUNT))[0]

    assert {leg.symbol for leg in trade.legs} == {P550_MAR, C610_MAR}
    assert trade.strategy_type is StrategyType.SHORT_STRANGLE
    assert trade.notes is not None
    assert "rolled" in trade.notes


def test_rolling_four_times_stays_one_trade() -> None:
    """A strangle rolled four times is one trade with one cumulative P&L."""
    rows = strangle_open()
    chain = [
        (9700, P560, C600, opt("550", "P", MAR), opt("610", "C", MAR), 301),
        (
            9701,
            opt("550", "P", MAR),
            opt("610", "C", MAR),
            opt("540", "P", date(2025, 4, 17)),
            opt("620", "C", date(2025, 4, 17)),
            311,
        ),
        (
            9702,
            opt("540", "P", date(2025, 4, 17)),
            opt("620", "C", date(2025, 4, 17)),
            opt("530", "P", date(2025, 5, 16)),
            opt("630", "C", date(2025, 5, 16)),
            321,
        ),
        (
            9703,
            opt("530", "P", date(2025, 5, 16)),
            opt("630", "C", date(2025, 5, 16)),
            opt("520", "P", date(2025, 6, 20)),
            opt("640", "C", date(2025, 6, 20)),
            331,
        ),
    ]
    for index, (order_id, cp, cc, op, oc, first_id) in enumerate(chain):
        rows += roll_order(
            order_id=order_id,
            when=at(10) + timedelta(days=30 * (index + 1)),
            close_put=cp,
            close_call=cc,
            open_put=op,
            open_call=oc,
            first_id=first_id,
        )

    merged = match_rolls(build_strategies(rows, ACCOUNT))

    assert len(merged) == 1
    trade = merged[0]
    assert trade.roll_count == 4
    assert trade.opened_at == at(2)
    assert trade.strategy_type is StrategyType.SHORT_STRANGLE
    assert len(trade.legs) == 2
    # Each roll took in 600 and paid 400; the original credit sits underneath.
    assert trade.net_credit == D("447.72") + D("600") * 4
    assert trade.closing_cash_flow == D("-400") * 4
    assert trade.realized_pnl == D("447.72") + D("800")


def test_a_roll_that_is_later_closed_reads_as_one_closed_trade() -> None:
    rows = rolled_history() + [
        tx(
            id=401,
            sub_type="Buy to Close",
            symbol=P550_MAR,
            price="1.00",
            net_value=D("-100"),
            when=at(1, month=3),
            order_id=9800,
            leg_count=2,
        ),
        tx(
            id=402,
            sub_type="Buy to Close",
            symbol=C610_MAR,
            price="0.50",
            net_value=D("-50"),
            when=at(1, month=3),
            order_id=9800,
            leg_count=2,
        ),
    ]
    merged = match_rolls(build_strategies(rows, ACCOUNT))

    assert len(merged) == 1
    trade = merged[0]
    assert not trade.is_open
    assert trade.closed_at == at(1, month=3)
    assert trade.roll_count == 1
    assert trade.realized_pnl == D("447.72") + D("600") - D("400") - D("150")


def test_unrelated_trades_are_never_merged_as_rolls() -> None:
    """A second strangle put on later is a second trade, not a roll."""
    rows = strangle_open() + [
        tx(
            id=411,
            sub_type="Sell to Open",
            symbol=P550_MAR,
            price="2.00",
            net_value=D("200"),
            when=at(5, month=2),
            order_id=9900,
            leg_count=2,
        ),
        tx(
            id=412,
            sub_type="Sell to Open",
            symbol=C610_MAR,
            price="2.00",
            net_value=D("200"),
            when=at(5, month=2),
            order_id=9900,
            leg_count=2,
        ),
    ]
    merged = match_rolls(build_strategies(rows, ACCOUNT))

    assert len(merged) == 2
    assert all(trade.roll_count == 0 for trade in merged)


def test_match_rolls_does_not_mutate_the_input() -> None:
    """Callers keep their own objects; merging works on copies."""
    original = build_strategies(rolled_history(), ACCOUNT)
    before = [(s.id, s.net_credit, s.roll_count, len(s.legs)) for s in original]

    match_rolls(original)

    assert [(s.id, s.net_credit, s.roll_count, len(s.legs)) for s in original] == before


# --------------------------------------------------------------------------- determinism


def test_ids_are_identical_across_two_runs_over_the_same_history() -> None:
    """Ids are derived, never generated. A resync must update, not duplicate."""
    rows = strangle_open() + iron_condor_open() + strangle_close()

    first = build_strategies(rows, ACCOUNT)
    second = build_strategies(rows, ACCOUNT)

    assert [s.id for s in first] == [s.id for s in second]
    assert [s.id for s in first] == [f"{ACCOUNT}:SPY:9001", f"{ACCOUNT}:SPY:9100"]
    assert [s.net_credit for s in first] == [s.net_credit for s in second]
    assert [s.closed_at for s in first] == [s.closed_at for s in second]


def test_ids_survive_the_history_arriving_in_a_different_order() -> None:
    """The API paginates newest-first; the rebuild must not depend on that."""
    rows = strangle_open() + iron_condor_open() + strangle_close()

    forwards = build_strategies(rows, ACCOUNT)
    backwards = build_strategies(list(reversed(rows)), ACCOUNT)

    assert [s.id for s in forwards] == [s.id for s in backwards]
    assert [s.strategy_type for s in forwards] == [s.strategy_type for s in backwards]
    assert [s.realized_pnl for s in forwards] == [s.realized_pnl for s in backwards]


def test_id_format_is_account_underlying_and_first_order() -> None:
    strategy = build_strategies(strangle_open() + strangle_close(), ACCOUNT)[0]

    assert strategy.id == f"{ACCOUNT}:SPY:9001"
    assert strategy.id.split(":") == [ACCOUNT, "SPY", "9001"]


def test_a_row_without_an_order_id_still_gets_a_stable_id() -> None:
    rows = [
        tx(id=421, sub_type="Sell to Open", symbol=P560, price="2.00", net_value=D("200"), when=at(2)),
    ]
    first = build_strategies(rows, ACCOUNT)[0]
    second = build_strategies(rows, ACCOUNT)[0]

    assert first.id == second.id == f"{ACCOUNT}:SPY:t421"


# --------------------------------------------------------------------------- overrides


def test_manual_overrides_merge_two_strategies_into_one_group() -> None:
    """The escape hatch: the user's own grouping beats the heuristics.

    Legging into a strangle over two orders produces two records; saying so
    once should make them one trade for good.
    """
    rows = [
        tx(
            id=431,
            sub_type="Sell to Open",
            symbol=P560,
            price="2.00",
            net_value=D("200"),
            when=at(2),
            order_id=9001,
            leg_count=1,
        ),
        tx(
            id=432,
            sub_type="Sell to Open",
            symbol=C600,
            price="2.50",
            net_value=D("250"),
            when=at(3),
            order_id=9002,
            leg_count=1,
        ),
    ]
    overrides = {f"{ACCOUNT}:SPY:9001": "manual-1", f"{ACCOUNT}:SPY:9002": "manual-1"}

    strategies = build_strategies(rows, ACCOUNT, overrides)

    assert len(strategies) == 1
    trade = strategies[0]
    assert trade.id == "manual-1"
    assert trade.manual_group
    assert trade.opened_at == at(2)
    assert trade.net_credit == D("450")
    assert trade.strategy_type is StrategyType.SHORT_STRANGLE
    assert trade.order_ids == [9001, 9002]
    assert trade.roll_count == 0  # a manual merge is not a roll


def test_manual_overrides_leave_untouched_strategies_alone() -> None:
    rows = strangle_open() + iron_condor_open()
    overrides = {f"{ACCOUNT}:SPY:9001": "manual-2"}

    strategies = build_strategies(rows, ACCOUNT, overrides)

    assert sorted(s.id for s in strategies) == [f"{ACCOUNT}:SPY:9100", "manual-2"]


def test_match_rolls_respects_a_manual_group() -> None:
    """Having been told how to group, do not then regroup it."""
    overrides = {f"{ACCOUNT}:SPY:9700": "manual-3"}
    strategies = build_strategies(rolled_history(), ACCOUNT, overrides)

    merged = match_rolls(strategies)

    assert sorted(s.id for s in merged) == [f"{ACCOUNT}:SPY:9001", "manual-3"]
    assert all(s.roll_count == 0 for s in merged)
