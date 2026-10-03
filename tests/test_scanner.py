"""Tests for Tom's scanner: his checklists, the strike picker, and the sizing.

The sizing tests carry the point of the feature: a candidate is only useful if
its size already obeys Tom's caps for this account — 2% of net liq at his
stop, credit up to 1% on a strangle, buying power under 50%, and each kind of
trade inside its §8 share of that 50% (11x 30%, PMCC 40%, spec 30%).
"""

from __future__ import annotations

from dataclasses import replace
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
        "allocation_bp": {},
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
    charts = {"NVDA": chart("NVDA", FALLING), "SPY": chart("SPY", RISING)}
    metrics = {"NVDA": Metric(market_cap=Decimal(4e12), iv_rank=Decimal("0.5"))}
    found = scanner.screen(charts, metrics, held=set(), last_opened={}, today=TODAY)
    put = next(c for c in found if c.symbol == "NVDA" and c.setup == "naked_put")
    assert put.status == "watch"
    assert any(c.label == "Trending up" and c.ok is False and c.hard for c in put.checks)


def test_earnings_inside_the_trade_rules_a_put_out() -> None:
    charts = {"PLTR": chart("PLTR", RISING)}
    metrics = {"PLTR": Metric(market_cap=Decimal(4e11), earnings=TODAY + timedelta(days=20))}
    found = scanner.screen(charts, metrics, held=set(), last_opened={}, today=TODAY)
    put = next(c for c in found if c.symbol == "PLTR" and c.setup == "naked_put")
    earnings = next(c for c in put.checks if c.label == "No earnings before expiry")
    assert earnings.ok is False and earnings.hard


def test_a_strangle_is_blocked_by_a_position_that_moves_with_it() -> None:
    charts = {"/ES": chart("/ES", FLAT), "/GC": chart("/GC", FLAT)}
    found = scanner.screen(charts, {}, held={"/RTY"}, last_opened={}, today=TODAY)
    es = next(c for c in found if c.setup == "strangle" and c.symbol == "/ES")
    gold = next(c for c in found if c.setup == "strangle" and c.symbol == "/GC")
    assert es.status == "watch"
    assert "you hold /RTY" in es.checks[0].detail
    assert gold.status != "watch"


def test_strangles_only_on_the_products_tom_names() -> None:
    charts = {s: chart(s, FLAT) for s in ("/ES", "/CL", "/GC", "/ZB", "/6J", "/6E", "/ZW")}
    found = scanner.screen(charts, {}, held=set(), last_opened={}, today=TODAY)
    assert {c.symbol for c in found if c.setup == "strangle"} == {"/ES", "/CL", "/GC", "/ZB"}


def test_only_toms_own_tickers_are_scanned() -> None:
    charts = {s: chart(s, RISING) for s in ("AAPL", "MSFT", "/NG", "SPY")}
    found = scanner.screen(charts, {}, held=set(), last_opened={}, today=TODAY)
    assert {c.symbol for c in found if c.setup in ("naked_put", "pmcc", "strangle")} == {"SPY"}
    assert all(c.tom_list for c in found)


def test_the_11x_runs_on_the_biggest_instrument_the_account_can_carry() -> None:
    charts = {"SPY": chart("SPY", RISING)}
    small = scanner.screen(charts, {}, held=set(), last_opened={}, today=TODAY, net_liq=Decimal(100000))
    eleven = next(c for c in small if c.setup == "11x")
    # 2% of $100,000 is $2,000: SPX's $5,000 trap and /ES's $2,500 do not fit.
    assert eleven.symbol == "SPY"
    assert eleven.ladder == ["/MES"]
    assert "SPX" in eleven.alternatives[0] and "/ES" in eleven.alternatives[0]
    big = scanner.screen(charts, {}, held=set(), last_opened={}, today=TODAY, net_liq=Decimal(300000))
    assert next(c for c in big if c.setup == "11x").symbol == "SPX"


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
        book=book(net_liq=Decimal(300000)),
    )
    # $250 credit; a 3x stop loses $500 a lot; 2% of $300,000 is $6,000: 12 lots.
    assert plan.credit == Decimal(250)
    assert plan.loss_at_stop == Decimal(500)
    # Reg-T, broad-index rate: max(15% x 700 - 100, 10% x 600) + 2.50 = $62.50 a
    # share, $6,250 a lot.
    assert plan.bp_per_lot == Decimal("6250.00")
    # Tom allows himself 50% of $300,000 in buying power, $150,000; spec trades
    # get 30% of it over about five trades: 6%, $9,000 a trade — one lot,
    # tighter than the stop or the 50%.
    assert plan.lots == 1
    assert plan.lots_reason == "6% of allowed BP a trade: Tom's 30% for spec trades over 5 trades"
    assert fit.bp_ok is True
    assert fit.strategy_ok is True
    assert fit.strategy_name == "spec trades"
    # $6,250 of the $150,000 allowed.
    assert fit.strategy_after_share == Decimal("0.04166666666666666666666666667")


def test_a_full_spec_share_leaves_no_room_whatever_the_11x_uses() -> None:
    c = naked_put_candidate()
    leg = quote(OptionType.PUT, "600", "-0.13", "2.50")
    # $300,000 account: 30% of the $150,000 allowed is $45,000 for spec trades,
    # and $40,000 of it is already in use. The 11x's capital is its own share.
    full = book(net_liq=Decimal(300000), allocation_bp={"Spec": Decimal(40000), "11x": Decimal(40000)})
    plan, fit = scanner.build_plan(
        c,
        [(LegSpec("Sell", OptionType.PUT, 1, delta=0.13), leg)],
        spot=Decimal(700),
        multiplier=Decimal(100),
        beta=Decimal(1),
        book=full,
    )
    assert plan.lots == 0
    assert plan.lots_reason == "Tom's 30% of allowed BP for spec trades"
    assert plan.oversize == "$6,250 of buying power a lot; $5,000 is left of Tom's 30% for spec trades"
    assert fit.strategy_ok is False


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
    found = scanner.screen(
        {"SPY": chart("SPY", RISING)}, {}, held=set(), last_opened={}, today=TODAY, net_liq=Decimal(100000)
    )
    c = next(x for x in found if x.setup == "11x")
    assert c.symbol == "SPY"
    legs = [
        (
            LegSpec("Buy", OptionType.PUT, 1, delta=0.25),
            quote(OptionType.PUT, "740", "-0.25", "6.00", dte=57),
        ),
        (
            LegSpec("Sell", OptionType.PUT, 1, below=(0, Decimal(5))),
            quote(OptionType.PUT, "735", "-0.21", "5.00", dte=57),
        ),
        (
            LegSpec("Sell", OptionType.PUT, 2, delta=0.05),
            quote(OptionType.PUT, "650", "-0.05", "0.80", dte=57),
        ),
    ]
    plan, _ = scanner.build_plan(
        c, legs, spot=Decimal(770), multiplier=Decimal(100), beta=Decimal(1), book=book()
    )
    # Debit $1.00 a share, two puts at $0.80: a $0.60 credit, $60 a lot.
    # Trap: 5 points x $100 + $60 = $560. 2% of $100,000 is $2,000: three lots
    # by the stop...
    assert plan.credit == Decimal(60)
    assert plan.loss_at_stop == Decimal(560)
    # ...but Reg-T wants the spread's $100 debit plus, for each naked put, the
    # broad-index 15% rule: max(115.50 - 120, 65) + 0.80 = $65.80 a share.
    assert plan.bp_per_lot == Decimal("13260.00")
    # One 11x may use 7.5% of the buying power Tom allows (30% over about
    # four), $3,750 of the $50,000 here: not one SPY lot. This is the trade a
    # /MES 11x replaces.
    assert plan.lots == 0
    assert plan.lots_reason == "7.5% of allowed BP a trade: Tom's 30% for 11x over 4 trades"
    # The card names the cap that is broken, not the one that is met.
    assert plan.oversize == "$13,260 of buying power a lot; one trade may use $3,750 (7.5% of allowed BP)"


def test_a_put_spread_is_capped_by_its_full_width_not_its_stop() -> None:
    found = scanner.screen({"SPX": chart("SPX", FLAT)}, {}, held=set(), last_opened={}, today=TODAY)
    c = next(x for x in found if x.setup == "spx_pcs")
    legs = [
        (
            LegSpec("Sell", OptionType.PUT, 1, delta=0.09),
            quote(OptionType.PUT, "7500", "-0.09", "3.00", dte=5),
        ),
        (
            LegSpec("Buy", OptionType.PUT, 1, below=(0, Decimal(20))),
            quote(OptionType.PUT, "7480", "-0.07", "2.00", dte=5),
        ),
    ]
    plan, _ = scanner.build_plan(
        c, legs, spot=Decimal(7700), multiplier=Decimal(100), beta=Decimal(1), book=book()
    )
    # $100 credit; $150 lost at the 2.5x stop, but $1,900 if it runs through both
    # strikes. 2% of $100,000 is $2,000: one lot, not thirteen.
    assert plan.lots == 1
    assert plan.lots_reason == "max loss up to 2% of net liq"


def strangle_legs(put_iv: str = "0.25", call_iv: str = "0.20") -> list[scanner.SpanLeg]:
    years = Decimal(50) / Decimal(365)
    return [
        (OptionType.PUT, Decimal(2500), Decimal(-1), Decimal(put_iv), years, Decimal(50)),
        (OptionType.CALL, Decimal(3050), Decimal(-1), Decimal(call_iv), years, Decimal(50)),
    ]


def test_span_finds_its_own_scan_width_back() -> None:
    requirement, _ = scanner.span_requirement(strangle_legs(), Decimal(2820), 8.0)
    fitted = scanner.fit_span_k(strangle_legs(), Decimal(2820), requirement)
    assert fitted is not None and abs(fitted - 8.0) < 0.01


def test_futures_buying_power_is_span_less_what_the_options_are_worth() -> None:
    requirement, value = scanner.span_requirement(strangle_legs(), Decimal(2820), 8.0)
    # Short options: the position's value is a liability, so it is negative and
    # brings buying power below the requirement — tastytrade's own report shows
    # that difference to the dollar.
    assert value < 0 < requirement
    put = quote(OptionType.PUT, "2500", "-0.08", "13.5", dte=50)
    call = quote(OptionType.CALL, "3050", "0.09", "10.5", dte=50)
    legs = [
        (
            LegSpec("Sell", OptionType.PUT, 1, delta=0.08),
            replace(put, iv=Decimal("0.25")),
        ),
        (
            LegSpec("Sell", OptionType.CALL, 1, delta=0.09),
            replace(call, iv=Decimal("0.20")),
        ),
    ]
    bp, basis = scanner.buying_power(
        "/RTY", "strangle", legs, spot=Decimal(2820), multiplier=Decimal(50), credit=Decimal(1200), span_k=8.0
    )
    assert bp == requirement + value
    assert basis.startswith("SPAN estimate")
