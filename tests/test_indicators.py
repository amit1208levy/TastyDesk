"""The catalogue has to explain itself.

The page no longer carries notes in its margins: hovering a heading for two
seconds is the only place a field's meaning is written down. That makes a
missing explanation invisible rather than obvious, so it is asserted here.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from tastydesk.core.indicators import (
    _LEG_HELP,
    _STRATEGY_HELP,
    LEG_FIELDS,
    STRATEGY_FIELDS,
    leg_values,
    verdict_for,
)
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


def test_every_field_is_explained() -> None:
    for field in STRATEGY_FIELDS + LEG_FIELDS:
        assert field.help, f"{field.id} has no explanation"
        # A sentence, not a second label: the hint is already the short form.
        assert len(field.help) > len(field.hint), field.id
        assert field.help.strip().endswith("."), field.id


def test_no_explanation_without_a_field() -> None:
    """A renamed field leaves its paragraph behind; this catches that."""
    assert set(_STRATEGY_HELP) == {f.id for f in STRATEGY_FIELDS}
    assert set(_LEG_HELP) == {f.id for f in LEG_FIELDS}


def test_short_delta_says_what_it_means() -> None:
    """The one the old margin note explained, kept as a worked example."""
    field = next(f for f in STRATEGY_FIELDS if f.id == "short_delta")
    assert "in the money" in field.help
    assert "0.30" in field.help


# --------------------------------------------------- the 21-day line, applied


def _short_call(strike: str, expiry: date, opened: datetime | None) -> Leg:
    return Leg(
        symbol=f"XLE   {expiry:%y%m%d}C{int(float(strike)) * 1000:08d}",
        instrument_type="Equity Option",
        underlying="XLE",
        direction=Direction.SHORT,
        quantity=D(1),
        option_type=OptionType.CALL,
        strike=D(strike),
        expiration=expiry,
        open_price=D("0.80"),
        mark=D("0.40"),
        opened_at=opened,
    )


def _diagonal(short_opened: datetime | None) -> Strategy:
    """A PMCC: a LEAP bought in July, a weekly sold against it last week."""
    leap = Leg(
        symbol="XLE   270617C00052500",
        instrument_type="Equity Option",
        underlying="XLE",
        direction=Direction.LONG,
        quantity=D(1),
        option_type=OptionType.CALL,
        strike=D("52.5"),
        expiration=date(2027, 6, 17),
        open_price=D("12.00"),
        mark=D("12.40"),
        opened_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    return Strategy(
        id="pmcc",
        account_number="A",
        underlying="XLE",
        strategy_type=StrategyType.DIAGONAL,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[leap, _short_call("63", date(2026, 10, 2), short_opened)],
        opened_at=datetime(2026, 7, 22, tzinfo=UTC),
        net_credit=D(80),
    )


def _call(strategy: Strategy, dte: int) -> str:
    pnl = compute_pnl(strategy)
    risk = assess(strategy, pnl, None, date(2026, 10, 2) - timedelta(days=dte), None)
    return verdict_for(
        strategy,
        pnl,
        risk,
        profit_target=D("0.5"),
        dte_exit=21,
        stop_multiple=D(2),
    ).action


def test_the_21_day_line_does_not_fire_on_a_weekly_sold_inside_it() -> None:
    """His rule, in his words: it only applies to trades opened over 21 DTE.

    The short call of a diagonal is sold short-dated on purpose. Measuring it
    from the day the LEAP was bought made the app tell him to roll or close a
    trade that was doing exactly what he put it on to do.
    """
    sold_inside = _diagonal(datetime(2026, 9, 18, tzinfo=UTC))

    assert sold_inside.front_entry_dte == 14
    # And it is not waved through either: eight days from expiry is still eight
    # days from expiry, so it is watched and told why the line is not the reason.
    assert _call(sold_inside, dte=8) == "Watch"


def test_the_line_still_fires_on_an_expiry_that_was_outside_it() -> None:
    sold_outside = _diagonal(datetime(2026, 8, 20, tzinfo=UTC))

    assert sold_outside.front_entry_dte == 43
    assert _call(sold_outside, dte=8) == "Roll or close"


def test_without_leg_dates_the_strategy_answers_for_its_front_month() -> None:
    """Rows written before legs carried a date still have to answer something."""
    old = _diagonal(None)

    assert old.front_entry_dte == (date(2026, 10, 2) - date(2026, 7, 22)).days


def test_leg_percentages_are_signed_by_direction() -> None:
    """A short call sold at 0.80, now 0.40, closed last session at 0.50."""
    leg = _short_call("100", date(2026, 12, 18), None)
    leg.prior_close = D("0.50")
    values = leg_values(leg, today=date(2026, 10, 1), price=D("90"))
    assert values["pnl_pct"] == D("0.5")
    assert values["day_change_pct"] == D("0.2")


def test_delta_reads_in_contracts_for_futures_and_shares_for_stock() -> None:
    """How tastytrade prints it: a short call at 0.30 delta on XLE is -30
    shares; the same on a future would be -0.30 of a contract."""
    from tastydesk.core.indicators import broker_delta

    stock = _short_call("100", date(2026, 12, 18), None)
    stock.delta = D("0.30")
    assert broker_delta([stock]) == D("-30")

    future = _short_call("100", date(2026, 12, 18), None)
    future.symbol = "./ZBH7 OZBF7 261224C111"
    future.multiplier = D(1000)
    future.delta = D("0.30")
    assert broker_delta([future]) == D("-0.30")
