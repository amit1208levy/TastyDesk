"""Naming the structure a set of legs forms, and saying whether its risk is capped.

The journal groups fills into trades; this module answers "what did I actually
put on?" for one such group. Everything downstream — the credit-strategy maths,
the risk engine, the "which setups work" analytics — keys off the answer, so the
rule here is **never guess**: a shape that is not exactly one of the known
structures comes back as :attr:`StrategyType.CUSTOM` rather than being rounded
to the nearest familiar name. A mislabelled iron condor would invent a max loss
that does not exist.

Two properties the callers rely on:

*Order-insensitive.* Legs arrive in whatever order the broker listed them. We
aggregate into positions keyed by (expiration, type, strike) and sort, so the
same trade always classifies the same way.

*Quantity-aware.* Two short puts against one long put is a ratio spread with a
naked put inside it, not a put credit spread. Counting legs without counting
contracts is how a trade with uncapped downside ends up displaying a tidy max
loss.

Risk profile
------------
:attr:`RiskProfile.DEFINED` means every short option is covered, inside its own
expiration, by a long option of the same type (or, for short calls, by enough
shares). Strikes set how big the capped loss is, not whether it is capped, so a
debit spread counts as defined just as a credit spread does. Note what this
deliberately excludes: a calendar's long leg lives in a *later* expiration,
so it does not cap the loss the near-term short can inflict at that short's
expiration — calendars and diagonals are UNDEFINED. A jade lizard is UNDEFINED
too: the call side is capped, the put side is naked, and worst case wins.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tastydesk.core.models import ZERO, Direction, Leg, OptionType, RiskProfile, StrategyType

__all__ = ["classify"]


@dataclass(frozen=True, slots=True)
class _OptionPos:
    """One netted option position: all legs sharing a contract collapsed into one."""

    expiration: date
    option_type: OptionType
    strike: Decimal
    direction: Direction
    quantity: Decimal
    multiplier: Decimal

    @property
    def is_short(self) -> bool:
        return self.direction is Direction.SHORT

    @property
    def is_put(self) -> bool:
        return self.option_type is OptionType.PUT


def classify(legs: list[Leg]) -> tuple[StrategyType, RiskProfile]:
    """Name the structure these legs form and say whether its risk is capped.

    Returns ``(StrategyType.CUSTOM, RiskProfile.UNDEFINED)`` for anything that
    is not recognisably one of the known structures, including legs that are
    missing the strike or expiration we would need to reason about them.
    """
    active = [leg for leg in legs if leg.quantity != ZERO]
    if not active:
        return StrategyType.CUSTOM, RiskProfile.UNDEFINED

    # A trade spanning two underlyings is a grouping mistake, not a structure.
    if len({leg.underlying.strip().upper() for leg in active}) > 1:
        return StrategyType.CUSTOM, RiskProfile.UNDEFINED

    option_legs = [leg for leg in active if leg.is_option]
    other_legs = [leg for leg in active if not leg.is_option]

    # Without a strike and an expiration we cannot tell a spread from a naked
    # short, and a wrong guess is worse than no guess.
    if any(leg.strike is None or leg.expiration is None for leg in option_legs):
        return StrategyType.CUSTOM, RiskProfile.UNDEFINED

    positions = _net_positions(option_legs)
    net_shares = sum((leg.signed_quantity for leg in other_legs), ZERO)

    strategy_type = _name_structure(positions, other_legs, net_shares)
    risk = (
        RiskProfile.DEFINED
        if _shorts_are_covered(positions, net_shares)
        else RiskProfile.UNDEFINED
    )
    return strategy_type, risk


def _net_positions(option_legs: list[Leg]) -> list[_OptionPos]:
    """Collapse legs onto contracts, netting long against short on the same contract.

    A roll or a partial close can leave the same contract represented twice.
    Buying back one of two short puts leaves one short put, and that is the
    position the risk engine has to see.
    """
    net: dict[tuple[date, OptionType, Decimal, Decimal], Decimal] = defaultdict(lambda: ZERO)
    for leg in option_legs:
        key = (leg.expiration, leg.option_type, leg.strike, leg.multiplier)
        net[key] += leg.signed_quantity

    positions = [
        _OptionPos(
            expiration=expiration,
            option_type=option_type,
            strike=strike,
            direction=Direction.SHORT if qty < ZERO else Direction.LONG,
            quantity=abs(qty),
            multiplier=multiplier,
        )
        for (expiration, option_type, strike, multiplier), qty in net.items()
        if qty != ZERO
    ]
    # Sorting makes the classifier order-insensitive: the broker's leg order
    # never reaches any of the shape tests below.
    positions.sort(key=lambda p: (p.expiration, p.option_type, p.strike, p.direction))
    return positions


def _name_structure(
    positions: list[_OptionPos],
    other_legs: list[Leg],
    net_shares: Decimal,
) -> StrategyType:
    if not positions:
        return StrategyType.EQUITY if other_legs else StrategyType.CUSTOM

    if other_legs and net_shares != ZERO:
        # Shares in the mix: the only structure we name is the covered call.
        # Everything else (covered strangles, stock repair, protective puts)
        # stays CUSTOM rather than borrowing a name that implies a payoff.
        return _name_with_shares(positions, net_shares)

    match len(positions):
        case 1:
            return _name_single(positions[0])
        case 2:
            return _name_pair(positions[0], positions[1])
        case 3:
            return _name_triple(positions)
        case 4:
            return _name_quad(positions)
        case _:
            return StrategyType.CUSTOM


def _name_with_shares(positions: list[_OptionPos], net_shares: Decimal) -> StrategyType:
    if len(positions) != 1:
        return StrategyType.CUSTOM
    pos = positions[0]
    # Covered means the shares can actually be delivered against assignment:
    # 100 shares per contract. 100 shares behind two short calls covers one of
    # them and leaves the other naked — that is not a covered call.
    if pos.is_short and not pos.is_put and net_shares >= pos.quantity * pos.multiplier:
        return StrategyType.COVERED_CALL
    return StrategyType.CUSTOM


def _name_single(pos: _OptionPos) -> StrategyType:
    if pos.is_short:
        return StrategyType.NAKED_PUT if pos.is_put else StrategyType.NAKED_CALL
    return StrategyType.LONG_PUT if pos.is_put else StrategyType.LONG_CALL


def _name_pair(first: _OptionPos, second: _OptionPos) -> StrategyType:
    same_expiration = first.expiration == second.expiration
    same_type = first.option_type is second.option_type
    same_quantity = first.quantity == second.quantity

    if same_type:
        if same_expiration:
            if not same_quantity:
                # Unequal contracts on the same expiry: the extra shorts (or
                # extra longs) change the payoff completely. Ratio, not vertical.
                if first.is_short == second.is_short:
                    return StrategyType.CUSTOM
                return StrategyType.RATIO_SPREAD
            if first.is_short == second.is_short:
                return StrategyType.CUSTOM  # two shorts or two longs at different strikes
            return _name_vertical(first, second)
        # Different expirations, same type: time spreads. Anything other than a
        # clean one-to-one long/short pair we refuse to name.
        if not same_quantity or first.is_short == second.is_short:
            return StrategyType.CUSTOM
        return StrategyType.CALENDAR if first.strike == second.strike else StrategyType.DIAGONAL

    # Put against call.
    if not same_expiration or not same_quantity:
        return StrategyType.CUSTOM
    if first.is_short and second.is_short:
        return (
            StrategyType.SHORT_STRADDLE if first.strike == second.strike else StrategyType.SHORT_STRANGLE
        )
    if not first.is_short and not second.is_short:
        return StrategyType.LONG_STRADDLE if first.strike == second.strike else StrategyType.LONG_STRANGLE
    # One long, one short, opposite types: a synthetic/risk reversal. Unnamed.
    return StrategyType.CUSTOM


def _name_vertical(first: _OptionPos, second: _OptionPos) -> StrategyType:
    short = first if first.is_short else second
    long_ = second if first.is_short else first
    if short.strike == long_.strike:
        return StrategyType.CUSTOM
    if short.is_put:
        # Selling the higher put and buying the lower one takes in a credit.
        return (
            StrategyType.PUT_CREDIT_SPREAD
            if short.strike > long_.strike
            else StrategyType.PUT_DEBIT_SPREAD
        )
    return (
        StrategyType.CALL_CREDIT_SPREAD if short.strike < long_.strike else StrategyType.CALL_DEBIT_SPREAD
    )


def _name_triple(positions: list[_OptionPos]) -> StrategyType:
    if len({p.expiration for p in positions}) != 1:
        return StrategyType.CUSTOM
    if len({p.quantity for p in positions}) != 1:
        return StrategyType.CUSTOM

    short_puts = [p for p in positions if p.is_put and p.is_short]
    long_puts = [p for p in positions if p.is_put and not p.is_short]
    short_calls = [p for p in positions if not p.is_put and p.is_short]
    long_calls = [p for p in positions if not p.is_put and not p.is_short]

    # Jade lizard: naked short put funding a call credit spread. The long call
    # above the short call caps the upside; nothing caps the downside, which is
    # exactly why it is priced the way it is.
    if len(short_puts) == 1 and not long_puts and len(short_calls) == 1 and len(long_calls) == 1:
        if long_calls[0].strike > short_calls[0].strike:
            return StrategyType.JADE_LIZARD
    return StrategyType.CUSTOM


def _name_quad(positions: list[_OptionPos]) -> StrategyType:
    if len({p.expiration for p in positions}) != 1:
        return StrategyType.CUSTOM
    if len({p.quantity for p in positions}) != 1:
        return StrategyType.CUSTOM

    short_puts = [p for p in positions if p.is_put and p.is_short]
    long_puts = [p for p in positions if p.is_put and not p.is_short]
    short_calls = [p for p in positions if not p.is_put and p.is_short]
    long_calls = [p for p in positions if not p.is_put and not p.is_short]
    if not (len(short_puts) == len(long_puts) == len(short_calls) == len(long_calls) == 1):
        return StrategyType.CUSTOM

    short_put, long_put = short_puts[0], long_puts[0]
    short_call, long_call = short_calls[0], long_calls[0]
    # Wings must sit outside the body, or this is not an iron anything.
    if long_put.strike >= short_put.strike or long_call.strike <= short_call.strike:
        return StrategyType.CUSTOM
    if short_put.strike > short_call.strike:
        return StrategyType.CUSTOM  # inverted body: a different animal, do not name it
    return StrategyType.IRON_FLY if short_put.strike == short_call.strike else StrategyType.IRON_CONDOR


def _shorts_are_covered(positions: list[_OptionPos], net_shares: Decimal) -> bool:
    """True when every short option's loss is capped by a long or by shares.

    Within one expiration, a long option of the same type caps a same-type
    short whatever its strike: past the point where both are in the money the
    deltas offset, so the loss stops growing. The strikes set the *size* of the
    max loss (a 10-wide spread risks 10), not whether one exists — which is why
    a put debit spread is as defined-risk as a put credit spread.

    Coverage is checked *within one expiration*. A long option in a later cycle
    is not protection: the near short can be assigned, or gap through its
    strike, while the long still holds time value and has not paid out. That is
    why calendars and diagonals come back UNDEFINED.

    Quantities are compared in notional units (contracts x multiplier) rather
    than contract counts, so two short puts against one long put is correctly
    seen as one naked put, and a mini contract never appears to cover a
    standard one.
    """
    if net_shares < ZERO:
        return False  # short stock: the upside loss has no ceiling

    share_cover = net_shares
    exposure: dict[tuple[date, OptionType], Decimal] = defaultdict(lambda: ZERO)
    for pos in positions:
        notional = pos.quantity * pos.multiplier
        exposure[(pos.expiration, pos.option_type)] += -notional if pos.is_short else notional

    for (_expiration, option_type), net in exposure.items():
        if net >= ZERO:
            continue
        uncovered = -net
        if option_type is OptionType.PUT:
            return False  # nothing else in the account caps a short put
        # Shares can stand in for a long call: assignment simply delivers stock
        # we already hold. Each block of shares can only do that once.
        used = min(uncovered, share_cover)
        share_cover -= used
        if uncovered - used > ZERO:
            return False
    return True
