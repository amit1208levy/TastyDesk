"""Tests for the Tom King page: his rules, his chart tests, and the prices behind them.

Every expectation is worked out by hand in the test. The ones that carry the
page's purpose are the size and stop checks — a strangle that is twice Tom's
size has to say so in dollars — and the add-to-a-loser finding, which is the
habit the page exists to catch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tastydesk.core import tom
from tastydesk.core.models import (
    DangerLevel,
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyRisk,
    StrategyType,
)
from tastydesk.core.pnl import compute_pnl
from tastydesk.core.prices import Bar, PriceHistory, parse_chart, range_for, yahoo_symbol

OPENED = datetime(2026, 9, 1, 14, 30, tzinfo=UTC)
EXPIRY = date(2026, 11, 20)


def leg(
    kind: OptionType | None,
    direction: Direction,
    strike: str | None,
    *,
    open_price: str,
    mark: str | None = None,
    quantity: str = "1",
    underlying: str = "SPY",
    expiration: date = EXPIRY,
    multiplier: str = "100",
    opened_at: datetime = OPENED,
    delta: str | None = None,
) -> Leg:
    return Leg(
        symbol=f"{underlying}-{kind.value if kind else 'F'}-{strike}-{expiration}",
        instrument_type="Equity Option" if kind else "Future",
        underlying=underlying,
        direction=direction,
        quantity=Decimal(quantity),
        multiplier=Decimal(multiplier),
        option_type=kind,
        strike=Decimal(strike) if strike else None,
        expiration=expiration if kind else None,
        open_price=Decimal(open_price),
        mark=Decimal(mark) if mark is not None else None,
        opened_at=opened_at,
        delta=Decimal(delta) if delta is not None else None,
    )


def trade(
    legs: list[Leg],
    strategy_type: StrategyType,
    *,
    sid: str = "t1",
    underlying: str | None = None,
    opened_at: datetime = OPENED,
    closed_at: datetime | None = None,
    closing_cash_flow: str = "0",
    roll_count: int = 0,
) -> Strategy:
    return Strategy(
        id=sid,
        account_number="X",
        underlying=underlying or legs[0].underlying,
        strategy_type=strategy_type,
        risk_profile=RiskProfile.UNDEFINED,
        legs=legs,
        opened_at=opened_at,
        closed_at=closed_at,
        net_credit=sum((lg.open_cash_flow for lg in legs), Decimal(0)),
        closing_cash_flow=Decimal(closing_cash_flow),
        roll_count=roll_count,
    )


def position(s: Strategy, name: str | None = None) -> tom.OpenPosition:
    risk = StrategyRisk(level=DangerLevel.OK, score=0.0, reasons=[], dte=(EXPIRY - date(2026, 9, 30)).days)
    return tom.OpenPosition(s, compute_pnl(s), risk, name)


def bars(closes: list[float], start: date = date(2026, 1, 1)) -> list[Bar]:
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(day=start + timedelta(days=i), open=c, high=c * 1.005, low=c * 0.995, close=c))
    return out


# --------------------------------------------------------------------------
# The chart
# --------------------------------------------------------------------------


def test_ema_is_seeded_with_the_simple_average() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    out = tom.ema(values, 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    # k = 2/(3+1) = 0.5: 4*0.5 + 2*0.5 = 3
    assert out[3] == pytest.approx(3.0)


def test_rsi_of_a_market_that_only_rises_is_100() -> None:
    out = tom.rsi([float(i) for i in range(30)])
    assert out[13] is None
    assert out[14] == 100.0
    assert out[-1] == 100.0


def test_parabolic_sar_flips_when_a_rally_breaks() -> None:
    series = bars([100 + i for i in range(30)] + [129 - 4 * i for i in range(10)])
    marks = tom.psar(series)
    assert marks[25] is not None and marks[25][1] is True
    assert marks[-1] is not None and marks[-1][1] is False


def test_a_steady_rally_is_bullish_on_all_four_of_toms_tests() -> None:
    series = tom.regime_series("SPY", bars([400 + i * 0.8 for i in range(120)]))
    regime = series.latest()
    assert regime is not None
    assert regime.label == "Bullish"
    assert regime.bullish == 4
    assert regime.focus == ("Standard 11x", "OTM DPMCC", "Naked puts")


def test_a_steady_decline_is_bearish_and_tom_cuts_buying_power() -> None:
    series = tom.regime_series("/ZB", bars([120 - i * 0.1 for i in range(120)]))
    regime = series.latest()
    assert regime is not None
    assert regime.label == "Bearish"
    assert regime.bullish == 0
    assert "Reduce buying power" in regime.focus
    # His put table assumes an uptrend; on a falling chart it says no.
    assert regime.put_zone.startswith("No new puts")


def test_regime_on_a_day_uses_only_the_bars_before_it() -> None:
    series = tom.regime_series("SPY", bars([float(100 + i) for i in range(80)]))
    i = series.last_before(date(2026, 3, 1))
    assert i is not None
    assert series.days[i] == date(2026, 2, 28)


# --------------------------------------------------------------------------
# Which playbook a trade is
# --------------------------------------------------------------------------


def eleven_x(underlying: str) -> Strategy:
    return trade(
        [
            leg(OptionType.PUT, Direction.LONG, "4000", open_price="50", underlying=underlying),
            leg(OptionType.PUT, Direction.SHORT, "3950", open_price="40", underlying=underlying),
            leg(OptionType.PUT, Direction.SHORT, "3500", open_price="6", quantity="2", underlying=underlying),
        ],
        StrategyType.CUSTOM,
    )


def test_a_put_debit_spread_paid_for_by_naked_puts_is_an_11x() -> None:
    assert tom.playbook_for(eleven_x("/ESZ6")).key == "11x"
    assert tom.playbook_for(eleven_x("/ESZ6")).tier == "core"
    # The same shape on oil is Tom's spec version, with a 1% loss cap.
    spec = tom.playbook_for(eleven_x("/CLZ6"))
    assert spec.key == "11x_spec"
    assert spec.max_loss_share == Decimal("0.01")


def test_short_options_in_two_months_are_still_a_strangle() -> None:
    s = trade(
        [
            leg(OptionType.PUT, Direction.SHORT, "72.5", open_price="1.03", quantity="2"),
            leg(
                OptionType.CALL,
                Direction.SHORT,
                "100",
                open_price="5.85",
                quantity="2",
                expiration=date(2027, 1, 15),
            ),
        ],
        StrategyType.CUSTOM,
    )
    assert tom.playbook_for(s).key == "strangle"


def test_long_puts_with_nearer_puts_sold_under_them_are_a_hedge() -> None:
    s = trade(
        [
            leg(
                OptionType.PUT,
                Direction.LONG,
                "7550",
                open_price="313",
                quantity="4",
                expiration=date(2026, 12, 18),
            ),
            leg(
                OptionType.PUT,
                Direction.SHORT,
                "7525",
                open_price="27",
                quantity="4",
                expiration=date(2026, 10, 16),
            ),
        ],
        StrategyType.DIAGONAL,
    )
    book = tom.playbook_for(s)
    assert book.key == "put_diagonal"
    assert book.tier == "hedge"


# --------------------------------------------------------------------------
# The open book
# --------------------------------------------------------------------------


def strangle(put_mark: str, call_mark: str) -> Strategy:
    # $6.00 + $4.00 = $1,000 of credit.
    return trade(
        [
            leg(OptionType.PUT, Direction.SHORT, "500", open_price="6.00", mark=put_mark, delta="-0.10"),
            leg(OptionType.CALL, Direction.SHORT, "700", open_price="4.00", mark=call_mark, delta="0.10"),
        ],
        StrategyType.SHORT_STRANGLE,
    )


def test_a_strangle_twice_toms_size_says_so_in_dollars() -> None:
    check = tom.check_position(
        position(strangle("6.00", "4.00")), net_liq=Decimal("50000"), regime=None, adds=[]
    )
    # Loss at a 2.5x stop is 1.5x the credit: $1,500, 3% of $50,000 against Tom's 1.5%.
    assert check.loss_at_stop == Decimal("1500.00")
    assert check.loss_at_stop_share == Decimal("0.03")
    size = [f for f in check.flags if f.code == "size"]
    assert size and size[0].level == "breach"
    assert "$1,500" in size[0].text


def test_a_strangle_that_costs_more_than_25x_its_credit_is_past_toms_stop() -> None:
    check = tom.check_position(
        position(strangle("20.00", "6.00")), net_liq=Decimal("500000"), regime=None, adds=[]
    )
    # $2,600 to close a $1,000 strangle: down 1.6x the credit against a 1.5x stop.
    assert check.stop_cost == Decimal("2500.00")
    assert check.stop_progress is not None and check.stop_progress > 1
    assert any(f.code == "stop" and f.level == "breach" for f in check.flags)


def test_a_winner_at_half_its_credit_is_at_toms_target() -> None:
    check = tom.check_position(
        position(strangle("3.00", "2.00")), net_liq=Decimal("500000"), regime=None, adds=[]
    )
    assert check.target_progress == Decimal(1)
    assert any(f.code == "target" for f in check.flags)


def test_outright_futures_cannot_be_sized_and_say_why() -> None:
    s = trade(
        [
            leg(
                None,
                Direction.LONG,
                None,
                open_price="106.5",
                mark="102.5",
                multiplier="1000",
                underlying="/ZBZ6",
            )
        ],
        StrategyType.FUTURE,
    )
    check = tom.check_position(position(s), net_liq=Decimal("90000"), regime=None, adds=[])
    assert check.loss_at_stop is None
    assert any(f.code == "size" and "outright futures" in f.text for f in check.flags)
    # Down $4,000, 4.4% of $90,000: more than the 2% Tom lets a whole trade lose.
    drawdown = [f for f in check.flags if f.code == "drawdown"]
    assert drawdown and drawdown[0].level == "breach"
    assert "$4,000" in drawdown[0].text


# --------------------------------------------------------------------------
# Adding to a loser
# --------------------------------------------------------------------------


def zb_chart() -> tom.Series:
    # Flat at 106 through Sept 1, then down half a point a day.
    closes = [106.0] * 244 + [106.0 - 0.5 * i for i in range(1, 30)]
    return tom.regime_series("/ZB", bars(closes))


def future(sid: str, opened: datetime) -> Strategy:
    return trade(
        [
            leg(
                None,
                Direction.LONG,
                None,
                open_price="106",
                multiplier="1000",
                underlying="/ZBZ6",
                opened_at=opened,
            )
        ],
        StrategyType.FUTURE,
        sid=sid,
        opened_at=opened,
    )


def test_buying_more_after_the_first_one_fell_is_adding_to_a_loser() -> None:
    first = future("a", datetime(2026, 9, 1, 15, tzinfo=UTC))
    second = future("b", datetime(2026, 9, 8, 15, tzinfo=UTC))
    found = tom.adds_to_losers([first, second], {"/ZB": zb_chart()})
    assert [a.strategy_id for a in found] == ["b"]
    assert found[0].move < -0.005
    assert "never adds to a losing position" in found[0].text


def test_buying_a_hedge_into_the_fall_is_not_averaging_down() -> None:
    first = future("a", datetime(2026, 9, 1, 15, tzinfo=UTC))
    hedge = trade(
        [
            leg(
                OptionType.PUT,
                Direction.LONG,
                "102",
                open_price="0.5",
                underlying="/ZBZ6",
                multiplier="1000",
                opened_at=datetime(2026, 9, 8, 15, tzinfo=UTC),
            )
        ],
        StrategyType.LONG_PUT,
        sid="h",
        opened_at=datetime(2026, 9, 8, 15, tzinfo=UTC),
    )
    assert tom.adds_to_losers([first, hedge], {"/ZB": zb_chart()}) == []


def test_a_same_day_add_is_not_judged() -> None:
    first = future("a", datetime(2026, 9, 1, 14, tzinfo=UTC))
    second = future("b", datetime(2026, 9, 1, 19, tzinfo=UTC))
    assert tom.adds_to_losers([first, second], {"/ZB": zb_chart()}) == []


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def closed_strangle(sid: str, closing: str) -> Strategy:
    return trade(
        [
            leg(OptionType.PUT, Direction.SHORT, "500", open_price="6.00"),
            leg(OptionType.CALL, Direction.SHORT, "700", open_price="4.00"),
        ],
        StrategyType.SHORT_STRANGLE,
        sid=sid,
        closed_at=OPENED + timedelta(days=20),
        closing_cash_flow=closing,
    )


def test_losses_over_2pct_of_the_account_on_the_day_are_counted() -> None:
    trades = [
        closed_strangle("big", "-2600"),
        closed_strangle("small", "-1300"),
        closed_strangle("win", "-500"),
    ]
    rules, _ = tom.history_rules(
        trades,
        series={},
        adds=[],
        net_liq=Decimal("100000"),
        net_liq_history={date(2026, 8, 31): Decimal("50000")},
        today=date(2026, 10, 1),
    )
    size = next(r for r in rules if r.key == "max_loss")
    # -$1,600 is 3.2% of the $50,000 the account was worth that day; -$300 is 0.6%.
    assert size.broken == 1
    assert size.pnl_broken == Decimal("-1600.00")
    assert size.status == "breach"


def test_the_part_of_a_loss_beyond_toms_stop_is_measured() -> None:
    trades = [closed_strangle("past", "-3000"), closed_strangle("at", "-2400")]
    rules, facts = tom.history_rules(
        trades, series={}, adds=[], net_liq=Decimal("1000000"), net_liq_history={}, today=date(2026, 10, 1)
    )
    stop = next(r for r in rules if r.key == "stop")
    # $2,000 lost against a $1,500 stop: $500 beyond it. $1,400 lost is inside the stop.
    assert stop.broken == 1 and stop.kept == 1
    assert facts["loss_beyond_stops"] == Decimal("500.00")


def test_rolled_trades_are_scored_against_the_ones_never_rolled() -> None:
    rolled = closed_strangle("r", "-2000")
    rolled.roll_count = 2
    rules, _ = tom.history_rules(
        [rolled, closed_strangle("n", "-500")],
        series={},
        adds=[],
        net_liq=Decimal("1000000"),
        net_liq_history={},
        today=date(2026, 10, 1),
    )
    rolling = next(r for r in rules if r.key == "rolling")
    assert (rolling.broken, rolling.pnl_broken) == (1, Decimal("-1000.00"))
    assert (rolling.kept, rolling.pnl_kept) == (1, Decimal("500.00"))


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------


def test_products_map_to_the_chart_that_answers_for_them() -> None:
    assert yahoo_symbol("/ZBZ6") == "ZB=F"
    # A micro reads the full-size chart: same index, deeper series.
    assert yahoo_symbol("/MESH7") == "ES=F"
    assert yahoo_symbol("SPX") == "^GSPC"
    assert yahoo_symbol("BRK/B") == "BRK-B"
    assert yahoo_symbol("/XYZZ6") is None


def test_the_chart_reaches_back_past_the_first_trade_with_room_to_warm_up() -> None:
    today = date(2026, 10, 1)
    assert range_for(None, today) == "1y"
    assert range_for(date(2026, 6, 1), today) == "1y"
    assert range_for(date(2025, 6, 25), today) == "2y"


def test_a_day_with_no_close_is_dropped_not_filled() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": -14400},
                    "timestamp": [1790688600, 1790775000, 1790861400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1.0, 2.0, 3.0],
                                "high": [1.5, None, 3.5],
                                "low": [0.5, None, 2.5],
                                "close": [1.2, None, 3.1],
                            }
                        ]
                    },
                }
            ]
        }
    }
    out = parse_chart(payload)
    assert [b.close for b in out] == [1.2, 3.1]
    # 13:30 UTC is 09:30 in New York, on the same calendar day.
    assert out[0].day == datetime.fromtimestamp(1790688600 - 14400, UTC).date()


async def test_a_product_with_no_chart_is_named_rather_than_shown_flat() -> None:
    calls: list[str] = []

    async def fetch(symbol: str, range_: str) -> dict:
        calls.append(symbol)
        if symbol == "ZB=F":
            raise RuntimeError("boom")
        return {
            "chart": {
                "result": [
                    {"timestamp": [1790688600], "indicators": {"quote": [{"close": [500.0]}]}, "meta": {}}
                ]
            }
        }

    prices = PriceHistory(fetch)
    out = await prices.daily(["SPY", "/ZBZ6", "/XYZZ6"])
    assert set(out) == {"SPY"}
    assert prices.errors["/ZB"] == "boom"
    assert "/XYZ" in prices.errors
    # A second ask inside the cache window does not go back out for SPY.
    await prices.daily(["SPY"])
    assert calls.count("SPY") == 1
