"""What the book is worth if the market moves, time passes, or volatility changes.

The payoff diagram answers one question — where does this land at expiration —
and refuses to answer any other, because between now and then an option is
worth more than its intrinsic value and no strike arithmetic knows how much
more. This module is that "how much more": Black-Scholes, fed with the leg's
own implied volatility, the strikes it actually holds and the days actually
left.

Three dials, because those are the three things that move an option's price:
where the underlying is, how much time is left, and what the market thinks
volatility is. Everything else is held still, which is the point of a
scenario — it is not a forecast, it is the same position priced under stated
conditions.

The conventions are the ones used everywhere else in this app. A leg's value is
what closing it would produce, negative for anything short. A future's value is
the move since entry, because nothing changed hands when it was bought. And a
figure that cannot be computed honestly is ``None``: an option with no implied
volatility to price against is not worth zero, it is unknown, and a total that
silently dropped it would be wrong in a direction the reader cannot see.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache

from tastydesk.core.models import ZERO, Leg, OptionType, Strategy

__all__ = [
    "Scenario",
    "black_scholes",
    "implied_vol",
    "leg_underlying",
    "leg_delta",
    "leg_value",
    "strategy_delta",
    "strategy_pnl",
    "strategy_value",
]

_DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True, slots=True)
class Scenario:
    """The three dials, as offsets from where things stand now.

    ``price_shift`` is a fraction of the underlying: 0.02 is two percent up,
    applied per product so a book of bonds and soybeans moves by the same
    proportion rather than by the same number of points.

    ``iv_shift`` is a fraction of the current implied volatility, not a number
    of points: an underlying at 12% and one at 56% do not both move to 22% when
    volatility rises, and reading it as relative keeps a grain from swamping
    every other row.

    ``days`` is how far forward to stand. A leg whose expiry has passed by then
    is settled at intrinsic, which is what expiry does.
    """

    price_shift: Decimal = ZERO
    iv_shift: Decimal = ZERO
    days: int = 0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes(
    right: OptionType,
    spot: Decimal,
    strike: Decimal,
    years: Decimal,
    iv: Decimal,
) -> Decimal:
    """The price of one contract, at zero interest and no dividend.

    Zero rate is a deliberate simplification and an honest one at these
    horizons: the carry on a 40-day option is a rounding error beside the
    volatility assumption sitting next to it, and pretending to a precision the
    inputs do not have would be worse than saying so here.

    At or past expiry it returns intrinsic, which is what the contract is worth
    then — the same number the payoff diagram draws.
    """
    if spot <= ZERO or strike <= ZERO:
        return ZERO
    if years <= ZERO or iv <= ZERO:
        intrinsic = (
            max(spot - strike, ZERO) if right is OptionType.CALL else max(strike - spot, ZERO)
        )
        return intrinsic

    s = float(spot)
    k = float(strike)
    t = float(years)
    v = float(iv)
    d1 = (math.log(s / k) + 0.5 * v * v * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)
    if right is OptionType.CALL:
        price = s * _norm_cdf(d1) - k * _norm_cdf(d2)
    else:
        price = k * _norm_cdf(-d2) - s * _norm_cdf(-d1)
    return Decimal(str(max(price, 0.0)))


def implied_vol(
    right: OptionType,
    spot: Decimal,
    strike: Decimal,
    years: Decimal,
    price: Decimal,
) -> Decimal | None:
    """The volatility at which Black-Scholes gives back this price.

    The greeks feed publishes an implied volatility for every leg, and priced
    with it the book did not come back to its own marks: a /ZS January call
    marked at 17.50 modelled at 23.35, a /ZB weekly at 0.85 modelled at 0.11.
    Feed, skew and a stale print all play a part, and none of them is the
    user's problem. So the volatility each leg is priced at is the one its own
    mark implies. The scenario then starts from the price on the screen and
    moves from there, which is the only starting point worth having.

    ``None`` when no volatility reproduces the price — a mark below intrinsic,
    usually a stale quote — in which case the caller falls back to the feed.
    """
    if price <= ZERO or spot <= ZERO or strike <= ZERO or years <= ZERO:
        return None
    intrinsic = max(spot - strike, ZERO) if right is OptionType.CALL else max(strike - spot, ZERO)
    if price < intrinsic:
        return None
    low, high = 0.0001, 5.0
    target = float(price)
    for _ in range(80):
        mid = (low + high) / 2
        value = float(black_scholes(right, spot, strike, years, Decimal(str(mid))))
        if value > target:
            high = mid
        else:
            low = mid
    solved = Decimal(str((low + high) / 2))
    return solved if solved < Decimal("4.99") else None


# The same leg is solved for its volatility once per curve point otherwise —
# forty-one times for one drag of a dial. Its inputs are the live mark and
# today's spot, so the answer only changes when those do.
_implied_vol_cached = lru_cache(maxsize=8192)(implied_vol)


def _option_inputs(
    leg: Leg,
    spot: Decimal | None,
    scenario: Scenario,
    today: date,
) -> tuple[OptionType, Decimal, Decimal, Decimal, Decimal] | None:
    """Right, moved spot, strike, years left and volatility under the scenario.

    Shared by the price and the delta so the two can never be computed from
    different assumptions.
    """
    if leg.strike is None or spot is None:
        return None
    left = leg.dte(today)
    if left is None:
        return None
    moved = spot * (Decimal(1) + scenario.price_shift)
    right = leg.option_type or OptionType.CALL

    # The volatility the mark implies, measured at today's price and today's
    # time left, so that with every dial at zero the leg is worth exactly what
    # the screen says. The feed's figure is the fallback, not the source.
    base = None
    if leg.mark is not None and left > 0:
        base = _implied_vol_cached(right, spot, leg.strike, Decimal(left) / _DAYS_PER_YEAR, leg.mark)
    if base is None:
        base = leg.iv
    if base is None or base <= ZERO:
        # No volatility to price against. Silence, not zero.
        return None

    years = Decimal(max(left - scenario.days, 0)) / _DAYS_PER_YEAR
    iv = max(base * (Decimal(1) + scenario.iv_shift), ZERO)
    return right, moved, leg.strike, years, iv


def leg_value(
    leg: Leg,
    spot: Decimal | None,
    scenario: Scenario,
    today: date,
) -> Decimal | None:
    """What closing this leg would produce under the scenario.

    Negative for anything short, matching :attr:`Leg.close_cash_flow`, so the
    scenario figure can be read beside the live one without a sign to remember.
    """
    # An outright contract's own mark is its price; the product quote can sit a
    # tick away, and a tick on /ZB is $31.25 of a gap between "now" and the
    # screen that the scenario would then carry into every change.
    if leg.is_future and leg.mark is not None:
        spot = leg.mark
    if not leg.is_option:
        if spot is None:
            return None
        moved = spot * (Decimal(1) + scenario.price_shift)
        if leg.is_future:
            # Nothing changed hands at entry; what it produces is the move.
            return (moved - leg.open_price) * leg.notional_multiplier
        return moved * leg.notional_multiplier

    inputs = _option_inputs(leg, spot, scenario, today)
    if inputs is None:
        return None
    right, moved, strike, years, iv = inputs
    price = black_scholes(right, moved, strike, years, iv)
    return price * leg.notional_multiplier


def bs_delta(right: OptionType, spot: Decimal, strike: Decimal, years: Decimal, iv: Decimal) -> Decimal:
    """Delta of one contract, per unit of the underlying, at zero rate.

    At expiry it is the step it becomes: one if in the money, zero if not.
    """
    if years <= ZERO or iv <= ZERO or spot <= ZERO or strike <= ZERO:
        itm = spot > strike if right is OptionType.CALL else spot < strike
        if not itm:
            return ZERO
        return Decimal(1) if right is OptionType.CALL else Decimal(-1)
    s, k, t, v = float(spot), float(strike), float(years), float(iv)
    d1 = (math.log(s / k) + 0.5 * v * v * t) / (v * math.sqrt(t))
    call = _norm_cdf(d1)
    return Decimal(str(round(call if right is OptionType.CALL else call - 1.0, 6)))


def leg_delta(
    leg: Leg,
    spot: Decimal | None,
    scenario: Scenario,
    today: date,
) -> Decimal | None:
    """The leg's delta under the scenario, per contract, before direction.

    This is where "delta changes with the strike" lives: a 0.16 put two
    strikes out becomes a 0.45 put once price has fallen to it, and a 0.05
    call goes to nothing. Priced at the same volatility the value is, so the
    delta and the P&L beside it come from one model.
    """
    if not leg.is_option:
        return Decimal(1)
    if leg.is_future and leg.mark is not None:
        spot = leg.mark
    inputs = _option_inputs(leg, spot, scenario, today)
    if inputs is None:
        return None
    return bs_delta(*inputs)


def leg_underlying(leg: Leg) -> str:
    """The contract this leg is actually written on.

    A futures option names its future in its symbol — "./ZBH7 OZBF7 261224P106"
    is an option on /ZBH7 — and it is not always the month the position is
    booked under. Bull ZB holds options on the March bond against a December
    quote; priced off December they were off by a point and a half.
    """
    symbol = leg.symbol.strip()
    if symbol.startswith("./"):
        return symbol[1:].split()[0]
    if leg.is_future:
        return symbol
    return leg.underlying


def strategy_value(
    strategy: Strategy,
    spot: Decimal | None,
    scenario: Scenario,
    today: date,
    spots: dict[str, Decimal] | None = None,
) -> Decimal | None:
    """What every leg together would produce. ``None`` if any leg cannot be priced.

    ``spots`` prices each leg off its own contract where one is known, with
    ``spot`` as the fallback for everything else.
    """
    total = ZERO
    for leg in strategy.legs:
        own = (spots or {}).get(leg_underlying(leg), spot)
        value = leg_value(leg, own, scenario, today)
        if value is None:
            return None
        total += value
    return total


def strategy_pnl(
    strategy: Strategy,
    spot: Decimal | None,
    scenario: Scenario,
    today: date,
    spots: dict[str, Decimal] | None = None,
) -> Decimal | None:
    """The trade's P&L under the scenario, on the same basis as ``open_pnl``.

    Credit taken in, plus cash already banked from closes and rolls, plus what
    the remaining legs would produce. With the dials at zero this is what the
    position is worth right now; with ``days`` at the full time to expiry it is
    the payoff diagram.
    """
    value = strategy_value(strategy, spot, scenario, today, spots)
    if value is None:
        return None
    return strategy.net_credit + strategy.closing_cash_flow + value


def strategy_delta(
    strategy: Strategy,
    spot: Decimal | None,
    scenario: Scenario,
    today: date,
    spots: dict[str, Decimal] | None = None,
) -> Decimal | None:
    """Net delta in contracts: each leg's delta times its signed quantity.

    The same figure the legs table totals and the broker shows for a
    position. ``None`` if any leg cannot be priced.
    """
    total = ZERO
    for leg in strategy.legs:
        own = (spots or {}).get(leg_underlying(leg), spot)
        delta = leg_delta(leg, own, scenario, today)
        if delta is None:
            return None
        total += delta * leg.signed_quantity
    return total
