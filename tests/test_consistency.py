"""One number, one value: the pages must not disagree about the same thing.

The Positions tiles once read an open P&L $1,400 away from the sum of the rows
beneath them, because the tile counted what earlier rolls had banked and the
rows did not. These tests hold the shared figures to one definition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace

from tastydesk.core.analytics import RuleSet
from tastydesk.core.models import Direction, Leg, OptionType, RiskProfile, Strategy, StrategyType
from tastydesk.core.pnl import compute_pnl
from tastydesk.service import DeskService, market_today


def _rolled_strangle() -> Strategy:
    """Four rolls banked money; the two legs open now are up $162.50."""

    def leg(strike: str, right: OptionType, sold: str, now: str) -> Leg:
        return Leg(
            symbol=f"./RTYZ6 R3EX6 261120{right.value}{strike}",
            instrument_type="Future Option",
            underlying="/RTYZ6",
            direction=Direction.SHORT,
            quantity=D(1),
            multiplier=D(50),
            option_type=right,
            strike=D(strike),
            open_price=D(sold),
            mark=D(now),
        )

    return Strategy(
        id="rty",
        account_number="A",
        underlying="/RTYZ6",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[
            leg("2500", OptionType.PUT, "13.50", "14.05"),
            leg("3100", OptionType.CALL, "10.00", "6.20"),
        ],
        opened_at=datetime(2026, 8, 1, tzinfo=UTC),
        net_credit=D("3197.34"),
        closing_cash_flow=D("-1083.44"),
        roll_count=4,
    )


class _Client:
    async def balances(self, account: object) -> SimpleNamespace:
        return SimpleNamespace(
            account_number="A",
            net_liquidating_value=D("100000"),
            cash_balance=D("90000"),
            used_derivative_buying_power=D("5000"),
            derivative_buying_power=D("95000"),
            maintenance_requirement=D("5000"),
        )

    async def prior_close_net_liq(self, account: object, today: object) -> D:
        return D("100000")


async def test_the_open_pnl_tile_is_the_sum_of_the_rows() -> None:
    trade = _rolled_strangle()
    service = DeskService.__new__(DeskService)
    service._client = _Client()
    service._rules = RuleSet()
    service._strategies = [trade]
    service._quotes = {}
    service._prior_net_liq_for = None
    service._prior_net_liq = None

    summary = await service._build_summary([object()], [trade])
    row = service._view(trade, market_today(), summary.net_liquidating_value)

    assert row.pnl.open_pnl == D("162.50")
    assert summary.open_pnl == row.pnl.open_pnl
    # And not the whole history of the trade, which counts the rolls.
    assert summary.open_pnl != compute_pnl(trade).open_pnl
