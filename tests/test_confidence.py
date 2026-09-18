"""Tests for how sure the app claims to be.

The rule these protect: a number the user would act on must not be produced
from evidence nobody checked. Agreement raises confidence, disagreement lowers
it, and evidence that could not be read costs something — while evidence that
does not exist for that shape at all costs nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from tastydesk.core.confidence import (
    DEFAULT_THRESHOLD,
    assess,
    profile_of,
)
from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)

OPEN = datetime(2026, 3, 2, 14, 30)


def leg(
    *,
    right: OptionType = OptionType.PUT,
    side: Direction = Direction.SHORT,
    strike: str = "100",
    expiration: date = date(2026, 6, 19),
    quantity: str = "1",
    delta: str | None = None,
) -> Leg:
    return Leg(
        symbol=f"/ZB-{right.value}-{strike}",
        instrument_type="Future Option",
        underlying="/ZBM6",
        direction=side,
        quantity=Decimal(quantity),
        multiplier=Decimal(1000),
        option_type=right,
        strike=Decimal(strike),
        expiration=expiration,
        open_price=Decimal("1.00"),
        delta=Decimal(delta) if delta is not None else None,
    )


def trade(
    *legs: Leg,
    tid: str = "t-1",
    underlying: str = "/ZBM6",
    opened: datetime = OPEN,
    structure: StrategyType = StrategyType.SHORT_STRANGLE,
    closed: datetime | None = None,
    short_delta: str | None = None,
) -> Strategy:
    return Strategy(
        id=tid,
        account_number="A1",
        underlying=underlying,
        strategy_type=structure,
        risk_profile=RiskProfile.UNDEFINED,
        legs=list(legs),
        opened_at=opened,
        closed_at=closed,
        short_delta_at_entry=Decimal(short_delta) if short_delta else None,
    )


def strangle(
    *,
    tid: str = "t",
    put: str = "100",
    call: str = "120",
    quantity: str = "1",
    opened: datetime = OPEN,
    expiration: date = date(2026, 6, 19),
    **kwargs: object,
) -> Strategy:
    return trade(
        leg(right=OptionType.PUT, strike=put, quantity=quantity, expiration=expiration),
        leg(right=OptionType.CALL, strike=call, quantity=quantity, expiration=expiration),
        tid=tid,
        opened=opened,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_strategy_with_no_trades_yet_claims_nothing() -> None:
    result = assess(strangle(), [], name="my strangle")
    assert result.confidence == 0.0
    assert result.unknowns


def test_the_same_trade_shape_clears_the_bar() -> None:
    members = [
        strangle(tid="m1", put="100", call="120"),
        strangle(tid="m2", put="98", call="122"),
    ]
    twin = strangle(tid="new", put="99", call="121")

    result = assess(twin, members, name="/ZB strangle")

    assert result.confidence >= DEFAULT_THRESHOLD
    assert result.verdict == "confident"
    assert "same legs, same ratio" in result.reasons


def test_a_different_structure_does_not_clear_the_bar() -> None:
    members = [strangle(tid="m1"), strangle(tid="m2")]
    naked = trade(
        leg(right=OptionType.PUT, strike="100"),
        tid="new",
        structure=StrategyType.NAKED_PUT,
    )

    result = assess(naked, members, name="/ZB strangle")

    assert result.confidence < DEFAULT_THRESHOLD
    assert any("different structure" in m for m in result.misses)


def test_the_standard_is_the_range_the_user_actually_traded() -> None:
    """68 to 105 days is his habit; 94 days is not an outlier inside it."""
    members = [
        strangle(tid="m1", expiration=date(2026, 5, 10)),  # 69 DTE
        strangle(tid="m2", expiration=date(2026, 6, 15)),  # 105 DTE
    ]
    middle = strangle(tid="new", expiration=date(2026, 6, 4))  # 94 DTE

    result = assess(middle, members, name="strangle")

    assert result.confidence >= DEFAULT_THRESHOLD
    assert not any("DTE" in m for m in result.misses)


def test_a_tenor_far_outside_that_range_is_called_out() -> None:
    members = [
        strangle(tid="m1", expiration=date(2026, 5, 10)),
        strangle(tid="m2", expiration=date(2026, 5, 15)),
    ]
    weekly = strangle(tid="new", expiration=date(2026, 3, 6))  # 4 DTE

    result = assess(weekly, members, name="strangle")

    assert result.confidence < DEFAULT_THRESHOLD
    assert any("DTE" in m for m in result.misses)


def test_strike_spacing_stands_in_for_delta_selection() -> None:
    members = [
        strangle(tid="m1", put="100", call="120"),  # ~18% wide
        strangle(tid="m2", put="102", call="118"),
    ]
    far_wider = strangle(tid="new", put="60", call="160")  # 91% wide

    result = assess(far_wider, members, name="strangle")

    assert result.confidence < DEFAULT_THRESHOLD
    assert any("apart" in m for m in result.misses)


def test_a_single_strike_costs_nothing_because_it_has_no_spacing() -> None:
    """A naked put has no width. That is not missing evidence."""
    members = [
        trade(leg(strike="100"), tid="m1", structure=StrategyType.NAKED_PUT),
        trade(leg(strike="98"), tid="m2", structure=StrategyType.NAKED_PUT),
    ]
    another = trade(leg(strike="99"), tid="new", structure=StrategyType.NAKED_PUT)

    result = assess(another, members, name="naked puts")

    assert result.confidence >= DEFAULT_THRESHOLD
    assert any("one strike" in n for n in result.not_applicable)
    assert not any("spacing" in u for u in result.unknowns)


def test_unreadable_evidence_costs_confidence() -> None:
    """Never recorded is not the same as checked and fine."""
    members = [
        strangle(tid="m1", short_delta="0.20"),
        strangle(tid="m2", short_delta="0.22"),
    ]
    with_delta = strangle(tid="a", short_delta="0.21")
    without = strangle(tid="b")

    assert assess(with_delta, members, name="x").confidence > assess(
        without, members, name="x"
    ).confidence
    assert any("delta" in u for u in assess(without, members, name="x").unknowns)


def test_a_name_that_contradicts_the_fills_caps_the_score() -> None:
    members = [
        trade(leg(strike="100"), tid="m1", structure=StrategyType.NAKED_PUT),
        trade(leg(strike="98"), tid="m2", structure=StrategyType.NAKED_PUT),
    ]
    same_shape = trade(leg(strike="99"), tid="new", structure=StrategyType.NAKED_PUT)

    honest = assess(same_shape, members, name="my puts")
    contradicted = assess(same_shape, members, name="my strangle")

    assert honest.confidence >= DEFAULT_THRESHOLD
    assert contradicted.confidence <= 0.90
    assert any("strangle" in m for m in contradicted.misses)


def test_a_rolled_trade_keeps_its_structure_but_loses_its_spacing() -> None:
    """Ten legs after four rolls is still a strangle; its width no longer reads."""
    members = [strangle(tid="m1"), strangle(tid="m2")]
    rolled = trade(
        leg(right=OptionType.PUT, strike="100"),
        leg(right=OptionType.PUT, strike="95"),
        leg(right=OptionType.PUT, strike="90"),
        leg(right=OptionType.CALL, strike="120"),
        leg(right=OptionType.CALL, strike="125"),
        tid="new",
        structure=StrategyType.SHORT_STRANGLE,
    )

    result = assess(rolled, members, name="strangle")

    assert any("same structure" in r for r in result.reasons)
    assert any("no longer reads" in u for u in result.unknowns)
    assert result.confidence < DEFAULT_THRESHOLD


def test_a_trade_opened_as_another_closed_is_flagged_as_a_roll() -> None:
    closed_at = datetime(2026, 3, 1, 15, 0)
    members = [
        strangle(tid="m1", closed=closed_at),
        strangle(tid="m2"),
    ]
    successor = strangle(tid="new", opened=closed_at + timedelta(hours=2))

    result = assess(successor, members, name="strangle")

    assert result.roll_of == "m1"
    assert any("roll" in r for r in result.reasons)


def test_another_product_is_not_a_weaker_match_but_no_match() -> None:
    members = [strangle(tid="m1"), strangle(tid="m2")]
    elsewhere = strangle(tid="new", underlying="/CLZ6")

    result = assess(elsewhere, members, name="strangle")

    assert result.confidence == 0.0
    assert any("different product" in m for m in result.misses)


def test_profile_recomputes_days_to_expiry_rather_than_trusting_the_record() -> None:
    """The stored field is only filled for new positions; history needs it too."""
    old = strangle(tid="old", opened=datetime(2024, 1, 3), expiration=date(2024, 3, 15))
    assert profile_of(old).dte_at_entry == 72
