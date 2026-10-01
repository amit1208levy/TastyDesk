"""Tests for Tom's scanner: his checklists, the strike picker, and the sizing.

The sizing tests carry the point of the feature: a candidate is only useful if
its size already obeys Tom's caps for this account — 2% of net liq at his
stop, credit up to 1% on a strangle, buying power under 50% and 20% a strategy.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tastydesk.core import scanner
from tastydesk.core.models import OptionType
from tastydesk.core.prices import Bar
from tastydesk.core.scanner import Book, LegSpec, Metric, OptionQuote

TODAY = date(2026, 10, 1)


def bars(closes: list[float]) -> list[Bar]:
    start = TODAY - timedelta(days=len(closes))
    return [
        Bar(day=start + timedelta(days=i), open=c, high=c * 1.004, low=c * 0.996, close=c)
        for i, c in enumerate(closes)
    ]


def chart(symbol: str, closes: list[float]) -> scanner.Technicals:
    t = scanner.technicals(symbol, bars(closes))
    assert t is not None
    return t


RISING = [100 + i * 0.5 for i in range(220)]
FALLING = [200 - i * 0.5 for i in range(220)]
FLAT = [100 + (0.4 if i % 2 else -0.4) for i in range(220)]


def book(**over: object) -> Book:
    values: dict[str, object] = {
        "net_liq": Decimal(100000),
        "bp_used": Decimal(30000),
        "theta": Decimal(100),
        "vega": Decimal(-300),
        "beta_weighted_delta": Decimal(50),
        "strategy_bp": {},
        "spy_price": Decimal(700),
    }
    values.update(over)
    return Book(**values)  # type: ignore[arg-type]


def quote(right: OptionType, strike: str, delta: str, mark: str, dte: int = 45) -> OptionQuote:
    return OptionQuote(
        symbol=f"X{right.value}{strike}",
        right=right,
        strike=Decimal(strike),
        expiry=TODAY + timedelta(days=dte),
        dte=dte,
        mark=Decimal(mark),
        bid=None,
        ask=None,
        delta=Decimal(delta),
        theta=Decimal("-0.05") if right is OptionType.PUT else Decimal("-0.04"),
        vega=Decimal("0.2"),
    )


# --------------------------------------------------------------------------
# The chart and the checklists
# --------------------------------------------------------------------------


def test_a_flat_chart_is_calm_and_a_sliding_one_is_not() -> None:
    assert chart("/ZS", FLAT).calm
    assert not chart("/ZN", FALLING).calm


def test_no_naked_put_in_a_downtrend_however_good_the_name() -> None:
    charts = {"AAPL": chart("AAPL", FALLING), "SPY": chart("SPY", RISING)}
    metrics = {"AAPL": Metric(market_cap=Decimal(3e12), iv_rank=Decimal("0.5"))}
    found = scanner.screen(charts, metrics, held=set(), last_opened={}, today=TODAY)
    put = next(c for c in found if c.symbol == "AAPL" and c.setup == "naked_put")
    assert put.status == "watch"
    assert any(c.label == "Trending up" and c.ok is False and c.hard for c in put.checks)


def test_earnings_inside_the_trade_rules_a_put_out() -> None:
    charts = {"MSFT": chart("MSFT", RISING)}
    metrics = {"MSFT": Metric(market_cap=Decimal(3e12), earnings=TODAY + timedelta(days=20))}
    found = scanner.screen(charts, metrics, held=set(), last_opened={}, today=TODAY)
    put = next(c for c in found if c.symbol == "MSFT" and c.setup == "naked_put")
    earnings = next(c for c in put.checks if c.label == "No earnings before expiry")
    assert earnings.ok is False and earnings.hard


def test_a_strangle_is_blocked_by_a_position_that_moves_with_it() -> None:
    charts = {"/ZN": chart("/ZN", FLAT), "/ZS": chart("/ZS", FLAT)}
    found = scanner.screen(charts, {}, held={"/ZB"}, last_opened={}, today=TODAY)
    zn = next(c for c in found if c.symbol == "/ZN")
    zs = next(c for c in found if c.symbol == "/ZS")
    assert zn.status == "watch"
    assert "you hold /ZB" in zn.checks[0].detail
    assert zs.status != "watch"


def test_only_the_best_strangle_in_a_group_stays_ready() -> None:
    charts = {"/6E": chart("/6E", FLAT), "/6A": chart("/6A", FLAT)}
    metrics = {"/6E": Metric(iv_rank=Decimal("0.6")), "/6A": Metric(iv_rank=Decimal("0.4"))}
    found = scanner.screen(charts, metrics, held=set(), last_opened={}, today=TODAY)
    e = next(c for c in found if c.symbol == "/6E")
    a = next(c for c in found if c.symbol == "/6A")
    assert e.status == "ready"
    assert a.status != "ready"
    assert any(c.label == "Best in its group" and "/6E" in c.detail for c in a.checks)


def test_the_11x_follows_the_campaign_and_the_regime() -> None:
    charts = {"/MES": chart("/MES", RISING), "SPY": chart("SPY", RISING)}
    found = scanner.screen(
        charts, {}, held=set(), last_opened={"11x": TODAY - timedelta(days=5)}, today=TODAY
    )
    eleven = next(c for c in found if c.setup == "11x")
    due = next(c for c in eleven.checks if c.label == "Due in the campaign")
    assert due.ok is False  # placed five days ago; Tom waits two weeks
    # Bullish market: the out-of-the-money spread, long put near 25 delta.
    assert eleven.order is not None and eleven.order.legs[0].delta == 0.25


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------


def test_the_expiry_nearest_the_target_wins_and_monthlies_break_ties() -> None:
    listing = [
        (TODAY + timedelta(days=43), 43, "Weekly"),
        (TODAY + timedelta(days=47), 47, "Regular"),
        (TODAY + timedelta(days=90), 90, "Regular"),
    ]
    assert scanner.pick_expiration(listing, 45, (30, 60)) == (TODAY + timedelta(days=47), 47)
    assert scanner.pick_expiration(listing, 45, (100, 140)) is None


def test_the_strike_is_chosen_on_the_real_delta() -> None:
    quotes = [
        quote(OptionType.PUT, "480", "-0.18", "2.10"),
        quote(OptionType.PUT, "470", "-0.13", "1.40"),
        quote(OptionType.PUT, "460", "-0.09", "0.95"),
        quote(OptionType.CALL, "560", "0.13", "1.20"),
    ]
    best = scanner.pick_strike(quotes, OptionType.PUT, 0.13)
    assert best is not None and best.strike == Decimal(470)


# --------------------------------------------------------------------------
# The size
# --------------------------------------------------------------------------


def naked_put_candidate() -> scanner.Candidate:
    found = scanner.screen({"SPY": chart("SPY", RISING)}, {}, held=set(), last_opened={}, today=TODAY)
    return next(c for c in found if c.setup == "naked_put")


def test_a_naked_put_is_sized_by_toms_stop_then_by_buying_power() -> None:
    c = naked_put_candidate()
    leg = quote(OptionType.PUT, "600", "-0.13", "2.50")
    plan, fit = scanner.build_plan(
        c,
        [(LegSpec("Sell", OptionType.PUT, 1, delta=0.13), leg)],
        spot=Decimal(700),
        multiplier=Decimal(100),
        beta=Decimal(1),
        book=book(),
    )
    # $250 credit; a 3x stop loses $500 a lot; 2% of $100,000 is $2,000: four lots.
    assert plan.credit == Decimal(250)
    assert plan.loss_at_stop == Decimal(500)
    # Reg-T: max(20% x 700 - 100, 10% x 600) + 2.50 = 62.50 a share, $6,250 a lot.
    assert plan.bp_per_lot == Decimal("6250.00")
    # Room to 50% is $20,000 (3 lots); 20% per strategy is $20,000 (3 lots).
    assert plan.lots == 3
    assert fit.bp_ok is True


def test_a_strangle_is_capped_at_one_percent_of_net_liq_in_credit() -> None:
    found = scanner.screen({"/CL": chart("/CL", FLAT)}, {}, held=set(), last_opened={}, today=TODAY)
    c = next(x for x in found if x.setup == "strangle")
    put = quote(OptionType.PUT, "60", "-0.08", "0.95", dte=60)
    call = quote(OptionType.CALL, "95", "0.09", "0.95", dte=60)
    plan, _ = scanner.build_plan(
        c,
        [
            (LegSpec("Sell", OptionType.PUT, 1, delta=0.08), put),
            (LegSpec("Sell", OptionType.CALL, 1, delta=0.09), call),
        ],
        spot=Decimal(80),
        multiplier=Decimal(1000),
        beta=Decimal("0.1"),
        book=book(),
    )
    # $1,900 of credit against a $1,000 cap: not one full-size lot fits.
    assert plan.credit == Decimal(1900)
    assert plan.lots == 0
    assert any("/MCL" in n for n in plan.notes)


def test_the_11x_is_sized_on_its_trap() -> None:
    found = scanner.screen({"/MES": chart("/MES", RISING)}, {}, held=set(), last_opened={}, today=TODAY)
    c = next(x for x in found if x.setup == "11x")
    legs = [
        (LegSpec("Buy", OptionType.PUT, 1, delta=0.25), quote(OptionType.PUT, "7400", "-0.25", "60", dte=57)),
        (
            LegSpec("Sell", OptionType.PUT, 1, below=(0, Decimal(50))),
            quote(OptionType.PUT, "7350", "-0.21", "50", dte=57),
        ),
        (LegSpec("Sell", OptionType.PUT, 2, delta=0.05), quote(OptionType.PUT, "6500", "-0.05", "8", dte=57)),
    ]
    plan, _ = scanner.build_plan(
        c, legs, spot=Decimal(7700), multiplier=Decimal(5), beta=Decimal(1), book=book()
    )
    # Debit 10 points, two puts at 8: a 6-point credit, $30. Trap: 50 x $5 + $30.
    assert plan.credit == Decimal(30)
    assert plan.loss_at_stop == Decimal(280)
    assert plan.lots == 7  # $2,000 / $280
