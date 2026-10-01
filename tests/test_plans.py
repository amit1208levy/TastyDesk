"""A trade plan: his own lines, judged on his own numbers, quoted back to him."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from tastydesk.core import plans
from tastydesk.core.analytics import RuleSet
from tastydesk.core.models import Direction, Leg, OptionType, RiskProfile, Strategy, StrategyType
from tastydesk.core.pnl import compute_pnl
from tastydesk.core.risk import assess

TODAY = date(2026, 10, 1)


def _short_put(mark: str, delta: str | None = None) -> Strategy:
    leg = Leg(
        symbol="XLE   261218P00080000",
        instrument_type="Equity Option",
        underlying="XLE",
        direction=Direction.SHORT,
        quantity=D(1),
        option_type=OptionType.PUT,
        strike=D(80),
        expiration=date(2026, 12, 18),
        open_price=D("2.00"),
        mark=D(mark),
        delta=None if delta is None else D(delta),
        opened_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    return Strategy(
        id="p",
        account_number="A",
        underlying="XLE",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[leg],
        opened_at=datetime(2026, 9, 1, tzinfo=UTC),
        net_credit=D(200),
    )


def _verdict(strategy: Strategy, plan: plans.TradePlan | None):
    pnl = compute_pnl(strategy)
    risk = assess(strategy, pnl, None, TODAY, None)
    return plans.plan_verdict(strategy, pnl, risk, plan, RuleSet())


def test_his_own_profit_line_replaces_the_default() -> None:
    # Up 40% of the credit: under the default 50% it is "Leave it".
    trade = _short_put("1.20")
    assert _verdict(trade, None).action != "Take profit"
    plan = plans.from_dict({"take_profit": "0.35", "profit_note": "close it, sell the next month"})
    verdict = _verdict(trade, plan)
    assert verdict.action == "Take profit"
    assert "your plan: close it, sell the next month" in verdict.reason


def test_the_adjust_line_fires_on_short_delta() -> None:
    trade = _short_put("2.10", delta="-0.34")
    plan = plans.from_dict({"adjust_delta": "0.30", "adjust_note": "roll down and out"})
    verdict = _verdict(trade, plan)
    assert verdict.action == "Adjust"
    assert "roll down and out" in verdict.reason


def test_checks_say_where_each_line_stands() -> None:
    trade = _short_put("1.50")
    pnl = compute_pnl(trade)
    risk = assess(trade, pnl, None, TODAY, None)
    plan = plans.from_dict({"take_profit": "0.5", "stop_multiple": "2", "dte_exit": 21})
    lines = {c.key: c for c in plans.check(trade, pnl, risk, plan)}
    assert lines["profit"].reading == "25% now" and not lines["profit"].hit
    assert lines["loss"].reading == "not losing now"
    assert lines["time"].reading == "78 days left"


def test_nonsense_numbers_are_refused() -> None:
    with pytest.raises(ValueError):
        plans.from_dict({"take_profit": "50"})
    with pytest.raises(ValueError):
        plans.from_dict({"adjust_delta": "30"})


def test_the_plan_reads_back_as_promises() -> None:
    plan = plans.from_dict({"take_profit": "0.5", "loss_note": "no rolling for a debit"})
    lines = plans.sentences(plan)
    assert lines[0]["text"] == "I take profit at 50% of max profit."
    assert lines[1] == {"key": "loss", "text": "No rolling for a debit."}
