"""Pricing the book under stated conditions.

The payoff diagram answers one question and refuses every other. These are the
others: what is this worth if the market moves two percent, if a week passes,
if volatility collapses. The refusals matter as much as the numbers — an option
with no implied volatility to price against is unknown, not zero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal as D

from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)
from tastydesk.core.pnl import payoff_at
from tastydesk.core.scenario import Scenario, black_scholes, leg_value, strategy_pnl

TODAY = date(2026, 10, 1)


def short_put(strike: str = "580", iv: str | None = "0.20", quantity: str = "1") -> Leg:
    return Leg(
        symbol=f"SPY   261120P00{int(D(strike) * 1000):06d}",
        instrument_type="Equity Option",
        underlying="SPY",
        direction=Direction.SHORT,
        quantity=D(quantity),
        option_type=OptionType.PUT,
        strike=D(strike),
        expiration=date(2026, 11, 20),
        open_price=D("5.00"),
        mark=D("4.00"),
        iv=None if iv is None else D(iv),
    )


def strangle(legs: list[Leg]) -> Strategy:
    return Strategy(
        id="s",
        account_number="A",
        underlying="SPY",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=legs,
        opened_at=datetime(2026, 9, 1, tzinfo=UTC),
        net_credit=D("500"),
    )


def test_a_put_is_worth_more_when_the_market_falls() -> None:
    leg = short_put()
    here = leg_value(leg, D(600), Scenario(), TODAY)
    lower = leg_value(leg, D(600), Scenario(price_shift=D("-0.05")), TODAY)

    assert here is not None and lower is not None
    # Short, so both are negative: it costs more to buy back after a fall.
    assert lower < here < 0


def test_time_passing_is_worth_money_to_the_seller() -> None:
    leg = short_put()
    now = leg_value(leg, D(600), Scenario(), TODAY)
    later = leg_value(leg, D(600), Scenario(days=30), TODAY)

    assert now is not None and later is not None
    assert later > now  # less negative: cheaper to buy back


def test_volatility_is_shifted_relatively_not_in_points() -> None:
    """A 12% underlying and a 56% one do not both jump to 22%."""
    # No mark, so each is priced at its feed volatility rather than at the
    # one its mark implies — which is what this is testing.
    quiet = short_put(iv="0.12")
    loud = short_put(iv="0.56")
    quiet.mark = None
    loud.mark = None
    scenario = Scenario(iv_shift=D("0.5"))

    quiet_change = leg_value(quiet, D(600), scenario, TODAY) - leg_value(quiet, D(600), Scenario(), TODAY)
    loud_change = leg_value(loud, D(600), scenario, TODAY) - leg_value(loud, D(600), Scenario(), TODAY)

    assert quiet_change < 0 and loud_change < 0
    assert abs(loud_change) > abs(quiet_change)


def test_at_expiry_the_scenario_is_the_payoff_diagram() -> None:
    """The two have to meet, or one of them is lying."""
    trade = strangle([short_put(), short_put(strike="640")])
    dte = (date(2026, 11, 20) - TODAY).days

    priced = strategy_pnl(trade, D(600), Scenario(days=dte), TODAY)
    drawn = payoff_at(trade, D(600))

    assert priced is not None
    assert abs(priced - drawn) < D("0.01")


def test_a_leg_with_no_volatility_is_unknown_not_zero() -> None:
    trade = strangle([short_put(), short_put(strike="640", iv=None)])

    assert leg_value(trade.legs[1], D(600), Scenario(), TODAY) is None
    # And one unknown leg makes the whole trade unknown rather than a smaller
    # number that looks complete.
    assert strategy_pnl(trade, D(600), Scenario(), TODAY) is None


def test_a_future_is_priced_as_the_move_since_entry() -> None:
    future = Leg(
        symbol="/ZBZ6",
        instrument_type="Future",
        underlying="/ZBZ6",
        direction=Direction.LONG,
        quantity=D(1),
        multiplier=D(1000),
        open_price=D("106.53125"),
        mark=D("104.671875"),
    )

    flat = leg_value(future, D("104.671875"), Scenario(), TODAY)
    up = leg_value(future, D("104.671875"), Scenario(price_shift=D("0.01")), TODAY)

    assert flat == D("-1859.375")
    assert up is not None and up > flat
    # A future has no time value, so a week passing changes nothing.
    assert leg_value(future, D("104.671875"), Scenario(days=7), TODAY) == flat


def test_black_scholes_at_expiry_is_intrinsic() -> None:
    assert black_scholes(OptionType.CALL, D(110), D(100), D(0), D("0.3")) == D(10)
    assert black_scholes(OptionType.PUT, D(110), D(100), D(0), D("0.3")) == D(0)


def test_with_every_dial_at_zero_a_leg_is_worth_its_mark() -> None:
    """The scenario starts from the price on the screen.

    Priced at the feed's implied volatility, the book did not come back to its
    own marks — a /ZS call marked 17.50 modelled at 23.35. Each leg is priced at
    the volatility its mark implies, so at zero it is the mark, exactly.
    """
    leg = short_put(iv="0.35")  # a feed figure that does not match the mark
    value = leg_value(leg, D(600), Scenario(), TODAY)

    assert value is not None
    assert abs(value - leg.mark * leg.notional_multiplier) < D("0.01")


def test_a_futures_option_is_priced_off_its_own_month() -> None:
    from tastydesk.core.scenario import leg_underlying

    march = Leg(
        symbol="./ZBH7 OZBF7 261224P106",
        instrument_type="Future Option",
        underlying="/ZBZ6",
        direction=Direction.SHORT,
        quantity=D(2),
        multiplier=D(1000),
        option_type=OptionType.PUT,
        strike=D(106),
        expiration=date(2026, 12, 24),
        open_price=D("3.70"),
    )
    assert leg_underlying(march) == "/ZBH7"
