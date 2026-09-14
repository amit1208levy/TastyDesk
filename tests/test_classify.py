"""Classifier tests: one per structure, plus the ways a classifier normally lies.

The two failure modes that matter are (a) reading the broker's leg order as
meaning, and (b) counting legs instead of contracts — a 2x1 ratio has a naked
short inside it and must never be reported as a credit spread.
"""

from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal

import pytest

from tastydesk.core.classify import classify
from tastydesk.core.models import Direction, Leg, OptionType, RiskProfile, StrategyType
from tastydesk.core.occ import build_occ_symbol

NEAR = date(2025, 12, 19)
FAR = date(2026, 1, 16)
UNDERLYING = "SPY"


def opt(
    option_type: str,
    strike: float | int,
    direction: str,
    quantity: int = 1,
    expiration: date = NEAR,
    underlying: str = UNDERLYING,
) -> Leg:
    """One option leg. ``direction`` is "S" or "L"; strike is a plain number."""
    strike_dec = Decimal(str(strike))
    kind = OptionType.CALL if option_type.upper().startswith("C") else OptionType.PUT
    return Leg(
        symbol=build_occ_symbol(underlying, expiration, kind.value, strike_dec),
        instrument_type="Equity Option",
        underlying=underlying,
        direction=Direction.SHORT if direction.upper().startswith("S") else Direction.LONG,
        quantity=Decimal(quantity),
        multiplier=Decimal(100),
        option_type=kind,
        strike=strike_dec,
        expiration=expiration,
        open_price=Decimal("1.50"),
    )


def sp(strike, qty=1, exp=NEAR):
    return opt("P", strike, "S", qty, exp)


def lp(strike, qty=1, exp=NEAR):
    return opt("P", strike, "L", qty, exp)


def sc(strike, qty=1, exp=NEAR):
    return opt("C", strike, "S", qty, exp)


def lc(strike, qty=1, exp=NEAR):
    return opt("C", strike, "L", qty, exp)


def shares(quantity: int, direction: str = "L", underlying: str = UNDERLYING) -> Leg:
    return Leg(
        symbol=underlying,
        instrument_type="Equity",
        underlying=underlying,
        direction=Direction.SHORT if direction.upper().startswith("S") else Direction.LONG,
        quantity=Decimal(quantity),
        multiplier=Decimal(1),
        open_price=Decimal("580"),
    )


# --- the named structures ------------------------------------------------


def test_short_strangle():
    assert classify([sp(560), sc(600)]) == (StrategyType.SHORT_STRANGLE, RiskProfile.UNDEFINED)


def test_short_straddle():
    assert classify([sp(580), sc(580)]) == (StrategyType.SHORT_STRADDLE, RiskProfile.UNDEFINED)


def test_long_strangle():
    assert classify([lp(560), lc(600)]) == (StrategyType.LONG_STRANGLE, RiskProfile.DEFINED)


def test_long_straddle():
    assert classify([lp(580), lc(580)]) == (StrategyType.LONG_STRADDLE, RiskProfile.DEFINED)


def test_put_credit_spread():
    assert classify([sp(560), lp(550)]) == (StrategyType.PUT_CREDIT_SPREAD, RiskProfile.DEFINED)


def test_call_credit_spread():
    assert classify([sc(600), lc(610)]) == (StrategyType.CALL_CREDIT_SPREAD, RiskProfile.DEFINED)


def test_put_debit_spread():
    assert classify([lp(580), sp(570)]) == (StrategyType.PUT_DEBIT_SPREAD, RiskProfile.DEFINED)


def test_call_debit_spread():
    assert classify([lc(580), sc(590)]) == (StrategyType.CALL_DEBIT_SPREAD, RiskProfile.DEFINED)


def test_iron_condor():
    legs = [lp(550), sp(560), sc(600), lc(610)]
    assert classify(legs) == (StrategyType.IRON_CONDOR, RiskProfile.DEFINED)


def test_iron_fly():
    legs = [lp(550), sp(580), sc(580), lc(610)]
    assert classify(legs) == (StrategyType.IRON_FLY, RiskProfile.DEFINED)


def test_jade_lizard_is_undefined_because_the_put_is_naked():
    legs = [sp(560), sc(600), lc(610)]
    assert classify(legs) == (StrategyType.JADE_LIZARD, RiskProfile.UNDEFINED)


def test_naked_put():
    assert classify([sp(560)]) == (StrategyType.NAKED_PUT, RiskProfile.UNDEFINED)


def test_naked_call():
    assert classify([sc(600)]) == (StrategyType.NAKED_CALL, RiskProfile.UNDEFINED)


def test_long_call():
    assert classify([lc(600)]) == (StrategyType.LONG_CALL, RiskProfile.DEFINED)


def test_long_put():
    assert classify([lp(560)]) == (StrategyType.LONG_PUT, RiskProfile.DEFINED)


def test_covered_call():
    assert classify([shares(100), sc(600)]) == (StrategyType.COVERED_CALL, RiskProfile.DEFINED)


def test_covered_call_needs_one_hundred_shares_per_contract():
    # 100 shares behind two short calls leaves one of them naked.
    assert classify([shares(100), sc(600, qty=2)]) == (StrategyType.CUSTOM, RiskProfile.UNDEFINED)


def test_calendar_is_undefined_despite_the_long_leg():
    legs = [sc(600, exp=NEAR), lc(600, exp=FAR)]
    assert classify(legs) == (StrategyType.CALENDAR, RiskProfile.UNDEFINED)


def test_diagonal_is_undefined():
    legs = [sc(600, exp=NEAR), lc(610, exp=FAR)]
    assert classify(legs) == (StrategyType.DIAGONAL, RiskProfile.UNDEFINED)


def test_ratio_spread():
    # Two short puts against one long put: one put is naked.
    assert classify([sp(560, qty=2), lp(550)]) == (StrategyType.RATIO_SPREAD, RiskProfile.UNDEFINED)


def test_backspread_is_a_defined_ratio():
    # One short, two longs below it: the extra long only helps.
    assert classify([sp(560), lp(550, qty=2)]) == (StrategyType.RATIO_SPREAD, RiskProfile.DEFINED)


def test_equity_only():
    assert classify([shares(100)]) == (StrategyType.EQUITY, RiskProfile.DEFINED)


def test_short_equity_is_undefined():
    assert classify([shares(100, "S")]) == (StrategyType.EQUITY, RiskProfile.UNDEFINED)


# --- the traps -----------------------------------------------------------


def test_ratio_is_not_a_credit_spread():
    """The whole point of counting contracts, stated as its own test."""
    ratio, _ = classify([sp(560, qty=2), lp(550)])
    assert ratio is not StrategyType.PUT_CREDIT_SPREAD
    assert classify([sp(560), lp(550)])[0] is StrategyType.PUT_CREDIT_SPREAD


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_leg_order_never_changes_the_answer(order):
    legs = [lp(550), sp(560), sc(600), lc(610)]
    shuffled = [legs[i] for i in order]
    assert classify(shuffled) == (StrategyType.IRON_CONDOR, RiskProfile.DEFINED)


def test_shuffled_jade_lizard():
    for order in itertools.permutations([sp(560), sc(600), lc(610)]):
        assert classify(list(order)) == (StrategyType.JADE_LIZARD, RiskProfile.UNDEFINED)


def test_odd_five_leg_combination_stays_custom():
    # A condor with a stray extra short put bolted on: not an iron condor any
    # more, and nothing else either. Naming it would invent a max loss.
    legs = [lp(550), sp(560), sc(600), lc(610), sp(540)]
    assert classify(legs) == (StrategyType.CUSTOM, RiskProfile.UNDEFINED)


def test_five_leg_all_defined_combination_is_still_custom():
    legs = [lp(550), sp(560), sc(600), lc(610), lc(620)]
    strategy_type, risk = classify(legs)
    assert strategy_type is StrategyType.CUSTOM
    assert risk is RiskProfile.DEFINED  # unnamed, but every short is still covered


def test_two_underlyings_is_never_a_structure():
    legs = [sp(560), opt("C", 600, "S", underlying="QQQ")]
    assert classify(legs) == (StrategyType.CUSTOM, RiskProfile.UNDEFINED)


def test_partial_close_nets_down_to_the_remaining_position():
    # Sold two puts, bought one back: what is left is a single naked put.
    legs = [sp(560, qty=2), lp(560)]
    assert classify(legs) == (StrategyType.NAKED_PUT, RiskProfile.UNDEFINED)


def test_missing_strike_is_not_guessed():
    broken = sp(560)
    broken.strike = None
    assert classify([broken, sc(600)]) == (StrategyType.CUSTOM, RiskProfile.UNDEFINED)


def test_no_legs():
    assert classify([]) == (StrategyType.CUSTOM, RiskProfile.UNDEFINED)


def test_inverted_iron_condor_is_custom():
    # Short put above the short call: a real position, but not an iron condor.
    legs = [lp(550), sp(600), sc(560), lc(610)]
    assert classify(legs)[0] is StrategyType.CUSTOM


def test_unequal_quantities_break_the_iron_condor():
    legs = [lp(550), sp(560, qty=2), sc(600), lc(610)]
    strategy_type, risk = classify(legs)
    assert strategy_type is StrategyType.CUSTOM
    assert risk is RiskProfile.UNDEFINED  # one of the two short puts is naked


def test_big_lizard_is_still_a_jade_lizard():
    # Short straddle with the call side capped: same shape, same naked put.
    legs = [sp(580), sc(580), lc(600)]
    assert classify(legs) == (StrategyType.JADE_LIZARD, RiskProfile.UNDEFINED)


def test_reverse_jade_lizard_is_not_named():
    # Long put under the short put instead of a long call over the short call:
    # the naked side is the calls, and that structure has no entry in the enum.
    legs = [sp(560), sc(600), lp(550)]
    assert classify(legs) == (StrategyType.CUSTOM, RiskProfile.UNDEFINED)


def test_protective_put_with_stock_is_custom_but_defined():
    strategy_type, risk = classify([shares(100), lp(560)])
    assert strategy_type is StrategyType.CUSTOM
    assert risk is RiskProfile.DEFINED  # no short options at all
