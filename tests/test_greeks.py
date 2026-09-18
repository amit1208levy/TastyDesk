"""Tests for portfolio greeks.

The point of this module is that a /ZB delta and an XLE delta cannot be added
together, so the tests are mostly about units: a futures option worth $1,000 a
point and an equity option worth $100 a point must come out in the same
currency before anything is summed, and an underlying whose beta is unknown must
leave the beta-weighted total rather than quietly join it at 1.0.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from tastydesk.core.greeks import leg_dollar_delta, portfolio_greeks
from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
    UnderlyingQuote,
)

OPENED = datetime(2026, 9, 1, 14, 30)
EXP = date(2026, 11, 20)


def option(
    *,
    underlying: str,
    multiplier: str,
    delta: str | None,
    direction: Direction = Direction.SHORT,
    option_type: OptionType = OptionType.CALL,
    quantity: str = "1",
    theta: str | None = None,
    vega: str | None = None,
) -> Leg:
    return Leg(
        symbol=f"{underlying}-{option_type.value}-{quantity}",
        instrument_type="Future Option" if underlying.startswith("/") else "Equity Option",
        underlying=underlying,
        direction=direction,
        quantity=Decimal(quantity),
        multiplier=Decimal(multiplier),
        option_type=option_type,
        strike=Decimal(100),
        expiration=EXP,
        open_price=Decimal("1.00"),
        delta=Decimal(delta) if delta is not None else None,
        theta=Decimal(theta) if theta is not None else None,
        vega=Decimal(vega) if vega is not None else None,
    )


def shares(underlying: str, quantity: str) -> Leg:
    return Leg(
        symbol=underlying,
        instrument_type="Equity",
        underlying=underlying,
        direction=Direction.LONG,
        quantity=Decimal(quantity),
        multiplier=Decimal(1),
        open_price=Decimal("50"),
    )


def strategy(underlying: str, *legs: Leg, sid: str = "s-1") -> Strategy:
    return Strategy(
        id=sid,
        account_number="A1",
        underlying=underlying,
        strategy_type=StrategyType.NAKED_CALL,
        risk_profile=RiskProfile.UNDEFINED,
        legs=list(legs),
        opened_at=OPENED,
    )


def quote(symbol: str, price: str, beta: str | None) -> UnderlyingQuote:
    return UnderlyingQuote(
        symbol=symbol,
        mark=Decimal(price),
        beta=Decimal(beta) if beta is not None else None,
    )


SPY = quote("SPY", "600", "1.0")


def test_dollar_delta_uses_the_contract_multiplier() -> None:
    """A /ZB point is $1,000; an XLE point is $100. The unit is money, not delta."""
    zb = option(underlying="/ZBZ6", multiplier="1000", delta="0.20", direction=Direction.LONG)
    xle = option(underlying="XLE", multiplier="100", delta="0.20", direction=Direction.LONG)

    assert leg_dollar_delta(zb, Decimal("107")) == Decimal("0.20") * 1000 * 107
    assert leg_dollar_delta(xle, Decimal("64")) == Decimal("0.20") * 100 * 64


def test_shares_have_a_delta_of_one_without_a_quote() -> None:
    lot = shares("XLE", "100")
    assert leg_dollar_delta(lot, Decimal("64")) == Decimal("6400")


def test_short_legs_are_signed_negative() -> None:
    short_call = option(underlying="XLE", multiplier="100", delta="0.30")
    assert leg_dollar_delta(short_call, Decimal("64")) == Decimal("-0.30") * 100 * 64


def test_beta_weighting_restates_the_book_in_spy_shares() -> None:
    """Two products, two multipliers, two betas — one answer, in SPY shares."""
    zb = strategy(
        "/ZBZ6",
        option(underlying="/ZBZ6", multiplier="1000", delta="0.50", direction=Direction.LONG),
        sid="zb",
    )
    xle = strategy(
        "XLE",
        option(underlying="XLE", multiplier="100", delta="0.50", direction=Direction.LONG),
        sid="xle",
    )
    quotes = {
        "/ZB": quote("/ZB", "100", "0.5"),
        "XLE": quote("XLE", "60", "1.0"),
        "SPY": SPY,
    }

    totals = portfolio_greeks([zb, xle], quotes, reference_price=Decimal("600"))

    # /ZB: 0.5 x 1000 x 100 = 50,000 dollars of delta, halved by beta -> 25,000
    # XLE: 0.5 x 100 x 60   =  3,000 dollars of delta, beta 1.0       ->  3,000
    assert totals.dollar_delta == Decimal("53000")
    assert totals.beta_weighted_dollars == Decimal("28000")
    assert totals.beta_weighted_delta == Decimal("28000") / Decimal("600")
    assert totals.dollars_per_spy_percent == Decimal("280")
    assert totals.fully_measured


def test_quotes_are_found_by_product_root() -> None:
    """Metrics come back keyed /ZS; the position says /ZSF7. They are one product."""
    trade = strategy(
        "/ZSF7",
        option(underlying="/ZSF7", multiplier="50", delta="0.40", direction=Direction.LONG),
    )
    totals = portfolio_greeks(
        [trade], {"/ZS": quote("/ZS", "1300", "0.07")}, reference_price=Decimal("600")
    )
    assert totals.dollar_delta == Decimal("0.40") * 50 * 1300
    assert totals.missing_beta == ()


def test_a_negative_beta_flips_the_direction() -> None:
    """Long corn is short S&P exposure. Corn's beta really is negative."""
    trade = strategy(
        "/ZCH7",
        option(underlying="/ZCH7", multiplier="50", delta="0.50", direction=Direction.LONG),
    )
    totals = portfolio_greeks(
        [trade], {"/ZC": quote("/ZC", "500", "-0.07")}, reference_price=Decimal("600")
    )
    assert totals.dollar_delta > 0
    assert totals.beta_weighted_dollars is not None
    assert totals.beta_weighted_dollars < 0


def test_unknown_beta_is_excluded_and_named_not_assumed_to_be_one() -> None:
    known = strategy(
        "XLE",
        option(underlying="XLE", multiplier="100", delta="0.50", direction=Direction.LONG),
        sid="xle",
    )
    unknown = strategy(
        "/ZQZ6",
        option(underlying="/ZQZ6", multiplier="4167", delta="0.50", direction=Direction.LONG),
        sid="zq",
    )
    quotes = {"XLE": quote("XLE", "60", "1.0"), "/ZQ": quote("/ZQ", "96", None)}

    totals = portfolio_greeks([known, unknown], quotes, reference_price=Decimal("600"))

    assert totals.missing_beta == ("/ZQ",)
    assert not totals.fully_measured
    # The known leg is still counted; the unknown one is left out entirely.
    assert totals.beta_weighted_dollars == Decimal("3000")


def test_missing_delta_is_reported_and_left_out_of_the_total() -> None:
    trade = strategy(
        "XLE",
        option(underlying="XLE", multiplier="100", delta="0.50", direction=Direction.LONG),
        option(underlying="XLE", multiplier="100", delta=None, direction=Direction.LONG),
    )
    totals = portfolio_greeks(
        [trade], {"XLE": quote("XLE", "60", "1.0")}, reference_price=Decimal("600")
    )
    assert totals.legs_total == 2
    assert totals.legs_with_delta == 1
    assert len(totals.missing_delta) == 1
    assert totals.dollar_delta == Decimal("3000")


def test_an_unpriced_underlying_drops_out_rather_than_counting_as_zero() -> None:
    trade = strategy(
        "XLE", option(underlying="XLE", multiplier="100", delta="0.50", direction=Direction.LONG)
    )
    totals = portfolio_greeks([trade], {}, reference_price=Decimal("600"))
    assert totals.missing_price == ("XLE",)
    assert totals.dollar_delta is None
    assert totals.beta_weighted_delta is None


def test_theta_and_vega_are_money_and_short_premium_collects() -> None:
    trade = strategy(
        "/ZBZ6",
        option(underlying="/ZBZ6", multiplier="1000", delta="0.20", theta="-0.05", vega="0.30"),
    )
    totals = portfolio_greeks(
        [trade], {"/ZB": quote("/ZB", "107", "0.5")}, reference_price=Decimal("600")
    )
    # Short one contract: theta -0.05 x -1000 = +50 a day, vega 0.30 x -1000 = -300.
    assert totals.theta == Decimal("50.00")
    assert totals.vega == Decimal("-300.00")


def test_two_contract_months_of_one_product_share_a_row_but_not_a_price() -> None:
    """Each leg is valued against its own month; the row refuses to pick one."""
    jan = strategy(
        "/ZSF7",
        option(underlying="/ZSF7", multiplier="50", delta="0.40", direction=Direction.LONG),
        sid="jan",
    )
    nov = strategy(
        "/ZSX6",
        option(underlying="/ZSX6", multiplier="50", delta="0.40", direction=Direction.LONG),
        sid="nov",
    )
    quotes = {
        "/ZSF7": quote("/ZSF7", "1320", "0.07"),
        "/ZSX6": quote("/ZSX6", "1300", "0.07"),
    }
    totals = portfolio_greeks([jan, nov], quotes, reference_price=Decimal("600"))

    row = totals.by_underlying[0]
    assert row.product == "/ZS"
    assert row.underlying_price is None
    assert row.dollar_delta == Decimal("0.40") * 50 * 1320 + Decimal("0.40") * 50 * 1300


def test_an_empty_book_measures_nothing_rather_than_zero() -> None:
    totals = portfolio_greeks([], {})
    assert totals.dollar_delta is None
    assert totals.beta_weighted_delta is None
    assert totals.by_underlying == ()
