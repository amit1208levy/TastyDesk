"""Tests for the performance analytics — the "how do I improve" half of the desk.

Every expectation in this file is hand-computed and written out in the test so a
future change to :mod:`tastydesk.core.analytics` has to argue with arithmetic
rather than with a snapshot.

Conventions these tests pin down
-------------------------------
* Money is account cash flow (models.py): ``realized_pnl = net_credit +
  closing_cash_flow``, positive means cash was banked.
* ``expectancy = win_rate * avg_win - loss_rate * avg_loss`` with ``avg_loss`` a
  **positive magnitude** and ``loss_rate = losses / trades`` (scratches sit in
  the denominator of both rates). It must equal ``total_pnl / trades``.
* Two headline tests carry the module's reason to exist and should be the last
  things anyone weakens: ``test_pnl_per_bp_day_ranks_the_small_fast_winner_first``
  and ``test_winner_held_past_the_profit_target_is_a_violation``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from tastydesk.core.analytics import (
    DEFAULT_RULES,
    DTE_BUCKETS,
    IV_RANK_BUCKETS,
    SHORT_DELTA_BUCKETS,
    UNKNOWN_BUCKET,
    PerformanceStats,
    RuleSet,
    by_bucket,
    by_strategy_type,
    by_underlying,
    closed_strategies,
    max_profit_at_close,
    performance,
    rule_adherence,
)
from tastydesk.core.models import (
    ZERO,
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)

# One fixed calendar so every "days held" and "DTE at close" below is countable
# by hand. Jan 5 + 46 days == Feb 20, the expiration.
OPENED = datetime(2026, 1, 5, 14, 30)
EXPIRATION = date(2026, 2, 20)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def option_leg(expiration: date = EXPIRATION) -> Leg:
    """A short put. Analytics never reads leg detail, but Strategy.expirations does."""
    return Leg(
        symbol=f"SPY{expiration:%y%m%d}P00580000",
        instrument_type="Equity Option",
        underlying="SPY",
        direction=Direction.SHORT,
        quantity=Decimal(1),
        option_type=OptionType.PUT,
        strike=Decimal(580),
        expiration=expiration,
        open_price=Decimal("2.00"),
    )


def equity_leg(underlying: str = "SPY") -> Leg:
    """100 shares. No expiration, so the 21 DTE rule has nothing to judge."""
    return Leg(
        symbol=underlying,
        instrument_type="Equity",
        underlying=underlying,
        direction=Direction.LONG,
        quantity=Decimal(100),
        multiplier=Decimal(1),
    )


def trade(
    trade_id: str,
    *,
    credit: str = "100",
    pnl: str = "0",
    days_held: int = 10,
    buying_power: str | None = "500",
    strategy_type: StrategyType = StrategyType.PUT_CREDIT_SPREAD,
    underlying: str = "SPY",
    expiration: date | None = EXPIRATION,
    legs: list[Leg] | None = None,
    dte_at_entry: int | None = None,
    iv_rank_at_entry: str | None = None,
    short_delta_at_entry: str | None = None,
    still_open: bool = False,
) -> Strategy:
    """A closed trade that took in ``credit`` and banked ``pnl``.

    ``closing_cash_flow`` is derived rather than passed so the tests can state
    the two numbers a trader actually thinks in — "I sold it for 100 and kept
    90" — while the model keeps its own identity
    ``realized_pnl = net_credit + closing_cash_flow``.
    """
    if legs is None:
        legs = [option_leg(expiration)] if expiration is not None else [equity_leg(underlying)]
    return Strategy(
        id=trade_id,
        account_number="5WX00000",
        underlying=underlying,
        strategy_type=strategy_type,
        risk_profile=RiskProfile.DEFINED,
        legs=legs,
        opened_at=OPENED,
        closed_at=None if still_open else OPENED + timedelta(days=days_held),
        net_credit=Decimal(credit),
        closing_cash_flow=Decimal(pnl) - Decimal(credit),
        buying_power_used=None if buying_power is None else Decimal(buying_power),
        dte_at_entry=dte_at_entry,
        iv_rank_at_entry=None if iv_rank_at_entry is None else Decimal(iv_rank_at_entry),
        short_delta_at_entry=(None if short_delta_at_entry is None else Decimal(short_delta_at_entry)),
    )


def test_the_builder_honours_the_sign_convention() -> None:
    """Guard the guard: if this drifts every expectation below is meaningless."""
    won = trade("w", credit="100", pnl="90")
    assert won.net_credit == Decimal(100)
    assert won.closing_cash_flow == Decimal(-10)  # paid 10 to buy it back
    assert won.realized_pnl == Decimal(90)

    lost = trade("l", credit="100", pnl="-150")
    assert lost.closing_cash_flow == Decimal(-250)
    assert lost.realized_pnl == Decimal(-150)


# ---------------------------------------------------------------------------
# Scope: only closed trades count
# ---------------------------------------------------------------------------


def test_open_trades_are_ignored_not_zero_filled() -> None:
    """An open trade's realized_pnl is a partial number; averaging it in lies."""
    trades = [
        trade("open", credit="300", pnl="0", still_open=True),
        trade("done", credit="100", pnl="60"),
    ]
    stats = performance(trades)

    assert stats.trades == 1  # not 2, and the open trade is not a scratch
    assert stats.scratches == 0
    assert stats.total_pnl == Decimal(60)
    assert stats.expectancy == Decimal(60)


def test_rules_are_scored_over_closed_trades_only() -> None:
    trades = [trade("open", still_open=True), trade("done", credit="100", pnl="60")]
    for adherence in rule_adherence(trades).values():
        assert adherence.trades_considered == 1


def test_closed_strategies_come_back_in_close_order() -> None:
    """Ties break on id so the ordering is stable for the UI and for snapshots."""
    out = closed_strategies(
        [
            trade("b", days_held=9),
            trade("a", days_held=9),
            trade("c", days_held=1),
            trade("open", still_open=True),
        ]
    )
    assert [s.id for s in out] == ["c", "a", "b"]


def test_empty_input_gives_empty_stats_rather_than_a_crash() -> None:
    stats = performance([])
    assert stats == performance([trade("open", still_open=True)])
    assert stats.trades == 0
    assert stats.win_rate == 0.0
    assert stats.loss_rate == 0.0
    assert stats.expectancy == ZERO
    # Undefined, not zero: there is nothing to take a ratio or a maximum over.
    assert stats.profit_factor is None
    assert stats.pnl_per_bp_day is None
    assert stats.avg_days_in_trade is None
    assert stats.largest_win is None
    assert stats.largest_loss is None


# ---------------------------------------------------------------------------
# Expectancy and the win/loss/scratch split
# ---------------------------------------------------------------------------


def expectancy_set() -> list[Strategy]:
    """Four closed trades: +200, +100, -150, and one dead flat scratch.

    wins 2, losses 1, scratches 1, trades 4.
    """
    return [
        trade("win_big", credit="200", pnl="200"),
        trade("win_small", credit="100", pnl="100"),
        trade("loss", credit="100", pnl="-150"),
        trade("scratch", credit="100", pnl="0"),
    ]


def test_expectancy_over_a_known_set() -> None:
    """expectancy = win_rate * avg_win - loss_rate * avg_loss, avg_loss positive.

    By hand:
        win_rate  = 2/4 = 0.50      avg_win  = (200 + 100) / 2 = 150
        loss_rate = 1/4 = 0.25      avg_loss = 150 (magnitude of -150)
        expectancy = 0.50 * 150 - 0.25 * 150 = 75 - 37.50 = 37.50
    and the identity that keeps the long form honest:
        total_pnl / trades = 150 / 4 = 37.50
    """
    stats = performance(expectancy_set())

    assert stats.trades == 4
    assert stats.wins == 2
    assert stats.losses == 1
    assert stats.scratches == 1
    assert stats.win_rate == 0.5
    assert stats.loss_rate == 0.25

    assert stats.avg_win == Decimal(150)
    # Positive magnitude. A negative avg_loss here would flip the sign of the
    # whole expression and turn every losing system into a winning one.
    assert stats.avg_loss == Decimal(150)
    assert stats.avg_loss > ZERO

    assert stats.expectancy == Decimal("37.50")
    assert stats.total_pnl == Decimal(150)
    assert stats.expectancy == stats.total_pnl / Decimal(stats.trades)


def test_scratch_at_exactly_zero_is_neither_win_nor_loss() -> None:
    """Exactly 0 is its own bucket, and it still drags the win rate down.

    A trade that tied up buying power for a week to break even happened; hiding
    it would flatter every aggregate it touches.
    """
    stats = performance([trade("win", credit="100", pnl="100"), trade("flat", credit="100", pnl="0")])

    assert stats.wins == 1
    assert stats.losses == 0
    assert stats.scratches == 1
    assert stats.trades == 2
    assert stats.win_rate == 0.5  # not 1.0
    assert stats.total_pnl == Decimal(100)
    assert stats.expectancy == Decimal(50)


def test_a_one_cent_loss_is_a_loss_and_a_one_cent_win_is_a_win() -> None:
    """The boundary is strict: only exact zero scratches."""
    stats = performance(
        [trade("tiny_win", credit="100", pnl="0.01"), trade("tiny_loss", credit="100", pnl="-0.01")]
    )
    assert (stats.wins, stats.losses, stats.scratches) == (1, 1, 0)


def test_largest_win_and_loss_keep_their_natural_signs() -> None:
    stats = performance(expectancy_set())
    assert stats.largest_win == Decimal(200)
    assert stats.largest_loss == Decimal(-150)  # signed, not a magnitude


def test_largest_win_and_loss_are_none_when_the_side_is_empty() -> None:
    only_wins = performance([trade("w", credit="100", pnl="100")])
    assert only_wins.largest_win == Decimal(100)
    assert only_wins.largest_loss is None

    only_losses = performance([trade("l", credit="100", pnl="-100")])
    assert only_losses.largest_win is None
    assert only_losses.largest_loss == Decimal(-100)


# ---------------------------------------------------------------------------
# Profit factor
# ---------------------------------------------------------------------------


def test_profit_factor_is_gross_profit_over_gross_loss() -> None:
    """(200 + 100) / 150 = 2."""
    assert performance(expectancy_set()).profit_factor == Decimal(2)


def test_profit_factor_with_zero_losses_is_none() -> None:
    """Not infinity, not zero — undefined.

    Zero would sort the flawless run to the bottom of every "worst strategies"
    table; infinity would poison every sort it appears in.
    """
    stats = performance([trade("a", credit="100", pnl="100"), trade("b", credit="100", pnl="50")])

    assert stats.losses == 0
    assert stats.profit_factor is None
    assert stats.profit_factor != ZERO  # explicitly not the "terrible" reading


def test_a_scratch_does_not_conjure_a_denominator_for_profit_factor() -> None:
    """A 0.00 result is not a loss, so the ratio stays undefined."""
    stats = performance([trade("w", credit="100", pnl="100"), trade("z", credit="100", pnl="0")])
    assert stats.losses == 0
    assert stats.profit_factor is None


def test_profit_factor_is_zero_when_there_were_only_losses() -> None:
    """Zero has a meaning here — no gross profit at all — and None has another."""
    stats = performance([trade("l1", credit="100", pnl="-100")])
    assert stats.profit_factor == ZERO
    assert stats.profit_factor is not None


# ---------------------------------------------------------------------------
# P&L per buying-power-day: the metric the module exists for
# ---------------------------------------------------------------------------


def test_pnl_per_bp_day_ranks_the_small_fast_winner_first() -> None:
    """$50 on $500 over 10 days beats $80 on $2000 over 40 days.

    This is the whole reason a premium seller needs more than a win rate.

        FAST: 50 / (500 * 10)   = 50 / 5_000  = 0.010
        SLOW: 80 / (2000 * 40)  = 80 / 80_000 = 0.001

    FAST earns ten times as much per dollar of buying power per day, and the two
    metrics a journal usually shows rank them the other way round or not at all:
    both are 100% winners, and SLOW banked more dollars.
    """
    fast = trade("fast", underlying="FAST", credit="50", pnl="50", buying_power="500", days_held=10)
    slow = trade("slow", underlying="SLOW", credit="80", pnl="80", buying_power="2000", days_held=40)

    split = by_underlying([fast, slow])
    fast_stats, slow_stats = split["FAST"], split["SLOW"]

    # Win rate cannot see any difference at all.
    assert fast_stats.win_rate == slow_stats.win_rate == 1.0
    # Raw dollars actively rank them the wrong way.
    assert slow_stats.total_pnl > fast_stats.total_pnl

    assert fast_stats.pnl_per_bp_day == Decimal("0.010")
    assert slow_stats.pnl_per_bp_day == Decimal("0.001")

    # The ordering assertion is the point of the test, stated explicitly so it
    # cannot be softened into an equality check on one number.
    assert fast_stats.pnl_per_bp_day > slow_stats.pnl_per_bp_day
    ranked = sorted(split.items(), key=lambda kv: kv[1].pnl_per_bp_day or ZERO, reverse=True)
    assert [name for name, _ in ranked] == ["FAST", "SLOW"]


def test_trades_without_buying_power_are_excluded_not_counted_as_zero() -> None:
    """ "We do not know" is a different claim from "it earned nothing".

    Only the $60-on-$500-for-10-days trade has the inputs, so the answer is that
    trade's own 60 / 5_000 = 0.012 — undiluted — over a sample of one.
    """
    trades = [
        trade("no_bp", credit="100", pnl="100", buying_power=None),
        trade("zero_bp", credit="100", pnl="100", buying_power="0"),
        trade("known", credit="60", pnl="60", buying_power="500", days_held=10),
    ]
    stats = performance(trades)

    assert stats.trades == 3  # all three are still real closed trades
    assert stats.pnl_per_bp_day == Decimal("0.012")
    # Averaged over three it would be 0.004 — the number a zero-fill produces.
    assert stats.pnl_per_bp_day != Decimal("0.004")
    assert stats.pnl_per_bp_day_n == 1  # and the UI is told the sample is one


def test_pnl_per_bp_day_is_none_when_no_trade_has_buying_power() -> None:
    stats = performance([trade("a", buying_power=None), trade("b", buying_power=None)])
    assert stats.trades == 2
    assert stats.pnl_per_bp_day is None
    assert stats.pnl_per_bp_day_n == 0


def test_an_open_trade_has_no_days_held_so_it_cannot_reach_the_metric() -> None:
    """Days held is unknowable until the close, so the trade is skipped."""
    stats = performance(
        [
            trade("still_on", credit="100", pnl="100", buying_power="500", still_open=True),
            trade("done", credit="60", pnl="60", buying_power="500", days_held=10),
        ]
    )
    assert stats.pnl_per_bp_day_n == 1
    assert stats.days_in_trade_n == 1
    assert stats.pnl_per_bp_day == Decimal("0.012")


def test_intraday_trades_are_floored_at_one_day() -> None:
    """A 0-day hold divides by 1 day, not by ~0.

    Without the floor a 20-minute scalp returns a per-day figure in the hundreds
    and dominates every ranking it appears in.
    """
    stats = performance([trade("scalp", credit="50", pnl="50", buying_power="500", days_held=0)])
    assert stats.avg_days_in_trade == 0.0
    assert stats.pnl_per_bp_day == Decimal("0.1")  # 50 / (500 * 1)


def test_average_days_in_trade_reports_its_own_sample_size() -> None:
    stats = performance([trade("a", days_held=10), trade("b", days_held=20)])
    assert stats.avg_days_in_trade == 15.0
    assert stats.days_in_trade_n == 2


# ---------------------------------------------------------------------------
# Percent of max profit captured
# ---------------------------------------------------------------------------


def test_max_profit_is_the_credit_for_credit_structures_and_none_otherwise() -> None:
    assert max_profit_at_close(trade("c", strategy_type=StrategyType.IRON_CONDOR, credit="250")) == (
        Decimal(250)
    )
    # A debit structure's max profit depends on width and where the underlying
    # finished; neither is on the record, so None beats an invented denominator.
    assert max_profit_at_close(trade("d", strategy_type=StrategyType.PUT_DEBIT_SPREAD, credit="-100")) is None
    # A "credit" structure that was somehow opened for a debit is not one either.
    assert max_profit_at_close(trade("odd", strategy_type=StrategyType.NAKED_PUT, credit="-50")) is None


def test_pct_of_max_profit_covers_only_the_trades_with_a_knowable_denominator() -> None:
    """Credit trade: 60 / 100 = 0.6. The debit trade contributes nothing at all."""
    trades = [
        trade("credit", credit="100", pnl="60"),
        trade("debit", credit="-100", pnl="60", strategy_type=StrategyType.PUT_DEBIT_SPREAD),
    ]
    stats = performance(trades)

    assert stats.trades == 2
    assert stats.avg_pct_of_max_profit_captured == Decimal("0.6")
    assert stats.pct_of_max_profit_n == 1


def test_pct_of_max_profit_is_none_when_nothing_is_measurable() -> None:
    stats = performance([trade("d", credit="-100", pnl="60", strategy_type=StrategyType.LONG_CALL)])
    assert stats.avg_pct_of_max_profit_captured is None
    assert stats.pct_of_max_profit_n == 0


# ---------------------------------------------------------------------------
# Sample size is part of every answer
# ---------------------------------------------------------------------------


def test_every_aggregate_reports_its_sample_size() -> None:
    """A 100% win rate over three trades is not a finding, and n says so."""
    trades = [
        trade("a", credit="100", pnl="50", buying_power="500", days_held=10),
        trade("b", credit="100", pnl="50", buying_power=None, days_held=10),
        trade(
            "c",
            credit="-100",
            pnl="50",
            buying_power=None,
            strategy_type=StrategyType.CALL_DEBIT_SPREAD,
        ),
    ]
    stats = performance(trades)

    assert stats.win_rate == 1.0
    assert stats.trades == 3  # the denominator behind that 100%
    assert stats.is_significant is False  # three trades is not evidence

    # Each derived metric carries the size of ITS subset, not of the whole set.
    assert stats.pnl_per_bp_day_n == 1  # only "a" had buying power
    assert stats.pct_of_max_profit_n == 2  # "c" is a debit structure
    assert stats.days_in_trade_n == 3  # every closed trade has a duration


def test_significance_flag_flips_at_twenty_trades() -> None:
    nineteen = [trade(f"t{i}", credit="100", pnl="50") for i in range(19)]
    assert performance(nineteen).is_significant is False
    assert performance([*nineteen, trade("t19", credit="100", pnl="50")]).is_significant is True


def test_sample_sizes_survive_the_by_group_split() -> None:
    trades = [
        trade("a", underlying="SPY", credit="100", pnl="50"),
        trade("b", underlying="SPY", credit="100", pnl="-50"),
        trade("c", underlying="IWM", credit="100", pnl="50"),
    ]
    split = by_underlying(trades)
    assert split["SPY"].trades == 2
    assert split["IWM"].trades == 1
    assert sum(s.trades for s in split.values()) == 3


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_by_strategy_type_splits_and_orders_by_name() -> None:
    trades = [
        trade("a", strategy_type=StrategyType.PUT_CREDIT_SPREAD, credit="100", pnl="50"),
        trade("b", strategy_type=StrategyType.IRON_CONDOR, credit="200", pnl="-100"),
        trade("c", strategy_type=StrategyType.IRON_CONDOR, credit="200", pnl="100"),
    ]
    split = by_strategy_type(trades)

    assert list(split) == [StrategyType.IRON_CONDOR, StrategyType.PUT_CREDIT_SPREAD]
    assert split[StrategyType.IRON_CONDOR].trades == 2
    assert split[StrategyType.IRON_CONDOR].total_pnl == ZERO
    assert split[StrategyType.PUT_CREDIT_SPREAD].trades == 1


def test_by_underlying_is_alphabetical() -> None:
    trades = [trade("a", underlying="SPY"), trade("b", underlying="IWM"), trade("c", underlying="AAPL")]
    assert list(by_underlying(trades)) == ["AAPL", "IWM", "SPY"]


# ---------------------------------------------------------------------------
# Entry-condition buckets — boundaries pinned so they cannot drift
# ---------------------------------------------------------------------------


def test_twenty_one_dte_lands_in_the_8_21_bucket() -> None:
    """DTE ranges are whole days, inclusive at BOTH ends.

    21 DTE is the management line, so which side of the boundary it falls on
    decides what every "does the 21 DTE rule pay?" chart is actually comparing.
    """
    split = by_bucket([trade("at_21", dte_at_entry=21)], "dte_at_entry")
    assert list(split) == ["8-21"]
    assert split["8-21"].trades == 1

    split_22 = by_bucket([trade("at_22", dte_at_entry=22)], "dte_at_entry")
    assert list(split_22) == ["22-45"]


@pytest.mark.parametrize(
    ("dte", "expected"),
    [
        (0, "0-7"),
        (7, "0-7"),
        (8, "8-21"),
        (21, "8-21"),
        (22, "22-45"),
        (45, "22-45"),
        (46, "46-90"),
        (90, "46-90"),
        (91, "91+"),
        (400, "91+"),
    ],
)
def test_dte_bucket_boundaries(dte: int, expected: str) -> None:
    assert list(by_bucket([trade("t", dte_at_entry=dte)], "dte_at_entry")) == [expected]


def test_a_thirty_delta_short_strike_lands_in_the_riskiest_bucket() -> None:
    """0.30 delta is "0.30+", not "0.20-0.30".

    The continuous dimensions are lower-inclusive and upper-exclusive, so the
    boundary belongs to the riskier bucket — the direction to err in when the
    whole point of the split is finding where the losses come from.
    """
    split = by_bucket([trade("thirty", short_delta_at_entry="0.30")], "short_delta_at_entry")
    assert list(split) == ["0.30+"]
    assert split["0.30+"].trades == 1


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        ("0.05", "<0.10"),
        ("0.0999", "<0.10"),
        ("0.10", "0.10-0.20"),
        ("0.1999", "0.10-0.20"),
        ("0.20", "0.20-0.30"),
        ("0.2999", "0.20-0.30"),
        ("0.30", "0.30+"),
        ("0.45", "0.30+"),
        # A short put's delta is negative; only distance from the money matters.
        ("-0.30", "0.30+"),
        ("-0.16", "0.10-0.20"),
    ],
)
def test_short_delta_bucket_boundaries(delta: str, expected: str) -> None:
    assert list(by_bucket([trade("t", short_delta_at_entry=delta)], "short_delta_at_entry")) == [expected]


@pytest.mark.parametrize(
    ("iv_rank", "expected"),
    [
        ("0.19", "<20"),
        ("0.20", "20-35"),  # lower-inclusive
        ("0.3499", "20-35"),
        ("0.35", "35-50"),
        ("0.50", "50+"),
        # Hand-entered percents work too: anything above 1.0 cannot be a fraction.
        ("19", "<20"),
        ("35", "35-50"),
        ("62", "50+"),
    ],
)
def test_iv_rank_bucket_boundaries(iv_rank: str, expected: str) -> None:
    assert list(by_bucket([trade("t", iv_rank_at_entry=iv_rank)], "iv_rank_at_entry")) == [expected]


def test_missing_entry_data_lands_in_an_explicit_unknown_bucket() -> None:
    """Counts must still add up to the number of closed trades."""
    trades = [
        trade("a", dte_at_entry=30),
        trade("b", dte_at_entry=None),
        trade("c", dte_at_entry=None),
    ]
    split = by_bucket(trades, "dte_at_entry")

    assert split[UNKNOWN_BUCKET].trades == 2
    assert sum(s.trades for s in split.values()) == len(closed_strategies(trades))


def test_buckets_come_back_in_documented_order_with_empties_omitted() -> None:
    trades = [
        trade("late", dte_at_entry=100),
        trade("mid", dte_at_entry=30),
        trade("early", dte_at_entry=3),
        trade("nodata", dte_at_entry=None),
    ]
    labels = list(by_bucket(trades, "dte_at_entry"))

    assert labels == ["0-7", "22-45", "91+", UNKNOWN_BUCKET]
    assert "8-21" not in labels  # empty buckets are dropped, not zero-filled
    assert [x for x in labels if x != UNKNOWN_BUCKET] == [x for x in DTE_BUCKETS if x in labels]


def test_bucket_orders_are_the_exported_constants() -> None:
    assert DTE_BUCKETS == ("0-7", "8-21", "22-45", "46-90", "91+")
    assert IV_RANK_BUCKETS == ("<20", "20-35", "35-50", "50+")
    assert SHORT_DELTA_BUCKETS == ("<0.10", "0.10-0.20", "0.20-0.30", "0.30+")


def test_unknown_bucket_dimension_raises() -> None:
    with pytest.raises(ValueError, match="unknown bucket dimension"):
        by_bucket([trade("a")], "moon_phase_at_entry")


# ---------------------------------------------------------------------------
# Rule adherence — profit target
# ---------------------------------------------------------------------------


def test_winner_held_past_the_profit_target_is_a_violation() -> None:
    """Riding a winner from 50% to 90% of max profit breaks the rule.

    It still ended green, and that is exactly the point of measuring adherence:
    the rule exists to convert a 50% winner into cash and free the buying power.
    The trades that punish you for ignoring it are the ones that reversed — and
    by definition those are not in the winners list, so if a green outcome
    excused the behaviour the metric would always say "well done".

    Credit 100, kept 90 -> 90% of max profit, past the 65% ceiling (50% target
    plus a 15% band).
    """
    greedy = trade("greedy", credit="100", pnl="90")
    result = rule_adherence([greedy])["profit_target"]

    assert result.measurable is True
    assert result.violated == 1
    assert result.followed == 0
    assert result.not_applicable == 0
    assert result.adherence_rate == 0.0

    # The violated bucket holds a POSITIVE P&L. A green trade was a rule break.
    assert result.pnl_when_violated == Decimal(90)
    assert result.pnl_when_violated > ZERO
    assert greedy.realized_pnl > ZERO


@pytest.mark.parametrize(
    ("kept", "outcome"),
    [
        ("50", "followed"),  # exactly at the target
        ("55", "followed"),
        ("65", "followed"),  # exactly at the top of the band
        ("66", "violated"),  # one dollar past it
        ("90", "violated"),
    ],
)
def test_profit_target_band_boundaries(kept: str, outcome: str) -> None:
    """Closed between 50% and 65% of max profit counts as managed at the target.

    Without the band no real fill would ever score as compliant and the metric
    would say nothing at all.
    """
    result = rule_adherence([trade("t", credit="100", pnl=kept)])["profit_target"]
    assert (result.followed, result.violated) == ((1, 0) if outcome == "followed" else (0, 1))


def test_a_winner_that_never_reached_the_target_never_triggered_the_rule() -> None:
    """30% of max profit is not a violation — the rule never fired.

    Closing it early is the 21 DTE rule's business, not this one's.
    """
    result = rule_adherence([trade("early", credit="100", pnl="30")])["profit_target"]
    assert result.not_applicable == 1
    assert (result.followed, result.violated) == (0, 0)
    assert result.adherence_rate is None  # nothing was judged, so no rate
    assert result.judged == 0


def test_losers_and_debit_structures_are_not_applicable_to_the_profit_target() -> None:
    trades = [
        trade("loser", credit="100", pnl="-200"),
        trade("debit", credit="-100", pnl="150", strategy_type=StrategyType.CALL_DEBIT_SPREAD),
    ]
    result = rule_adherence(trades)["profit_target"]
    assert result.not_applicable == 2
    assert result.judged == 0


def test_profit_target_counterfactual_is_scoped_and_says_what_it_excluded() -> None:
    """Mechanical rule: close every qualifying winner at exactly 50% of credit.

    greedy kept 90 of 100, managed kept 55 of 100, early kept only 30.
        counterfactual  = 0.50 * 100 + 0.50 * 100 = 100
        actual (same 2) = 90 + 55 = 145
        delta           = 100 - 145 = -45
    "early" is excluded because the target never printed.
    """
    trades = [
        trade("greedy", credit="100", pnl="90"),
        trade("managed", credit="100", pnl="55"),
        trade("early", credit="100", pnl="30"),
    ]
    result = rule_adherence(trades)["profit_target"]

    assert result.followed == 1
    assert result.violated == 1
    assert result.adherence_rate == 0.5
    assert result.pnl_when_followed == Decimal(55)
    assert result.pnl_when_violated == Decimal(90)

    assert result.counterfactual_pnl == Decimal(100)
    assert result.counterfactual_actual_pnl == Decimal(145)
    assert result.counterfactual_delta == Decimal(-45)
    # Never show the counterfactual without these two numbers.
    assert result.counterfactual_trades == 2
    assert result.counterfactual_excluded == 1
    assert result.counterfactual_trades + result.counterfactual_excluded == result.trades_considered


def test_profit_target_thresholds_are_tunable() -> None:
    """Same trade, tighter rule: 90% capture is fine when the target IS 90%."""
    kept_90 = [trade("t", credit="100", pnl="90")]
    strict = RuleSet(profit_target_pct=Decimal("0.90"), profit_target_band=Decimal("0.05"))

    assert rule_adherence(kept_90)["profit_target"].violated == 1
    assert rule_adherence(kept_90, rules=strict)["profit_target"].followed == 1


# ---------------------------------------------------------------------------
# Rule adherence — 21 DTE
# ---------------------------------------------------------------------------


def test_closing_at_exactly_21_dte_follows_the_rule() -> None:
    """Closed 2026-01-30 against a 2026-02-20 expiration is exactly 21 DTE."""
    at_the_line = trade("line", days_held=25, credit="100", pnl="40")
    assert at_the_line.closed_at is not None
    assert (EXPIRATION - at_the_line.closed_at.date()).days == 21

    result = rule_adherence([at_the_line])["dte_exit"]
    assert result.followed == 1
    assert result.violated == 0
    assert dict(result.distribution)["21+"] == 1


def test_still_on_inside_21_dte_is_a_violation() -> None:
    """Closed 2026-02-14 is 6 DTE — neither closed nor rolled at the line."""
    late = trade("late", days_held=40, credit="100", pnl="40")
    result = rule_adherence([late])["dte_exit"]

    assert result.violated == 1
    assert result.followed == 0
    assert dict(result.distribution)["1-7"] == 1
    # Pricing the counterfactual exit needs a mark on the 21 DTE date, which
    # needs daily snapshots — so it is withheld, not guessed.
    assert result.counterfactual_pnl is None
    assert result.counterfactual_excluded == 1


@pytest.mark.parametrize(
    ("days_held", "dte_at_close", "expected_bin"),
    [
        (25, 21, "21+"),
        (26, 20, "8-20"),
        (38, 8, "8-20"),
        (39, 7, "1-7"),
        (45, 1, "1-7"),
        (46, 0, "0 or expired"),  # held to expiration
        (50, -4, "0 or expired"),  # assigned / settled after the fact
    ],
)
def test_dte_at_close_distribution_bins(days_held: int, dte_at_close: int, expected_bin: str) -> None:
    held = trade("t", days_held=days_held)
    assert held.closed_at is not None
    assert (EXPIRATION - held.closed_at.date()).days == dte_at_close

    result = rule_adherence([held])["dte_exit"]
    assert dict(result.distribution)[expected_bin] == 1
    assert sum(count for _, count in result.distribution) == 1


def test_equity_has_no_expiration_to_be_late_on() -> None:
    shares = trade(
        "shares",
        strategy_type=StrategyType.EQUITY,
        expiration=None,
        credit="-5000",
        pnl="200",
        days_held=5,
    )
    result = rule_adherence([shares])["dte_exit"]

    assert result.not_applicable == 1
    assert result.judged == 0
    assert result.adherence_rate is None
    assert sum(count for _, count in result.distribution) == 0


def test_dte_exit_is_tunable() -> None:
    """Closed at 21 DTE is a violation of a 45 DTE rule."""
    at_21 = [trade("t", days_held=25)]
    assert rule_adherence(at_21, rules=RuleSet(dte_exit=45))["dte_exit"].violated == 1


# ---------------------------------------------------------------------------
# Rule adherence — the 2x stop
# ---------------------------------------------------------------------------


def test_stop_rule_without_mae_reports_not_measurable_rather_than_guessing() -> None:
    """A closed trade shows where it ended, never how bad it got on the way.

    Max adverse excursion comes from daily snapshots of open P&L. Without them
    the honest answer is "not measurable yet" — scoring every trade as compliant
    would manufacture a 100% adherence rate out of missing data.
    """
    trades = [trade("a", credit="100", pnl="-300"), trade("b", credit="100", pnl="80")]
    result = rule_adherence(trades)["stop_loss"]  # mae_by_strategy omitted

    assert result.measurable is False
    assert result.unmeasurable_reason is not None
    assert "max adverse excursion" in result.unmeasurable_reason
    assert "snapshots" in result.unmeasurable_reason

    assert result.followed == 0
    assert result.violated == 0
    assert result.adherence_rate is None  # not 1.0, not 0.0
    assert result.trades_considered == 2
    assert result.not_applicable == 2
    assert result.pnl_when_followed == ZERO
    assert result.pnl_when_violated == ZERO
    assert result.counterfactual_pnl is None


def test_an_empty_mae_map_is_measurable_but_judges_nothing() -> None:
    """Supplying snapshots that cover no trade is a different state from none."""
    result = rule_adherence([trade("a", credit="100", pnl="-300")], mae_by_strategy={})["stop_loss"]
    assert result.measurable is True
    assert result.unmeasurable_reason is None
    assert result.not_applicable == 1
    assert result.adherence_rate is None


def test_a_trade_that_never_reached_the_stop_is_untouched_by_the_rule() -> None:
    """Credit 100 -> the stop sits at -200. A worst point of -50 never fired it."""
    result = rule_adherence([trade("a", credit="100", pnl="40")], mae_by_strategy={"a": Decimal("-50")})[
        "stop_loss"
    ]

    assert result.not_applicable == 1
    assert result.judged == 0
    assert result.counterfactual_trades == 0


def test_stopping_out_at_2x_the_credit_follows_the_rule() -> None:
    """Credit 100, worst point -250, closed at -200 = exactly 2x. Band is 1.5x-2.5x."""
    result = rule_adherence([trade("a", credit="100", pnl="-200")], mae_by_strategy={"a": Decimal("-250")})[
        "stop_loss"
    ]

    assert result.followed == 1
    assert result.violated == 0
    assert result.adherence_rate == 1.0
    assert result.pnl_when_followed == Decimal(-200)
    assert result.counterfactual_pnl == Decimal(-200)
    assert result.counterfactual_delta == ZERO


def test_holding_through_the_stop_is_a_violation_even_if_it_recovered() -> None:
    """Breached -200 and finished +90: bailed out, but the stop was ignored.

    Counterfactual: stopped at -200 instead of the +90 that actually landed, so
    the rule costs 290 on this trade. The note says why that flatters nothing.
    """
    result = rule_adherence(
        [trade("lucky", credit="100", pnl="90")], mae_by_strategy={"lucky": Decimal("-250")}
    )["stop_loss"]

    assert result.violated == 1
    assert result.followed == 0
    assert result.pnl_when_violated == Decimal(90)
    assert result.counterfactual_pnl == Decimal(-200)
    assert result.counterfactual_actual_pnl == Decimal(90)
    assert result.counterfactual_delta == Decimal(-290)
    assert result.counterfactual_trades == 1


def test_running_well_past_the_stop_is_the_same_violation() -> None:
    """Credit 100, stop -200, actually closed -600: outside the 2.5x band."""
    result = rule_adherence(
        [trade("blown", credit="100", pnl="-600")], mae_by_strategy={"blown": Decimal("-700")}
    )["stop_loss"]
    assert result.violated == 1
    assert result.pnl_when_violated == Decimal(-600)


def test_mae_magnitude_is_used_so_either_sign_convention_works() -> None:
    """A caller storing drawdowns as positive numbers gets the same answer."""
    negative = rule_adherence([trade("a", credit="100", pnl="-200")], mae_by_strategy={"a": Decimal("-250")})[
        "stop_loss"
    ]
    positive = rule_adherence([trade("a", credit="100", pnl="-200")], mae_by_strategy={"a": Decimal("250")})[
        "stop_loss"
    ]
    assert (negative.followed, negative.violated) == (positive.followed, positive.violated) == (1, 0)


def test_stop_multiple_is_tunable() -> None:
    """Closed at -200 on a 100 credit: compliant at 2x, way past a 1x stop."""
    trades = [trade("a", credit="100", pnl="-200")]
    mae = {"a": Decimal("-250")}
    assert rule_adherence(trades, mae_by_strategy=mae)["stop_loss"].followed == 1
    one_x = RuleSet(stop_loss_multiple=Decimal("1.0"))
    assert rule_adherence(trades, rules=one_x, mae_by_strategy=mae)["stop_loss"].violated == 1


# ---------------------------------------------------------------------------
# Rule adherence — shape of the result
# ---------------------------------------------------------------------------


def test_rule_adherence_returns_all_three_rules() -> None:
    result = rule_adherence([trade("a", credit="100", pnl="90")])
    assert set(result) == {"profit_target", "dte_exit", "stop_loss"}
    assert result["profit_target"].rule == "profit_target"
    assert result["dte_exit"].rule == "dte_exit"
    assert result["stop_loss"].rule == "stop_loss"
    for adherence in result.values():
        assert adherence.description  # every rule can state itself to the UI
        # and every rule explains what it could not see, either in its notes or
        # (when it could not run at all) in unmeasurable_reason.
        assert adherence.notes or adherence.unmeasurable_reason


def test_default_rules_are_the_users_stated_rules() -> None:
    assert DEFAULT_RULES.profit_target_pct == Decimal("0.50")
    assert DEFAULT_RULES.dte_exit == 21
    assert DEFAULT_RULES.stop_loss_multiple == Decimal("2.0")


def test_every_closed_trade_is_accounted_for_by_every_rule() -> None:
    """followed + violated + not_applicable must partition the closed trades.

    A trade that falls through the cracks is a trade the adherence rate quietly
    lies about.
    """
    trades = [
        trade("greedy", credit="100", pnl="90"),
        trade("managed", credit="100", pnl="55"),
        trade("loser", credit="100", pnl="-250", days_held=40),
        trade("debit", credit="-100", pnl="40", strategy_type=StrategyType.PUT_DEBIT_SPREAD),
        trade("shares", strategy_type=StrategyType.EQUITY, expiration=None, credit="-5000", pnl="10"),
        trade("open", still_open=True),
    ]
    mae = {"loser": Decimal("-400")}

    for name, adherence in rule_adherence(trades, mae_by_strategy=mae).items():
        assert adherence.trades_considered == 5, name  # the open trade is out
        assert adherence.followed + adherence.violated + adherence.not_applicable == 5, name
        assert adherence.judged == adherence.followed + adherence.violated, name


def test_performance_stats_is_immutable() -> None:
    """The UI gets a report, not a scratchpad it can edit into agreement."""
    stats = performance([trade("a", credit="100", pnl="50")])
    assert isinstance(stats, PerformanceStats)
    with pytest.raises(AttributeError):
        stats.win_rate = 1.0  # type: ignore[misc]


def test_an_unconfirmed_expiry_is_kept_out_of_every_total() -> None:
    """A win rate is worth nothing if one unresolved trade can swing it.

    The real case: a deep in-the-money LEAP closed by expiry with no closing
    row. Its recorded cash flows showed a $31,861 loss that never happened, and
    including it moved the year's realized figure by four times its true value.
    """
    from tastydesk.core.analytics import closed_strategies, performance, unverified_strategies

    real = trade("real", credit="500", pnl="800")
    guess = trade("guess", credit="-31860", pnl="-31860")
    guess.outcome_unverified = True

    book = [real, guess]

    assert [s.id for s in closed_strategies(book)] == ["real"]
    assert [s.id for s in unverified_strategies(book)] == ["guess"]

    stats = performance(book)

    assert stats.trades == 1
    assert stats.total_pnl == Decimal("800")
    # Counting the guess would have put the book deep in the red on one unknown.
    assert stats.total_pnl > Decimal(0)


def test_asking_for_them_explicitly_still_works() -> None:
    """Excluded from totals is not the same as hidden."""
    from tastydesk.core.analytics import closed_strategies

    guess = trade("guess", credit="-31860", pnl="-31860")
    guess.outcome_unverified = True

    assert [s.id for s in closed_strategies([guess], verified_only=False)] == ["guess"]
