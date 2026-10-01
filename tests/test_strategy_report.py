"""Tests for the Performance tab's per-strategy deep dive.

The figures are worked out by hand. The test that carries the module's purpose
is the one where three big losses decide a strategy: the summary has to say so
in words, with the number it would have made without them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tastydesk.core.models import Direction, Leg, OptionType, RiskProfile, Strategy, StrategyType
from tastydesk.core.strategy_report import distribution, group, report

START = datetime(2026, 1, 5, 15, tzinfo=UTC)


def put(sid: str, pnl: str, *, day: int, credit: str = "100", underlying: str = "SPY") -> Strategy:
    """A naked put that took in ``credit`` and closed ``day`` days after START for ``pnl``."""
    leg = Leg(
        symbol=f"{underlying}-{sid}",
        instrument_type="Equity Option",
        underlying=underlying,
        direction=Direction.SHORT,
        quantity=Decimal(1),
        option_type=OptionType.PUT,
        strike=Decimal(500),
        expiration=date(2026, 12, 18),
        open_price=Decimal(credit) / 100,
    )
    opened = START + timedelta(days=day)
    return Strategy(
        id=sid,
        account_number="X",
        underlying=underlying,
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[leg],
        opened_at=opened,
        closed_at=opened + timedelta(days=10),
        net_credit=Decimal(credit),
        closing_cash_flow=Decimal(pnl) - Decimal(credit),
    )


def test_buckets_have_zero_on_an_edge_and_round_widths() -> None:
    bins = distribution([Decimal(v) for v in ("-450", "-120", "60", "80", "260")])
    assert any(b.low == 0 for b in bins)
    assert all(b.high - b.low == bins[0].high - bins[0].low for b in bins)
    assert sum(b.trades for b in bins) == 5
    # No bucket holds both a loss and a win.
    assert all(b.high <= 0 or b.low >= 0 for b in bins)


def test_outliers_fold_into_open_end_buckets_without_crossing_zero() -> None:
    values = [Decimal(50)] * 60 + [Decimal(-20), Decimal(-9000)]
    bins = distribution(values)
    assert bins[0].open_low
    assert bins[0].high <= 0
    assert bins[0].trades == 2
    # The ordinary winners are not crushed into one bar by the -$9,000 outlier.
    assert bins[-1].high - bins[-1].low < 1000


def test_a_strategy_decided_by_its_big_losses_says_so() -> None:
    wins = [put(f"w{i}", "100", day=i) for i in range(9)]
    losses = [put("l1", "-900", day=20), put("l2", "-700", day=30), put("l3", "-400", day=40)]
    r = report("ABA", "ABA", "SPY", wins + losses, breakdown_by="structure", today=date(2026, 6, 1))

    assert r.stats.trades == 12
    assert r.stats.total_pnl == Decimal(-1100)
    assert r.verdict == "losing money"
    assert [t.pnl for t in r.worst] == [Decimal(-900), Decimal(-700), Decimal(-400)]
    assert r.without_worst == Decimal(900)
    text = " ".join(r.summary)
    assert "The 3 worst trades lost $2,000. Without them it would be +$900" in text
    # $667 average loss against $100 average win: one loss eats about 7 wins.
    assert "6.7× bigger" in text and "about 7 wins" in text


def test_the_curve_runs_in_close_order_and_months_add_up() -> None:
    trades = [put("a", "100", day=0), put("b", "-50", day=40), put("c", "200", day=41)]
    r = report("x", "x", None, trades, breakdown_by="product", today=date(2026, 6, 1))
    assert [p.cumulative for p in r.curve] == [Decimal(100), Decimal(50), Decimal(250)]
    assert [(m.month, m.pnl, m.trades) for m in r.months] == [
        ("2026-01", Decimal(100), 1),
        ("2026-02", Decimal(150), 2),
    ]
    assert [(s.key, s.trades) for s in r.breakdown] == [("SPY", 3)]
    # Newest first in the full list.
    assert r.trades[0].id == "c"


def test_losses_past_toms_stop_are_measured_per_strategy() -> None:
    # A naked put's stop is 3x the credit: a $200 loss on $100 is at the stop,
    # a $500 loss is $300 beyond it.
    trades = [put("at", "-200", day=0), put("past", "-500", day=1), put("win", "100", day=2)]
    r = report("p", "p", None, trades, breakdown_by="product", today=date(2026, 6, 1))
    assert r.lens.past_stop == 1
    assert r.lens.beyond_stop == Decimal(300)


def test_grouping_by_product_folds_contract_months_together() -> None:
    trades = [put("a", "10", day=0, underlying="/ZBZ6"), put("b", "20", day=1, underlying="/ZBH7")]
    assert set(group(trades, "product")) == {"/ZB"}
    assert set(group(trades, "structure")) == {"Naked Put"}
