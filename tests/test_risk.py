"""Tests for the strategy-level risk scorer.

The headline test is :func:`test_defined_risk_spread_is_not_the_same_emergency`.
Everything else in this file exists to keep that one honest.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from tastydesk.core.models import (
    DangerLevel,
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyPnL,
    StrategyType,
    UnderlyingQuote,
)
from tastydesk.core.risk import DEFAULT_THRESHOLDS, assess

TODAY = date(2026, 9, 13)
OPENED = datetime(2026, 8, 20, 14, 30)


def option_leg(
    *,
    underlying: str = "SPY",
    direction: Direction = Direction.SHORT,
    option_type: OptionType = OptionType.PUT,
    strike: str = "580",
    expiration: date = date(2026, 10, 8),
    delta: str | None = None,
    open_price: str = "2.00",
    mark: str | None = None,
    quantity: str = "1",
) -> Leg:
    return Leg(
        symbol=f"{underlying}{expiration:%y%m%d}{option_type.value}{int(Decimal(strike) * 1000):08d}",
        instrument_type="Equity Option",
        underlying=underlying,
        direction=direction,
        quantity=Decimal(quantity),
        option_type=option_type,
        strike=Decimal(strike),
        expiration=expiration,
        open_price=Decimal(open_price),
        mark=Decimal(mark) if mark is not None else None,
        delta=Decimal(delta) if delta is not None else None,
    )


def strategy(
    *,
    strategy_type: StrategyType,
    risk_profile: RiskProfile,
    legs: list[Leg],
    underlying: str = "SPY",
    net_credit: str = "200",
    buying_power_used: str | None = None,
) -> Strategy:
    return Strategy(
        id="s-1",
        account_number="5WX00000",
        underlying=underlying,
        strategy_type=strategy_type,
        risk_profile=risk_profile,
        legs=legs,
        opened_at=OPENED,
        net_credit=Decimal(net_credit),
        buying_power_used=Decimal(buying_power_used) if buying_power_used is not None else None,
    )


def pnl(
    *,
    net_credit: str = "200",
    pct_of_credit: str | None = "-1.5",
    max_loss: str | None = None,
    pct_of_max_loss: str | None = None,
    open_pnl: str | None = "-300",
    legs: int = 2,
) -> StrategyPnL:
    return StrategyPnL(
        net_credit=Decimal(net_credit),
        cost_to_close=Decimal(open_pnl) - Decimal(net_credit) if open_pnl is not None else None,
        open_pnl=Decimal(open_pnl) if open_pnl is not None else None,
        pct_of_credit=Decimal(pct_of_credit) if pct_of_credit is not None else None,
        max_profit=Decimal(net_credit),
        max_loss=Decimal(max_loss) if max_loss is not None else None,
        pct_of_max_profit=None,
        pct_of_max_loss=Decimal(pct_of_max_loss) if pct_of_max_loss is not None else None,
        realized_pnl=Decimal("0"),
        is_credit=True,
        quoted_legs=legs,
        total_legs=legs,
    )


def text_of(risk) -> str:
    """Everything a human or a test could read off the result, as one string."""
    fields = (
        "level",
        "score",
        "dte",
        "worst_short_delta",
        "distance_to_short_pct",
        "distance_to_short_sigma",
        "breached_side",
        "pct_of_net_liq",
    )
    parts = [f"{r.code} {r.level} {r.message}" for r in risk.reasons]
    parts += [str(getattr(risk, name)) for name in fields]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# THE HEADLINE TEST
# ---------------------------------------------------------------------------


def test_defined_risk_spread_is_not_the_same_emergency() -> None:
    """The whole thesis of the application, in one assertion block.

    Same market, same -150% of the credit taken in. One structure has a floor
    under it and one does not, and the scorer has to say so. The short put of
    the spread is down 300% on its own; that number is meaningless and must not
    appear anywhere in the output.
    """
    quote = UnderlyingQuote(symbol="SPY", last=Decimal("576.40"), iv=Decimal("0.18"))

    short_put = option_leg(strike="580", delta="-0.62", open_price="4.00", mark="9.00")
    long_put = option_leg(
        strike="570", direction=Direction.LONG, delta="-0.38", open_price="2.00", mark="4.00"
    )
    spread = strategy(
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=RiskProfile.DEFINED,
        legs=[short_put, long_put],
    )
    # $2.00 credit on a 10-wide spread: max loss $800, currently down $300.
    spread_pnl = pnl(max_loss="-800", pct_of_max_loss="0.375")

    spread_risk = assess(spread, spread_pnl, quote, TODAY)

    assert spread_risk.level is not DangerLevel.CRITICAL

    moderation = [r for r in spread_risk.reasons if r.code == "defined_risk"]
    assert moderation, "a defined-risk spread must say out loud that its loss is capped"
    said = moderation[0].message.lower()
    assert "defined" in said
    assert "$800" in moderation[0].message
    assert "38%" in moderation[0].message

    # The short put alone is down 300%. Nothing may leak that number.
    assert "300" not in text_of(spread_risk)

    # Same loss against credit, no floor underneath it.
    strangle = strategy(
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[
            option_leg(strike="580", delta="-0.62", open_price="4.00", mark="9.00"),
            option_leg(strike="600", option_type=OptionType.CALL, delta="0.10", mark="0.40"),
        ],
    )
    strangle_risk = assess(strangle, pnl(), quote, TODAY)

    assert strangle_risk.score > spread_risk.score
    assert strangle_risk.level.rank >= spread_risk.level.rank


# ---------------------------------------------------------------------------
# the supporting cast
# ---------------------------------------------------------------------------


def test_clean_untested_position_scores_ok() -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.25"))
    expiry = date(2026, 10, 28)  # 45 DTE
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[
            option_leg(underlying="XYZ", strike="85", expiration=expiry, delta="-0.16", mark="0.90"),
            option_leg(
                underlying="XYZ",
                strike="115",
                option_type=OptionType.CALL,
                expiration=expiry,
                delta="0.16",
                mark="0.80",
            ),
        ],
    )
    risk = assess(trade, pnl(pct_of_credit="0.10", open_pnl="20"), quote, TODAY)

    assert risk.level is DangerLevel.OK
    assert risk.score < 15
    assert risk.breached is False
    assert risk.assignment_risk is False
    assert risk.pin_risk is False
    assert risk.worst_short_delta == Decimal("0.16")


def test_profit_target_is_a_management_signal_not_danger() -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.25"))
    expiry = date(2026, 10, 28)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="85", expiration=expiry, delta="-0.08", mark="0.20")],
    )
    risk = assess(trade, pnl(pct_of_credit="0.62", open_pnl="124", legs=1), quote, TODAY)

    assert risk.level is DangerLevel.OK
    codes = {r.code for r in risk.reasons}
    assert "profit_target" in codes


def test_21_dte_flag_lights_on_an_otherwise_quiet_trade() -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.20"))
    expiry = TODAY.fromordinal(TODAY.toordinal() + 20)  # 20 DTE
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="80", expiration=expiry, delta="-0.10", mark="0.30")],
    )
    risk = assess(trade, pnl(pct_of_credit="0.20", open_pnl="40", legs=1), quote, TODAY)

    assert risk.dte == 20
    assert risk.level is DangerLevel.WATCH
    gamma = [r for r in risk.reasons if r.code == "gamma_window"]
    assert gamma and "21" in gamma[0].message

    # One day the other side of the line and it stays quiet.
    far = TODAY.fromordinal(TODAY.toordinal() + 22)
    trade.legs[0].expiration = far
    assert assess(trade, pnl(pct_of_credit="0.20", open_pnl="40", legs=1), quote, TODAY).level is DangerLevel.OK


def test_expiry_week_with_a_tested_short_is_danger() -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.20"))
    expiry = TODAY.fromordinal(TODAY.toordinal() + 6)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="99", expiration=expiry, delta="-0.40", mark="1.10")],
    )
    risk = assess(trade, pnl(pct_of_credit="-0.80", open_pnl="-160", legs=1), quote, TODAY)

    assert risk.level is DangerLevel.DANGER
    assert any(r.code == "expiry_week" for r in risk.reasons)


@pytest.mark.parametrize(
    ("spot", "expected_side"),
    [("95", "put"), ("105", "call")],
)
def test_breach_detection_each_side(spot: str, expected_side: str) -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal(spot), iv=Decimal("0.20"))
    expiry = date(2026, 10, 28)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[
            option_leg(underlying="XYZ", strike="98", expiration=expiry, delta="-0.35", mark="3.00"),
            option_leg(
                underlying="XYZ",
                strike="102",
                option_type=OptionType.CALL,
                expiration=expiry,
                delta="0.35",
                mark="3.00",
            ),
        ],
    )
    risk = assess(trade, pnl(pct_of_credit="-0.60", open_pnl="-120"), quote, TODAY)

    assert risk.breached is True
    assert risk.breached_side == expected_side
    breach = [r for r in risk.reasons if r.code == "breached"]
    assert breach and breach[0].level is DangerLevel.DANGER
    assert expected_side in breach[0].message


def test_breach_detection_both_sides_when_inverted() -> None:
    """An inverted strangle (rolled through itself) can be breached both ways."""
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("97.50"), iv=Decimal("0.20"))
    expiry = date(2026, 10, 28)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[
            option_leg(underlying="XYZ", strike="100", expiration=expiry, delta="-0.60", mark="4.00"),
            option_leg(
                underlying="XYZ",
                strike="95",
                option_type=OptionType.CALL,
                expiration=expiry,
                delta="0.65",
                mark="4.20",
            ),
        ],
    )
    risk = assess(trade, pnl(pct_of_credit="-1.2", open_pnl="-240"), quote, TODAY)

    assert risk.breached_side == "both"
    breach = [r for r in risk.reasons if r.code == "breached"][0]
    assert "both" in breach.message


def test_sigma_distance_matches_a_hand_computed_value() -> None:
    """sigma = spot * iv * sqrt(dte/365); distance_in_sigma = distance / sigma.

    100 * 0.20 * sqrt(73/365) = 20 * sqrt(0.2) = 8.9442719...
    (100 - 95) / 8.9442719 = 0.5590169...
    """
    expiry = TODAY.fromordinal(TODAY.toordinal() + 73)
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.20"))
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="95", expiration=expiry, delta="-0.25", mark="1.50")],
    )
    risk = assess(trade, pnl(pct_of_credit="-0.40", open_pnl="-80", legs=1), quote, TODAY)

    assert risk.distance_to_short_pct == Decimal("0.0500")
    assert risk.distance_to_short_sigma == Decimal("0.5590")
    inside = [r for r in risk.reasons if r.code == "sigma_distance"]
    assert inside and inside[0].level is DangerLevel.TESTED


def test_sigma_is_none_rather_than_guessed_when_iv_is_missing() -> None:
    expiry = TODAY.fromordinal(TODAY.toordinal() + 73)
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=None)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="95", expiration=expiry, delta="-0.25", mark="1.50")],
    )
    risk = assess(trade, pnl(pct_of_credit="-0.40", open_pnl="-80", legs=1), quote, TODAY)

    assert risk.distance_to_short_sigma is None
    assert risk.distance_to_short_pct == Decimal("0.0500")
    assert not any(r.code == "sigma_distance" for r in risk.reasons)

    # No quote at all: no market-derived fields, and no invented ones.
    blind = assess(trade, pnl(pct_of_credit="-0.40", open_pnl="-80", legs=1), None, TODAY)
    assert blind.distance_to_short_pct is None
    assert blind.distance_to_short_sigma is None
    assert blind.breached is False


def test_short_itm_call_before_ex_dividend_is_assignment_risk() -> None:
    expiry = TODAY.fromordinal(TODAY.toordinal() + 20)
    ex_div = TODAY.fromordinal(TODAY.toordinal() + 5)
    quote = UnderlyingQuote(
        symbol="XYZ", last=Decimal("105"), iv=Decimal("0.22"), ex_dividend_date=ex_div
    )
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.COVERED_CALL,
        risk_profile=RiskProfile.DEFINED,
        legs=[
            option_leg(
                underlying="XYZ",
                strike="100",
                option_type=OptionType.CALL,
                expiration=expiry,
                delta="0.78",
                mark="5.60",
            )
        ],
    )
    risk = assess(trade, pnl(pct_of_credit="-1.1", open_pnl="-220", legs=1), quote, TODAY)

    assert risk.assignment_risk is True
    dividend = [r for r in risk.reasons if r.code.startswith("assignment_dividend")]
    assert dividend, "an ITM short call before ex-dividend must raise the dividend alarm"
    assert dividend[0].level is DangerLevel.DANGER
    assert "ex-dividend" in dividend[0].message

    # Same position, dividend already paid: still assignment risk, but calmer.
    quote.ex_dividend_date = TODAY.fromordinal(TODAY.toordinal() - 3)
    later = assess(trade, pnl(pct_of_credit="-1.1", open_pnl="-220", legs=1), quote, TODAY)
    assert later.assignment_risk is True
    assert not any(r.code.startswith("assignment_dividend") for r in later.reasons)


def test_pin_risk_on_expiry_day() -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100.20"), iv=Decimal("0.30"))
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="100", expiration=TODAY, delta="-0.50", mark="0.10")],
    )
    risk = assess(trade, pnl(pct_of_credit="0.90", open_pnl="180", legs=1), quote, TODAY)

    assert risk.pin_risk is True
    pin = [r for r in risk.reasons if r.code == "pin_risk"]
    assert pin and "expires today" in pin[0].message


def test_concentration_against_net_liq() -> None:
    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.20"))
    expiry = date(2026, 10, 28)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="80", expiration=expiry, delta="-0.12", mark="0.40")],
        buying_power_used="12000",
    )
    hot = assess(trade, pnl(pct_of_credit="0.10", open_pnl="20", legs=1), quote, TODAY, Decimal("100000"))
    assert hot.pct_of_net_liq == Decimal("0.1200")
    assert hot.level is DangerLevel.DANGER

    cool = assess(trade, pnl(pct_of_credit="0.10", open_pnl="20", legs=1), quote, TODAY, Decimal("400000"))
    assert cool.pct_of_net_liq == Decimal("0.0300")
    assert cool.level is DangerLevel.OK

    # No net liq on hand means no guess.
    assert assess(trade, pnl(pct_of_credit="0.10", open_pnl="20", legs=1), quote, TODAY).pct_of_net_liq is None


def test_defined_risk_stops_moderating_once_the_wing_stops_helping() -> None:
    """Moderation is not a permanent discount — it expires near max loss."""
    quote = UnderlyingQuote(symbol="SPY", last=Decimal("572.00"), iv=Decimal("0.18"))
    spread = strategy(
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=RiskProfile.DEFINED,
        legs=[
            option_leg(strike="580", delta="-0.82", open_price="4.00", mark="11.00"),
            option_leg(strike="570", direction=Direction.LONG, delta="-0.55", open_price="2.00", mark="5.00"),
        ],
    )
    deep = assess(spread, pnl(pct_of_credit="-3.2", max_loss="-800", pct_of_max_loss="0.80"), quote, TODAY)

    assert not any(r.code == "defined_risk" for r in deep.reasons)
    assert deep.level is DangerLevel.DANGER
    near_max = [r for r in deep.reasons if r.code == "near_max_loss"]
    assert near_max and near_max[0].level is DangerLevel.DANGER


def test_sitting_at_max_loss_is_critical() -> None:
    quote = UnderlyingQuote(symbol="SPY", last=Decimal("565.00"), iv=Decimal("0.18"))
    spread = strategy(
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=RiskProfile.DEFINED,
        legs=[
            option_leg(strike="580", delta="-0.95", open_price="4.00", mark="15.00"),
            option_leg(strike="570", direction=Direction.LONG, delta="-0.88", open_price="2.00", mark="8.00"),
        ],
    )
    risk = assess(spread, pnl(pct_of_credit="-3.9", max_loss="-800", pct_of_max_loss="0.97"), quote, TODAY)

    assert risk.level is DangerLevel.CRITICAL
    assert risk.score >= 85


def test_level_always_matches_the_worst_reason() -> None:
    """The headline number and the list under it must never disagree."""
    quote = UnderlyingQuote(symbol="SPY", last=Decimal("576.40"), iv=Decimal("0.18"))
    spread = strategy(
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=RiskProfile.DEFINED,
        legs=[
            option_leg(strike="580", delta="-0.62", open_price="4.00", mark="9.00"),
            option_leg(strike="570", direction=Direction.LONG, delta="-0.38", open_price="2.00", mark="4.00"),
        ],
    )
    risk = assess(spread, pnl(max_loss="-800", pct_of_max_loss="0.375"), quote, TODAY)

    assert risk.level is max((r.level for r in risk.reasons), key=lambda level: level.rank)
    assert [r.level.rank for r in risk.reasons] == sorted(
        (r.level.rank for r in risk.reasons), reverse=True
    )


def test_thresholds_are_tunable_without_touching_logic() -> None:
    from dataclasses import replace

    quote = UnderlyingQuote(symbol="XYZ", last=Decimal("100"), iv=Decimal("0.20"))
    expiry = TODAY.fromordinal(TODAY.toordinal() + 30)
    trade = strategy(
        underlying="XYZ",
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[option_leg(underlying="XYZ", strike="80", expiration=expiry, delta="-0.12", mark="0.40")],
    )
    patient = replace(DEFAULT_THRESHOLDS, gamma_dte=45)
    risk = assess(
        trade, pnl(pct_of_credit="0.10", open_pnl="20", legs=1), quote, TODAY, thresholds=patient
    )

    assert risk.level is DangerLevel.WATCH
    assert any(r.code == "gamma_window" for r in risk.reasons)
