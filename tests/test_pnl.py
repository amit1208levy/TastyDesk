"""Tests for the P&L engine — every number the user actually trusts.

Every expected value in this file is hand-computed from the structure, never
copied from a run of the code. The arithmetic is spelled out in a comment above
each assertion so a future reader can re-derive it without a calculator.

The headline test is :func:`test_canonical_put_credit_spread`, which locks in the
user's own verified baseline and then asserts, in executable form, that the
short leg's private 300% move appears nowhere in the strategy's P&L. That is the
cardinal rule: risk belongs to a Strategy, never to a Leg.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)
from tastydesk.core.occ import build_occ_symbol
from tastydesk.core.pnl import (
    breakevens,
    called_away,
    cash_secured_max_loss,
    compute_pnl,
    cost_to_close,
    day_change,
    jade_lizard_upside_covered,
    max_loss,
    max_profit,
    payoff_at,
    premium_at_risk,
)

NEAR = date(2026, 10, 16)
FAR = date(2026, 11, 20)
OPENED = datetime(2026, 9, 1, 14, 30)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def opt(
    option_type: str,
    strike: str,
    direction: str,
    *,
    open_price: str = "0",
    mark: str | None = None,
    quantity: str = "1",
    expiration: date = NEAR,
    underlying: str = "SPY",
) -> Leg:
    """One option leg. ``direction`` is "S" or "L"; prices are positive quotes."""
    kind = OptionType.CALL if option_type.upper().startswith("C") else OptionType.PUT
    strike_dec = Decimal(strike)
    return Leg(
        symbol=build_occ_symbol(underlying, expiration, kind.value, strike_dec),
        instrument_type="Equity Option",
        underlying=underlying,
        direction=Direction.SHORT if direction.upper().startswith("S") else Direction.LONG,
        quantity=Decimal(quantity),
        option_type=kind,
        strike=strike_dec,
        expiration=expiration,
        open_price=Decimal(open_price),
        mark=Decimal(mark) if mark is not None else None,
    )


def shares(
    quantity: str = "100",
    *,
    direction: str = "L",
    open_price: str = "100.00",
    mark: str | None = None,
    underlying: str = "SPY",
) -> Leg:
    """A share lot: multiplier 1, no strike, no expiration."""
    return Leg(
        symbol=underlying,
        instrument_type="Equity",
        underlying=underlying,
        direction=Direction.SHORT if direction.upper().startswith("S") else Direction.LONG,
        quantity=Decimal(quantity),
        multiplier=Decimal(1),
        open_price=Decimal(open_price),
        mark=Decimal(mark) if mark is not None else None,
    )


def strat(
    strategy_type: StrategyType,
    legs: list[Leg],
    *,
    net_credit: str,
    closing_cash_flow: str = "0",
    risk_profile: RiskProfile = RiskProfile.DEFINED,
    underlying: str = "SPY",
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
        closing_cash_flow=Decimal(closing_cash_flow),
    )


def canonical_spread(*, short_mark: str = "8.00", long_mark: str | None = "5.50") -> Strategy:
    """The user's verified baseline: 5-wide SPY put credit spread, 1 contract.

    Short 580P sold for 2.00, long 575P bought for 1.00, net credit +$100.
    """
    return strat(
        StrategyType.PUT_CREDIT_SPREAD,
        [
            opt("P", "580", "S", open_price="2.00", mark=short_mark),
            opt("P", "575", "L", open_price="1.00", mark=long_mark),
        ],
        net_credit="100",
    )


# --------------------------------------------------------------------------- #
# The canonical case and the cardinal rule
# --------------------------------------------------------------------------- #


def test_canonical_put_credit_spread() -> None:
    """The user's verified baseline, verbatim.

    Short 580P now marks 8.00, long 575P marks 5.50, one contract each.
      close the short: 8.00 x -100 = -800
      close the long:  5.50 x +100 = +550
      cost_to_close                 = -250
      open_pnl        = +100 + (-250)      = -150
      pct_of_credit   = -150 / 100         = -1.50
      max_loss        = 5-wide 500 - 100   =  400
      pct_of_max_loss = 150 / 400          =  0.375
    """
    spread = canonical_spread()
    result = compute_pnl(spread)

    assert result.cost_to_close == Decimal("-250")
    assert result.open_pnl == Decimal("-150")
    assert result.pct_of_credit == Decimal("-1.5")
    assert result.max_loss == Decimal("400")
    assert result.pct_of_max_loss == Decimal("0.375")

    # Max profit on a credit spread is the credit itself, and a loser reports
    # its progress against that ceiling as the negative number it is.
    assert result.max_profit == Decimal("100")
    assert result.pct_of_max_profit == Decimal("-1.5")
    assert result.is_credit is True
    assert result.fully_quoted is True


def test_canonical_spread_never_reports_the_short_legs_own_move() -> None:
    """THE CARDINAL RULE, in executable form.

    The short 580P was sold at 2.00 and now marks 8.00, so on its own it is down
    (8 - 2) / 2 = 300%. That number is meaningless: the long 575P gained at the
    very same instant, and the spread is down 150%, not 300%. So 3.0 must not be
    the value of any field of the StrategyPnL — not as a percentage, not as a
    dollar figure, not with the sign flipped.
    """
    spread = canonical_spread()
    result = compute_pnl(spread)

    short_leg_move = (Decimal("8.00") - Decimal("2.00")) / Decimal("2.00")
    assert short_leg_move == Decimal("3")

    for f in dataclass_fields(result):
        value = getattr(result, f.name)
        if isinstance(value, Decimal):
            assert abs(value) != short_leg_move, (
                f"StrategyPnL.{f.name} == {value}: the short leg's own 300% move "
                "has leaked into a strategy-level number"
            )

    # The honest strategy-level number is half the leg's, and that is the point.
    assert result.pct_of_credit == Decimal("-1.5")


# --------------------------------------------------------------------------- #
# Undefined risk
# --------------------------------------------------------------------------- #


def test_short_strangle_has_no_max_loss_and_caps_profit_at_the_credit() -> None:
    """Short 550P / 600C for 3.00 total: nothing bounds the upside loss.

    Above 600 the short call loses $100 per point forever, so max_loss must be
    None rather than a comforting fiction. Max profit is the credit, +300.
    """
    strangle = strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "550", "S", open_price="1.50", mark="1.20"),
            opt("C", "600", "S", open_price="1.50", mark="1.30"),
        ],
        net_credit="300",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert max_loss(strangle) is None
    assert max_profit(strangle) == Decimal("300")

    result = compute_pnl(strangle)
    # close both shorts: (1.20 + 1.30) x -100 = -250 ; 300 - 250 = +50
    assert result.cost_to_close == Decimal("-250")
    assert result.open_pnl == Decimal("50")
    assert result.max_loss is None
    # A winner has no "% of max loss" to report even if max_loss existed.
    assert result.pct_of_max_loss is None


def test_naked_put_refuses_max_loss_but_answers_the_cash_secured_question() -> None:
    """Short 100P for 2.00.

    max_loss is None: the only thing stopping the loss is the stock reaching
    zero, and calling that "defined risk" is exactly the fiction this project
    refuses. cash_secured_max_loss is the explicit opt-in:
      100 strike x 100 = 10,000 less the 200 credit = 9,800.
    """
    naked_put = strat(
        StrategyType.NAKED_PUT,
        [opt("P", "100", "S", open_price="2.00", mark="1.50")],
        net_credit="200",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert max_loss(naked_put) is None
    assert cash_secured_max_loss(naked_put) == Decimal("9800")
    assert max_profit(naked_put) == Decimal("200")


def test_naked_call_has_no_finite_worst_case_at_all() -> None:
    """Short 100C for 2.00: the upside is unbounded, so even the cash-secured
    question has no answer."""
    naked_call = strat(
        StrategyType.NAKED_CALL,
        [opt("C", "100", "S", open_price="2.00", mark="2.50")],
        net_credit="200",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert max_loss(naked_call) is None
    assert cash_secured_max_loss(naked_call) is None


def test_cash_secured_max_loss_matches_max_loss_when_risk_is_defined() -> None:
    """For a genuinely defined structure the two agree — 400 on the baseline."""
    spread = canonical_spread()
    assert max_loss(spread) == Decimal("400")
    assert cash_secured_max_loss(spread) == Decimal("400")


def test_covered_call_max_profit_and_stock_to_zero_worst_case() -> None:
    """100 shares bought at 100.00, short 105C sold for 2.00.

    net_credit = -10,000 (shares) + 200 (call) = -9,800.
      called away at 105: -9,800 + 105 x 100 = +700  (5 points + the 2.00 credit)
      stock to zero:      -9,800 + 0          = -9,800
    max_loss is still None — a covered call's floor is only the stock's zero —
    but cash_secured_max_loss names the 9,800 explicitly.
    """
    covered = strat(
        StrategyType.COVERED_CALL,
        [
            shares("100", open_price="100.00", mark="103.00"),
            opt("C", "105", "S", open_price="2.00", mark="1.00"),
        ],
        net_credit="-9800",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert max_profit(covered) == Decimal("700")
    assert max_loss(covered) is None
    assert cash_secured_max_loss(covered) == Decimal("9800")


# --------------------------------------------------------------------------- #
# Multi-leg defined structures
# --------------------------------------------------------------------------- #


def test_iron_condor_with_unequal_wings_uses_the_wider_side_only() -> None:
    """Put side 10 wide, call side 5 wide, credit +200.

    Both sides cannot lose at once, so the risk is the WIDER wing alone:
      1,000 - 200 = 800.
    The sum of the two sides (800 + 300 = 1,100) would be wrong, and so would
    the narrower side (500 - 200 = 300).
    """
    condor = strat(
        StrategyType.IRON_CONDOR,
        [
            opt("P", "540", "L", open_price="0.60", mark="0.40"),
            opt("P", "550", "S", open_price="1.60", mark="1.10"),
            opt("C", "600", "S", open_price="1.40", mark="1.00"),
            opt("C", "605", "L", open_price="0.40", mark="0.30"),
        ],
        net_credit="200",
    )

    assert max_loss(condor) == Decimal("800")
    assert max_loss(condor) != Decimal("1100")
    assert max_loss(condor) != Decimal("300")
    assert max_profit(condor) == Decimal("200")

    # Bottom of the put wing: 200 - (550 - 540) x 100 = -800. Top of the call
    # wing: 200 - (605 - 600) x 100 = -300, which never becomes the headline.
    assert payoff_at(condor, Decimal("540")) == Decimal("-800")
    assert payoff_at(condor, Decimal("605")) == Decimal("-300")


def test_iron_condor_wider_call_side_is_found_too() -> None:
    """Mirror image: put side 5 wide, call side 10 wide, credit +200.

    The wider side is now the calls, so max_loss = 1,000 - 200 = 800 again. This
    proves the engine measures both rays rather than always reading the puts.
    """
    condor = strat(
        StrategyType.IRON_CONDOR,
        [
            opt("P", "545", "L", open_price="0.50", mark="0.35"),
            opt("P", "550", "S", open_price="1.50", mark="1.00"),
            opt("C", "600", "S", open_price="1.50", mark="1.10"),
            opt("C", "610", "L", open_price="0.50", mark="0.45"),
        ],
        net_credit="200",
    )

    assert max_loss(condor) == Decimal("800")
    assert payoff_at(condor, Decimal("545")) == Decimal("-300")
    assert payoff_at(condor, Decimal("610")) == Decimal("-800")


def test_iron_fly() -> None:
    """Short 600 straddle, 10-wide wings (590P / 610C), credit +700.

    max_profit = the credit, 700, achieved only at 600 exactly.
    max_loss   = 1,000 - 700 = 300 at either wing.
    breakevens = 600 -/+ 7.00 = 593 and 607.
    """
    fly = strat(
        StrategyType.IRON_FLY,
        [
            opt("P", "590", "L", open_price="1.00", mark="0.80"),
            opt("P", "600", "S", open_price="4.50", mark="4.00"),
            opt("C", "600", "S", open_price="4.50", mark="3.80"),
            opt("C", "610", "L", open_price="1.00", mark="0.90"),
        ],
        net_credit="700",
    )

    assert max_profit(fly) == Decimal("700")
    assert max_loss(fly) == Decimal("300")
    assert payoff_at(fly, Decimal("600")) == Decimal("700")
    assert payoff_at(fly, Decimal("590")) == Decimal("-300")
    assert payoff_at(fly, Decimal("610")) == Decimal("-300")
    assert breakevens(fly) == [Decimal("593"), Decimal("607")]

    result = compute_pnl(fly)
    # close: long 590P +0.80x100=+80, short 600P -400, short 600C -380,
    # long 610C +0.90x100=+90  ->  80 - 400 - 380 + 90 = -610
    assert result.cost_to_close == Decimal("-610")
    assert result.open_pnl == Decimal("90")
    # 90 / 700 is a repeating decimal, so compare the product, not a rounding.
    assert result.pct_of_max_profit is not None
    assert result.pct_of_max_profit * Decimal("700") == pytest.approx(Decimal("90"))


def test_call_debit_spread_computes_max_profit_from_the_legs() -> None:
    """Long 100C for 3.00, short 105C for 1.00: a $200 debit, not a credit.

    net_credit = -300 + 100 = -200, so the CREDIT_STRATEGIES shortcut does not
    apply and the payoff must be read off the legs:
      max_profit = 5-wide 500 - 200 debit = 300
      max_loss   = the debit paid          = 200
      breakeven  = 100 + 2.00              = 102
    """
    debit = strat(
        StrategyType.CALL_DEBIT_SPREAD,
        [
            opt("C", "100", "L", open_price="3.00", mark="4.00"),
            opt("C", "105", "S", open_price="1.00", mark="1.50"),
        ],
        net_credit="-200",
    )

    assert max_profit(debit) == Decimal("300")
    assert max_loss(debit) == Decimal("200")
    assert breakevens(debit) == [Decimal("102")]

    result = compute_pnl(debit)
    assert result.is_credit is False
    # close: long +4.00x100 = +400, short 1.50x-100 = -150 -> +250
    assert result.cost_to_close == Decimal("250")
    assert result.open_pnl == Decimal("50")
    # A debit trade still has a credit scale to measure against: 50 / |-200|.
    assert result.pct_of_credit == Decimal("0.25")
    assert result.pct_of_max_profit is not None
    assert result.pct_of_max_profit * Decimal("300") == pytest.approx(Decimal("50"))


# --------------------------------------------------------------------------- #
# The management target
# --------------------------------------------------------------------------- #


def test_winner_at_fifty_percent_of_credit() -> None:
    """The tastytrade management target: buy the strangle back for half.

    Sold 550P and 600C for 1.50 each (credit +300); both now mark 0.75.
      cost_to_close = (0.75 + 0.75) x -100 = -150
      open_pnl      = 300 - 150            = +150
      pct_of_credit = 150 / 300            = 0.50
    For a pure credit structure max_profit IS the credit, so "% of max profit"
    must come out identical to "% of credit".
    """
    strangle = strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "550", "S", open_price="1.50", mark="0.75"),
            opt("C", "600", "S", open_price="1.50", mark="0.75"),
        ],
        net_credit="300",
        risk_profile=RiskProfile.UNDEFINED,
    )
    result = compute_pnl(strangle)

    assert result.cost_to_close == Decimal("-150")
    assert result.open_pnl == Decimal("150")
    assert result.pct_of_credit == Decimal("0.5")
    assert result.pct_of_max_profit == Decimal("0.5")
    assert result.pct_of_credit == result.pct_of_max_profit
    assert result.pct_of_max_loss is None


# --------------------------------------------------------------------------- #
# Missing data and degenerate inputs
# --------------------------------------------------------------------------- #


def test_one_unquoted_leg_makes_cost_to_close_none_not_zero() -> None:
    """The long 575P has no mark yet.

    Summing only the quoted short leg would report -800 and claim the spread is
    down 900% of its credit. Treating the missing mark as zero would be just as
    wrong. The only honest answer is None, plus "1 of 2 legs quoted".
    """
    spread = canonical_spread(long_mark=None)

    assert cost_to_close(spread) is None
    result = compute_pnl(spread)

    assert result.cost_to_close is None
    assert result.cost_to_close != Decimal("0")
    assert result.cost_to_close != Decimal("-800")
    assert result.open_pnl is None
    assert result.pct_of_credit is None
    assert result.pct_of_max_profit is None
    assert result.pct_of_max_loss is None

    assert result.quoted_legs == 1
    assert result.total_legs == 2
    assert result.quoted_legs < result.total_legs
    assert result.fully_quoted is False

    # Structural figures need no marks at all, so they survive intact.
    assert result.max_loss == Decimal("400")
    assert result.max_profit == Decimal("100")


def test_zero_net_credit_neither_raises_nor_produces_infinity() -> None:
    """A spread opened for scratch: credit taken in is exactly 0.

    There is no scale to express "% of credit" against, so it must be None —
    not a ZeroDivisionError, not Infinity, not NaN. Everything anchored to the
    structure instead of the credit still works:
      max_loss        = 5-wide 500 - 0 credit = 500
      pct_of_max_loss = 50 / 500              = 0.10
    """
    scratch = strat(
        StrategyType.PUT_CREDIT_SPREAD,
        [
            opt("P", "580", "S", open_price="2.00", mark="1.00"),
            opt("P", "575", "L", open_price="2.00", mark="0.50"),
        ],
        net_credit="0",
    )
    result = compute_pnl(scratch)

    assert result.pct_of_credit is None
    assert result.cost_to_close == Decimal("-50")
    assert result.open_pnl == Decimal("-50")
    assert result.max_profit == Decimal("0")
    assert result.max_loss == Decimal("500")
    assert result.pct_of_max_loss == Decimal("0.1")
    assert result.pct_of_max_profit is None
    assert result.is_credit is False

    for f in dataclass_fields(result):
        value = getattr(result, f.name)
        if isinstance(value, Decimal):
            assert value.is_finite(), f"StrategyPnL.{f.name} is {value}"


def test_multi_expiration_refuses_to_guess() -> None:
    """A calendar: short the near 600C, own the far 600C for a 1.50 debit.

    On the near expiry the trade's value depends on the far leg's remaining
    extrinsic value, which no strike-based arithmetic can know. So max profit
    and max loss are None and there are no breakevens to draw — a single number
    here would be confidently wrong in whichever direction vol happened to move.
    Live marks still work, because those are quoted, not modelled.
    """
    calendar = strat(
        StrategyType.CALENDAR,
        [
            opt("C", "600", "S", open_price="3.00", mark="3.00", expiration=NEAR),
            opt("C", "600", "L", open_price="4.50", mark="5.00", expiration=FAR),
        ],
        net_credit="-150",
    )

    assert max_profit(calendar) is None
    assert max_loss(calendar) is None
    assert cash_secured_max_loss(calendar) is None
    assert breakevens(calendar) == []

    result = compute_pnl(calendar)
    # close: short -3.00x100 = -300, long +5.00x100 = +500 -> +200
    assert result.cost_to_close == Decimal("200")
    assert result.open_pnl == Decimal("50")
    assert result.max_profit is None
    assert result.max_loss is None
    assert result.pct_of_max_profit is None
    assert result.pct_of_max_loss is None


def test_diagonal_refuses_to_guess_too() -> None:
    """Same refusal for a diagonal — different strikes as well as expiries."""
    diagonal = strat(
        StrategyType.DIAGONAL,
        [
            opt("C", "600", "S", open_price="3.00", mark="2.80", expiration=NEAR),
            opt("C", "610", "L", open_price="4.00", mark="4.20", expiration=FAR),
        ],
        net_credit="-100",
    )

    assert max_profit(diagonal) is None
    assert max_loss(diagonal) is None
    assert breakevens(diagonal) == []
    assert cost_to_close(diagonal) == Decimal("140")  # -2.80x100 + 4.20x100


# --------------------------------------------------------------------------- #
# Payoff and breakevens
# --------------------------------------------------------------------------- #


def test_payoff_at_across_the_whole_vertical() -> None:
    """The baseline spread's expiration payoff, walked end to end.

    Credit +100, short 580P, long 575P, one contract.
      570 (below the long strike):  100 - 10.00x100 + 5.00x100 = -400  (max loss)
      575 (at the long strike):     100 - 5.00x100             = -400
      577.50 (between the strikes): 100 - 2.50x100             = -150
      579 (the breakeven):          100 - 1.00x100             =    0
      580 (at the short strike):    100                        = +100
      585 (above both):             100                        = +100  (max profit)
    """
    spread = canonical_spread()

    assert payoff_at(spread, Decimal("570")) == Decimal("-400")
    assert payoff_at(spread, Decimal("575")) == Decimal("-400")
    assert payoff_at(spread, Decimal("577.50")) == Decimal("-150")
    assert payoff_at(spread, Decimal("579")) == Decimal("0")
    assert payoff_at(spread, Decimal("580")) == Decimal("100")
    assert payoff_at(spread, Decimal("585")) == Decimal("100")

    # The floor and the ceiling of that walk are exactly what the headline
    # numbers report.
    assert max_loss(spread) == Decimal("400")
    assert max_profit(spread) == Decimal("100")

    # Below the long strike the payoff is flat: that flatness is the definition
    # of defined risk.
    assert payoff_at(spread, Decimal("0")) == payoff_at(spread, Decimal("570"))


def test_put_credit_spread_has_exactly_one_breakeven() -> None:
    """Short strike 580 less the 1.00 credit per share = 579, and nothing else."""
    assert breakevens(canonical_spread()) == [Decimal("579")]


def test_short_strangle_has_two_breakevens() -> None:
    """Sold 550P / 600C for 3.00 total, one contract.

    Downside: 550 - 3.00 = 547. Upside: 600 + 3.00 = 603.
    """
    strangle = strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "550", "S", open_price="1.50", mark="1.50"),
            opt("C", "600", "S", open_price="1.50", mark="1.50"),
        ],
        net_credit="300",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert breakevens(strangle) == [Decimal("547"), Decimal("603")]
    # Between the breakevens the trade wins; outside them it loses.
    assert payoff_at(strangle, Decimal("575")) == Decimal("300")
    assert payoff_at(strangle, Decimal("547")) == Decimal("0")
    assert payoff_at(strangle, Decimal("603")) == Decimal("0")
    assert payoff_at(strangle, Decimal("610")) == Decimal("-700")


# --------------------------------------------------------------------------- #
# Jade lizard
# --------------------------------------------------------------------------- #


def jade_lizard(credit: str) -> Strategy:
    """Short 95P, short 105C, long 110C — a 5-wide ($500) call spread."""
    return strat(
        StrategyType.JADE_LIZARD,
        [
            opt("P", "95", "S", open_price="3.00", mark="2.50"),
            opt("C", "105", "S", open_price="3.50", mark="3.00"),
            opt("C", "110", "L", open_price="0.50", mark="0.40"),
        ],
        net_credit=credit,
        risk_profile=RiskProfile.UNDEFINED,
    )


def test_jade_lizard_upside_is_covered_when_credit_exceeds_the_call_wing() -> None:
    """Credit +600 against a 5-wide ($500) call spread: no upside risk at all.

    Above 110 the payoff flattens at 600 - 500 = +100, so however far the stock
    runs the trade cannot lose on that side.
    """
    lizard = jade_lizard("600")

    assert jade_lizard_upside_covered(lizard) is True
    assert payoff_at(lizard, Decimal("110")) == Decimal("100")
    assert payoff_at(lizard, Decimal("200")) == Decimal("100")

    # Covered upside is still not defined risk: the naked 95 put underneath
    # leaves the downside open, so max_loss stays None.
    assert max_loss(lizard) is None
    assert max_profit(lizard) == Decimal("600")
    # 95 x 100 - 600 credit = 8,900 if the stock goes to zero.
    assert cash_secured_max_loss(lizard) == Decimal("8900")


def test_jade_lizard_upside_not_covered_when_the_credit_falls_short() -> None:
    """Credit +400 against the same 5-wide wing: above 110 it settles at -100."""
    lizard = jade_lizard("400")

    assert jade_lizard_upside_covered(lizard) is False
    assert payoff_at(lizard, Decimal("110")) == Decimal("-100")
    assert max_loss(lizard) is None


def test_jade_lizard_question_is_meaningless_for_anything_else() -> None:
    """Asked of a put credit spread or a strangle, the answer is None, not False."""
    assert jade_lizard_upside_covered(canonical_spread()) is None

    strangle = strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "550", "S", open_price="1.50", mark="1.50"),
            opt("C", "600", "S", open_price="1.50", mark="1.50"),
        ],
        net_credit="300",
        risk_profile=RiskProfile.UNDEFINED,
    )
    assert jade_lizard_upside_covered(strangle) is None


# --------------------------------------------------------------------------- #
# Rolls and partial closes
# --------------------------------------------------------------------------- #


def test_max_profit_accounts_for_cash_already_banked() -> None:
    """A 2-lot strangle opened for +600, one lot bought back for -100.

    Only one lot is still live. The very best that can now happen is that it
    expires worthless, leaving 600 - 100 = +500 in the account. Reporting the
    original 600 as max profit would quote a figure this trade can no longer
    reach, and every "% of max profit" derived from it would read low.
      cost_to_close = (0.75 + 0.75) x -100 = -150
      open_pnl      = 600 - 100 - 150      = +350
      pct_of_max_profit = 350 / 500        = 0.70
    """
    rolled = strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "550", "S", open_price="1.50", mark="0.75"),
            opt("C", "600", "S", open_price="1.50", mark="0.75"),
        ],
        net_credit="600",
        closing_cash_flow="-100",
        risk_profile=RiskProfile.UNDEFINED,
    )

    # The payoff engine already knows this: between the strikes at expiration
    # the trade is worth 500, not 600.
    assert payoff_at(rolled, Decimal("575")) == Decimal("500")
    assert max_profit(rolled) == Decimal("500")
    assert max_profit(rolled) == payoff_at(rolled, Decimal("575"))

    result = compute_pnl(rolled)
    assert result.realized_pnl == Decimal("500")
    assert result.cost_to_close == Decimal("-150")
    assert result.open_pnl == Decimal("350")
    assert result.pct_of_max_profit == Decimal("0.7")


def test_defined_spread_max_loss_also_accounts_for_banked_cash() -> None:
    """The same consistency on the loss side, which already held.

    Baseline spread, but 50 of profit was already banked closing a first lot:
    the worst case improves from 400 to 350.
    """
    spread = strat(
        StrategyType.PUT_CREDIT_SPREAD,
        [
            opt("P", "580", "S", open_price="2.00", mark="8.00"),
            opt("P", "575", "L", open_price="1.00", mark="5.50"),
        ],
        net_credit="100",
        closing_cash_flow="50",
    )

    assert max_loss(spread) == Decimal("350")
    assert max_profit(spread) == Decimal("150")
    assert payoff_at(spread, Decimal("570")) == Decimal("-350")


# --------------------------------------------------------------------------- #
# The denominator of pct_of_credit: what is actually still at risk
# --------------------------------------------------------------------------- #


def rolled_strangle(*, mark: str = "4.50") -> Strategy:
    """The reproduced roll: SPY strangle opened +300, then rolled once.

    Old legs bought back for -500, new 570P/610C sold for +400, so
    ``net_credit`` is the gross +700 of both openings and ``closing_cash_flow``
    is the -500 that left. Only 700 - 500 = 200 is still on the table.
    """
    return strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "570", "S", open_price="2.00", mark=mark),
            opt("C", "610", "S", open_price="2.00", mark=mark),
        ],
        net_credit="700",
        closing_cash_flow="-500",
        risk_profile=RiskProfile.UNDEFINED,
    )


def test_a_rolled_trade_is_measured_against_everything_it_collected() -> None:
    """The scale is gross credit collected, and it never vanishes.

    Rolled strangle: opened +300, old legs bought back -500, new legs sold
    +400, so net_credit 700 and closing_cash_flow -500. Both new legs mark 4.50:
      cost_to_close = (4.50 + 4.50) x -100        = -900
      open_pnl      = 700 - 500 - 900             = -700
      premium       = 700   (everything taken in across the chain)
      pct_of_credit = -700 / 700                  = -1.00

    "Down 100% of everything I have collected on this trade" is the honest
    reading, and it is how a premium seller talks about a rolled chain.
    """
    rolled = compute_pnl(rolled_strangle())

    assert rolled.cost_to_close == Decimal("-900")
    assert rolled.open_pnl == Decimal("-700")
    assert rolled.pct_of_credit == Decimal("-1")


def test_a_roll_for_scratch_keeps_its_loss_scale() -> None:
    """The regression that matters most: rolling a loser for about even.

    Netting the buyback out of the denominator looks more principled and is a
    trap. A chain that collected 1400 and paid 1400 back would have a
    denominator of zero, so a trade down $2,400 gets no loss percentage at all
    and scores OK with nothing to say. Rolling a loser for roughly even is
    ordinary practice, so the unstable scale fails exactly where it is needed.
    """
    rolled_for_scratch = strat(
        StrategyType.SHORT_STRANGLE,
        [
            opt("P", "450", "S", open_price="2.00", mark="12.00"),
            opt("C", "730", "S", open_price="2.00", mark="12.00"),
        ],
        net_credit="1400",
        closing_cash_flow="-1400",
        risk_profile=RiskProfile.UNDEFINED,
    )
    scratch = compute_pnl(rolled_for_scratch)

    assert premium_at_risk(rolled_for_scratch) == Decimal("1400")
    assert scratch.open_pnl == Decimal("-2400")
    assert scratch.pct_of_credit is not None
    assert scratch.pct_of_credit < Decimal("-1.7")


def test_two_dollars_either_side_of_scratch_reads_the_same() -> None:
    """No discontinuity across a value rolls land on routinely.

    Netting the buyback out put a six-figure percentage on one side of scratch
    and None on the other, two dollars apart.
    """

    def roll(closing: str) -> Decimal | None:
        return compute_pnl(
            strat(
                StrategyType.SHORT_STRANGLE,
                [
                    opt("P", "450", "S", open_price="2.00", mark="12.00"),
                    opt("C", "730", "S", open_price="2.00", mark="12.00"),
                ],
                net_credit="1400",
                closing_cash_flow=closing,
                risk_profile=RiskProfile.UNDEFINED,
            )
        ).pct_of_credit

    for_a_debit = roll("-1402")
    for_a_credit = roll("-1398")

    assert for_a_debit is not None and for_a_credit is not None
    assert abs(for_a_debit - for_a_credit) < Decimal("0.01")


def test_progress_toward_profit_uses_what_is_still_achievable() -> None:
    """The 50%-profit rule is named after max profit, so it reads max profit.

    Rolled strangle with both new legs worthless:
      open_pnl   = 700 - 500 + 0 = +200
      max_profit = 700 - 500     = +200   (cash paid out to roll cannot return)
    The trade is at 100% of everything still available to it, which is what the
    "take it off" nag must see — while pct_of_credit correctly reads only
    0.2857, because it has banked 200 of the 700 it ever collected.
    """
    maxed = compute_pnl(rolled_strangle(mark="0"))

    assert maxed.open_pnl == Decimal("200")
    assert maxed.max_profit == Decimal("200")
    assert maxed.pct_of_max_profit == Decimal("1")
    # The two scales answer different questions and are allowed to differ.
    assert maxed.pct_of_credit == Decimal("200") / Decimal("700")


def test_a_partial_close_keeps_the_full_credit_scale() -> None:
    """2-lot strangle opened +600, one lot bought back -100, both legs mark 0.75.

    cost_to_close = (0.75 + 0.75) x -100 = -150
    open_pnl      = 600 - 100 - 150      = +350
    pct_of_credit = 350 / 600            = 0.5833
    max_profit    = 600 - 100            = +500, so 350/500 = 0.70 captured
    """
    partial = compute_pnl(
        strat(
            StrategyType.SHORT_STRANGLE,
            [
                opt("P", "550", "S", open_price="1.50", mark="0.75"),
                opt("C", "600", "S", open_price="1.50", mark="0.75"),
            ],
            net_credit="600",
            closing_cash_flow="-100",
            risk_profile=RiskProfile.UNDEFINED,
        )
    )

    assert partial.open_pnl == Decimal("350")
    assert partial.max_profit == Decimal("500")
    assert partial.pct_of_max_profit == Decimal("0.7")
    assert partial.pct_of_credit == Decimal("350") / Decimal("600")


# --------------------------------------------------------------------------- #
# Covered calls: premium is the scale, shares are a cost basis
# --------------------------------------------------------------------------- #


def covered_call(*, share_mark: str, call_mark: str) -> Strategy:
    """Buy 100 XYZ at 100.00, sell the 105 call for 2.00.

    net_credit = -10,000 (shares) + 200 (premium) = -9,800. The stock debit
    swamps the premium, which is exactly the trap.
    """
    return strat(
        StrategyType.COVERED_CALL,
        [
            shares("100", open_price="100.00", mark=share_mark, underlying="XYZ"),
            opt("C", "105", "S", open_price="2.00", mark=call_mark, underlying="XYZ"),
        ],
        net_credit="-9800",
        underlying="XYZ",
    )


def test_covered_call_percentage_is_premium_only_not_the_share_cost_basis() -> None:
    """Stock at 80, call marks 0.05 — a loss of nine times the premium taken in.

      cost_to_close = 80 x 100 (shares) + 0.05 x -100 (call) = +7,995
      open_pnl      = -9,800 + 7,995                          = -1,805
      premium at risk = -9,800 - (-10,000)                    = +200
      pct_of_credit = -1,805 / 200                            = -9.025
    Divided by the 9,800 cost basis it read -0.1842, nowhere near the -1.0
    watch line. max_loss is correctly None for a covered call, so
    pct_of_max_loss cannot cover for it either: without this the position has
    no working loss signal at all.
    """
    losing = compute_pnl(covered_call(share_mark="80", call_mark="0.05"))

    assert losing.open_pnl == Decimal("-1805")
    assert losing.max_loss is None
    assert losing.pct_of_max_loss is None
    assert losing.pct_of_credit == Decimal("-9.025")
    # The old number, named so a regression cannot slip through quietly.
    assert losing.pct_of_credit != Decimal("-0.1842")
    assert losing.pct_of_credit < Decimal("-2.0")  # past the user's 2x stop


def test_covered_call_at_max_profit_can_reach_the_profit_target() -> None:
    """Stock at 110, call marks 5.10: 690 of the 700 best case.

      cost_to_close = 110 x 100 + 5.10 x -100 = +10,490
      open_pnl      = -9,800 + 10,490          = +690
      pct_of_credit = 690 / 200                = 3.45
    Against the share cost basis this was 0.0704, so the 50% "take it off"
    signal was as unreachable as the loss signal.
    """
    maxed = covered_call(share_mark="110", call_mark="5.10")
    result = compute_pnl(maxed)

    assert max_profit(maxed) == Decimal("700")
    assert result.open_pnl == Decimal("690")
    assert result.pct_of_credit == Decimal("3.45")
    assert result.pct_of_credit >= Decimal("0.5")
    assert premium_at_risk(maxed) == Decimal("200")


def test_share_only_position_has_no_premium_scale() -> None:
    """No options, no premium, so no "% of credit" — None, never a share ratio."""
    stock = strat(
        StrategyType.EQUITY,
        [shares("100", open_price="100.00", mark="90", underlying="XYZ")],
        net_credit="-10000",
        underlying="XYZ",
        risk_profile=RiskProfile.UNDEFINED,
    )
    result = compute_pnl(stock)

    assert premium_at_risk(stock) == Decimal("0")
    assert result.pct_of_credit is None
    assert result.open_pnl == Decimal("-1000")


def test_removing_the_shares_leaves_the_debit_spread_scale_untouched() -> None:
    """The stock subtraction must not touch an all-option trade.

    A call debit spread has no share legs, so premium_at_risk is still the
    whole net_credit and the -200 debit remains the scale it is measured on.
    """
    debit = strat(
        StrategyType.CALL_DEBIT_SPREAD,
        [
            opt("C", "100", "L", open_price="3.00", mark="4.00"),
            opt("C", "105", "S", open_price="1.00", mark="1.50"),
        ],
        net_credit="-200",
    )

    assert premium_at_risk(debit) == Decimal("-200")
    assert compute_pnl(debit).pct_of_credit == Decimal("0.25")


# --------------------------------------------------------------------------- #
# The day's move
# --------------------------------------------------------------------------- #


def test_a_short_leg_that_got_more_expensive_lost_money_today() -> None:
    """Sign convention: a short option rising in price is a loss.

    The day is measured from the broker's own close price, so a credit spread
    whose short leg went up and long leg went up less is down by the
    difference -- computed on the structure, never on the short leg alone.
    """
    short = opt("P", "500", "S", open_price="5.00", mark="6.00")
    long_ = opt("P", "495", "L", open_price="3.00", mark="3.40")
    short.prior_close = Decimal("5.50")
    long_.prior_close = Decimal("3.20")
    spread = strat(StrategyType.PUT_CREDIT_SPREAD, [short, long_], net_credit="200")

    # short: (6.00 - 5.50) x -100 = -50 ; long: (3.40 - 3.20) x 100 = +20
    assert day_change(spread) == Decimal("-30")


def test_a_leg_with_no_close_price_makes_the_day_unknown_not_smaller() -> None:
    """Dropping the leg would report a move the position did not make."""
    short = opt("P", "500", "S", open_price="5.00", mark="6.00")
    long_ = opt("P", "495", "L", open_price="3.00", mark="3.40")
    short.prior_close = Decimal("5.50")
    # long_ has no close price at all
    spread = strat(StrategyType.PUT_CREDIT_SPREAD, [short, long_], net_credit="200")

    assert day_change(spread) is None


def test_an_unquoted_leg_makes_the_day_unknown() -> None:
    short = opt("P", "500", "S", open_price="5.00", mark=None)
    short.prior_close = Decimal("5.50")
    naked = strat(
        StrategyType.NAKED_PUT,
        [short],
        net_credit="500",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert day_change(naked) is None


# --------------------------------------------------------------------------- #
# Being called away
# --------------------------------------------------------------------------- #


def test_a_poor_mans_covered_call_knows_what_being_called_away_pays() -> None:
    """The question a PMCC holder actually asks as price climbs through the short.

    Max profit and max loss both refuse a diagonal, and rightly: the legs
    expire on different days. But assignment is not a theoretical maximum, it
    is an arithmetic outcome -- sell at the short strike, exercise the long at
    its own -- and the holder deserves the number rather than three dashes.
    """
    short = opt("C", "63", "S", open_price="1.03", quantity="2", expiration=NEAR)
    long_ = opt("C", "52.5", "L", open_price="12.01", quantity="2", expiration=FAR)
    pmcc = strat(
        StrategyType.CUSTOM,
        [short, long_],
        net_credit="-1694",
        risk_profile=RiskProfile.UNDEFINED,
    )

    # (63 - 52.5) x 100 x 2 = 2,100 delivered, less the 1,694 net debit paid.
    assert called_away(pmcc) == Decimal("406")


def test_a_naked_short_has_no_called_away_figure() -> None:
    """Nothing covers it, so there is no settlement to compute."""
    naked = strat(
        StrategyType.NAKED_CALL,
        [opt("C", "63", "S", open_price="1.03")],
        net_credit="103",
        risk_profile=RiskProfile.UNDEFINED,
    )

    assert called_away(naked) is None


# --------------------------------------------------------------------------- #
# Outright futures
# --------------------------------------------------------------------------- #


def _future(direction: str, open_price: str, mark: str, quantity: str = "1") -> Leg:
    """One outright futures contract: no strike, no expiry, $1,000 a point."""
    return Leg(
        symbol="/ZBZ6",
        instrument_type="Future",
        underlying="/ZB",
        direction=Direction.SHORT if direction.upper().startswith("S") else Direction.LONG,
        quantity=Decimal(quantity),
        multiplier=Decimal(1000),
        open_price=Decimal(open_price),
        mark=Decimal(mark),
    )


def test_a_future_is_worth_its_move_not_its_notional() -> None:
    """Buying a future moves no cash, so closing one cannot return the notional.

    The broker books the fill at a value of zero and settles the difference
    every evening instead. Priced like an option -- where the premium really
    did leave the account -- two long /ZB contracts read as +$211,339 of
    profit on a position that was down nine hundred dollars.
    """
    trade = strat(
        StrategyType.FUTURE,
        [_future("L", "106.53125", "105.734375"), _future("L", "105.875", "105.734375")],
        net_credit="-4.36",  # the two commissions; the contracts themselves cost nothing
        risk_profile=RiskProfile.UNDEFINED,
    )

    # (105.734375 - 106.53125) x 1000 + (105.734375 - 105.875) x 1000 - 4.36
    assert compute_pnl(trade).open_pnl == Decimal("-941.86")


def test_a_futures_contract_is_not_shares() -> None:
    """One point of /ZB is a thousand dollars; one share of anything is one."""
    leg = _future("L", "106.53125", "105.734375")

    assert leg.is_future
    assert not leg.is_option


def test_a_future_produced_no_cash_when_it_was_bought() -> None:
    """The other end of the convention close_cash_flow already had.

    Buying a futures contract moves nothing: the broker books the fill at a
    value of zero and settles the difference every evening. Charging the
    account the notional at open made a long /ZB leg that was down $1,859 read
    as -$108,390, and made the premium at risk on the trade holding it
    $220,700 — which every "% of credit" on that position was divided by.
    """
    future = Leg(
        symbol="/ZBZ6",
        instrument_type="Future",
        underlying="/ZBZ6",
        direction=Direction.LONG,
        quantity=Decimal(1),
        multiplier=Decimal(1000),
        open_price=Decimal("106.53125"),
        mark=Decimal("104.671875"),
    )

    assert future.open_cash_flow == Decimal(0)
    # What it is worth, and what it has cost: the same number, once.
    assert future.close_cash_flow == Decimal("-1859.375")
    assert future.open_cash_flow + future.close_cash_flow == Decimal("-1859.375")


def test_a_futures_leg_is_not_a_cost_basis_to_strip_out() -> None:
    """Premium at risk removes what stock cost. A future cost nothing."""
    future = Leg(
        symbol="/ZBZ6",
        instrument_type="Future",
        underlying="/ZBZ6",
        direction=Direction.LONG,
        quantity=Decimal(1),
        multiplier=Decimal(1000),
        open_price=Decimal("106.53125"),
        mark=Decimal("104.671875"),
    )
    short_call = Leg(
        symbol="./ZBZ6 OZBZ6 261120C111",
        instrument_type="Future Option",
        underlying="/ZBZ6",
        direction=Direction.SHORT,
        quantity=Decimal(2),
        multiplier=Decimal(1000),
        option_type=OptionType.CALL,
        strike=Decimal(111),
        expiration=date(2026, 11, 20),
        open_price=Decimal("0.40625"),
        mark=Decimal("0.390625"),
    )
    covered = Strategy(
        id="bull-zb",
        account_number="A",
        underlying="/ZBZ6",
        strategy_type=StrategyType.CUSTOM,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[future, short_call],
        opened_at=datetime(2026, 9, 23, tzinfo=UTC),
        net_credit=Decimal("812.50"),
    )

    assert premium_at_risk(covered) == Decimal("812.50")


def test_an_open_rolled_trade_is_measured_on_its_open_legs() -> None:
    """The /RTY strangle: four rolls banked $2,114, the two legs open now were
    sold for $1,175 and cost $1,012.50 to close. Open, it is up $162.50 —
    14% — not 52% of a max profit that includes the rolls."""

    def leg(strike: str, right: OptionType, sold: str, now: str) -> Leg:
        return Leg(
            symbol=f"./RTYZ6 R3EX6 261120{right.value}{strike}",
            instrument_type="Future Option",
            underlying="/RTYZ6",
            direction=Direction.SHORT,
            quantity=Decimal(1),
            multiplier=Decimal(50),
            option_type=right,
            strike=Decimal(strike),
            open_price=Decimal(sold),
            mark=Decimal(now),
        )

    trade = Strategy(
        id="rty",
        account_number="A",
        underlying="/RTYZ6",
        strategy_type=StrategyType.SHORT_STRANGLE,
        risk_profile=RiskProfile.UNDEFINED,
        legs=[leg("2500", OptionType.PUT, "13.50", "14.05"), leg("3100", OptionType.CALL, "10.00", "6.20")],
        opened_at=datetime(2026, 8, 1, tzinfo=UTC),
        net_credit=Decimal("3197.34"),
        closing_cash_flow=Decimal("-1083.44"),
        roll_count=4,
    )

    whole = compute_pnl(trade)
    now = compute_pnl(trade.open_legs_only())

    assert whole.pct_of_max_profit is not None and whole.pct_of_max_profit > Decimal("0.5")
    assert now.open_pnl == Decimal("162.50")
    assert now.pct_of_max_profit is not None and Decimal("0.13") < now.pct_of_max_profit < Decimal("0.15")
    assert trade.open_legs_only().roll_count == 0
