"""Profit and loss, measured where risk actually lives: the strategy.

Every number the user trusts comes from this module, so it follows three rules.

1. One sign convention, the account's. Money in is positive, money out is
   negative, exactly as ``Transaction.net_value`` arrives from tastytrade. A
   winning short premium trade therefore has ``open_pnl > 0`` even though
   closing it costs cash.
2. Silence beats a confident wrong number. A missing mark makes
   :func:`cost_to_close` return ``None`` and the UI say "partially quoted"; an
   unbounded structure makes :func:`max_loss` return ``None`` rather than a
   comforting fiction.
3. Nothing here ever looks at a single leg's percentage move. The short put of
   a put credit spread can be down 300% while the spread is down 150%; only the
   spread number is real, because the long put gained at the same instant.

Shape of the payoff engine
--------------------------
At expiration a book of options plus shares is a piecewise-linear function of
the underlying price, and every bend sits on a strike. So the whole engine is:
evaluate :func:`payoff_at` at zero and at each strike, and measure the slope of
the two outer rays. Max profit, max loss and breakevens all fall out of those
few points, which is why a vertical, a condor, a jade lizard and a covered call
need no special cases — the structure is read from the legs, not from a label.
"""

from __future__ import annotations

from decimal import Decimal

from tastydesk.core.models import (
    CREDIT_STRATEGIES,
    ZERO,
    Leg,
    OptionType,
    Strategy,
    StrategyPnL,
    StrategyType,
)

__all__ = [
    "compute_pnl",
    "max_profit",
    "max_loss",
    "cost_to_close",
    "breakevens",
    "payoff_at",
    "premium_at_risk",
    "cash_secured_max_loss",
    "jade_lizard_upside_covered",
]

# Used only when a strategy has no strikes at all (a pure share position). The
# payoff is a straight line there, so any probe price reports the same slope.
_REFERENCE_PRICE = Decimal(100)

# Breakevens are interpolated, so they can land on a repeating decimal. Prices
# are quoted in cents; four places is well past anything tradeable.
_BREAKEVEN_QUANTUM = Decimal("0.0001")


# --------------------------------------------------------------------------- #
# Current value
# --------------------------------------------------------------------------- #


def cost_to_close(strategy: Strategy) -> Decimal | None:
    """Cash flow from buying the whole structure back right now.

    Negative for a short structure: you pay to get out. ``None`` when any leg
    is unquoted — a partial sum would silently understate what the trade costs,
    and understating cost is the one error a risk tool must never make.
    """
    total = ZERO
    for leg in strategy.legs:
        cash = leg.close_cash_flow
        if cash is None:
            return None
        total += cash
    return total


def premium_at_risk(strategy: Strategy) -> Decimal:
    """The option premium this trade actually has on the line.

    This is the denominator of ``pct_of_credit``, and two separate corrections
    live in it. Both exist because the obvious denominator — the gross
    ``net_credit`` — quietly lies about trades this user puts on every week.

    1. **Cash that has already settled counts.** ``net_credit`` is the sum of
       every opening *and adjusting* row, so a strangle opened for +300 and
       then rolled (old legs bought back for -500, new legs sold for +400)
       reports ``net_credit`` 700 while only 200 is genuinely still at risk.
       Dividing by 700 divides the user's 2x stop by three and a half, and a
       rolled trade can never trip it. ``closing_cash_flow`` is exactly the
       cash that left, so adding it back in makes this figure equal
       :attr:`Strategy.realized_pnl` — which is precisely what
       :func:`max_profit` already uses for the same reason. Before this, one
       ``StrategyPnL`` could say "you are at 29% of your credit" and "you are
       at 100% of max profit" about the same instant.

       A partial close is treated identically, and that is a deliberate
       decision. A premium seller does manage against the credit he took in,
       so there is an argument for the gross figure; but the cash from a closed
       lot is banked and can never be lost again, the remaining legs cannot win
       it back, and a denominator that disagrees with ``max_profit`` inside one
       object is indefensible whichever direction the cash moved. Consistency
       wins: the scale is always "the cash still on the table".

    2. **Shares are not premium.** A covered call's ``net_credit`` is dominated
       by the stock debit (-10,000 + 200 = -9,800), so a percentage measured
       against it reads -18% for a position down nine times the premium
       collected — no loss rule can ever fire, and ``max_loss`` is rightly
       ``None`` for a covered call, so ``pct_of_max_loss`` cannot cover for it
       either. Stock is a cost basis, not a credit. Its opening cash flow comes
       back out, leaving what the options brought in, which is the only thing
       the seller's percentage rules were ever written about. The stock's own
       downside is reported by :func:`cash_secured_max_loss`, explicitly and
       separately, never smuggled into this ratio.

    Returns the signed premium (positive when credit was taken in). Zero means
    there is no premium scale at all — a pure share position, or a spread
    opened for scratch — and the caller must report ``None`` rather than divide.
    """
    # Only the *opening* share cash flow is removed. If shares were sold to
    # close, that cash sits in closing_cash_flow and belongs in the result: a
    # covered call whose stock has been sold has realised its stock leg, and
    # what is left over really is the premium outcome.
    share_cash = sum((leg.open_cash_flow for leg in strategy.legs if not leg.is_option), ZERO)
    return strategy.net_credit + strategy.closing_cash_flow - share_cash


def compute_pnl(strategy: Strategy) -> StrategyPnL:
    """Full P&L picture for one strategy."""
    credit = strategy.net_credit
    close_cost = cost_to_close(strategy)
    best = max_profit(strategy)
    worst = max_loss(strategy)

    # closing_cash_flow is zero while a strategy is fully open, so this is the
    # documented ``net_credit + cost_to_close``; after a partial close it also
    # keeps the cash already banked in the running total instead of losing it.
    open_pnl = None if close_cost is None else credit + strategy.closing_cash_flow + close_cost

    # The premium seller's home number: +1.0 is the whole credit captured,
    # -1.5 is down 150% of it. Measured against the premium still at risk, not
    # the gross opening credit — see premium_at_risk() for why a rolled trade
    # and a covered call both come out wrong otherwise. No premium means no
    # scale, so the answer is None rather than a division by zero.
    premium = premium_at_risk(strategy)
    pct_of_credit = None
    if open_pnl is not None and premium != ZERO:
        pct_of_credit = open_pnl / abs(premium)

    # Only report progress toward max profit while actually in profit. A
    # negative fraction of max profit mixes two scales and reads as nonsense.
    pct_of_max_profit = None
    if open_pnl is not None and open_pnl > ZERO and best is not None and best > ZERO:
        pct_of_max_profit = open_pnl / best

    # The number that puts "-150% of credit" in perspective: the same trade can
    # be down 150% of a small credit and still have used only a third of its
    # real risk. Undefined-risk trades get None, because there is no honest
    # denominator to divide by.
    pct_of_max_loss = None
    if open_pnl is not None and open_pnl < ZERO and worst is not None and worst > ZERO:
        pct_of_max_loss = -open_pnl / worst

    return StrategyPnL(
        net_credit=credit,
        cost_to_close=close_cost,
        open_pnl=open_pnl,
        pct_of_credit=pct_of_credit,
        max_profit=best,
        max_loss=worst,
        pct_of_max_profit=pct_of_max_profit,
        pct_of_max_loss=pct_of_max_loss,
        realized_pnl=strategy.realized_pnl,
        is_credit=credit > ZERO,
        quoted_legs=sum(1 for leg in strategy.legs if leg.mark is not None),
        total_legs=len(strategy.legs),
    )


# --------------------------------------------------------------------------- #
# Expiration payoff
# --------------------------------------------------------------------------- #


def _expiration_close_cash_flow(leg: Leg, underlying_price: Decimal) -> Decimal:
    """What closing this leg produces if the underlying settles here.

    At expiration an option is worth its intrinsic value, so this is the same
    ``mark * notional_multiplier`` as :attr:`Leg.close_cash_flow` with intrinsic
    substituted for the mark. Shares are worth the share price.
    """
    if leg.is_option:
        if leg.strike is None:
            # An unparsed symbol: contribute nothing rather than raise. The
            # guard in _payoff_points keeps it out of the headline figures.
            return ZERO
        if leg.option_type is OptionType.CALL:
            intrinsic = max(underlying_price - leg.strike, ZERO)
        else:
            intrinsic = max(leg.strike - underlying_price, ZERO)
        return intrinsic * leg.notional_multiplier
    return underlying_price * leg.notional_multiplier


def payoff_at(strategy: Strategy, underlying_price: Decimal) -> Decimal:
    """P&L of the whole trade if the underlying expires at this price.

    Same cash-flow convention as everywhere else: the credit already taken in,
    plus what unwinding each leg at expiration would produce. Cash already
    banked from partial closes is included, so a rolled strategy's diagram
    shows the P&L of the whole chain rather than of its current legs alone.

    For a multi-expiration strategy this treats every leg as expiring together,
    which is a drawing aid, not a valuation — see :func:`max_loss`.
    """
    total = strategy.net_credit + strategy.closing_cash_flow
    for leg in strategy.legs:
        total += _expiration_close_cash_flow(leg, underlying_price)
    return total


def _payoff_points(strategy: Strategy) -> list[Decimal] | None:
    """Zero plus every strike: the only prices where the payoff can bend.

    ``None`` means the structure cannot be read honestly — no legs, an option
    whose symbol would not parse, or more than one expiration.
    """
    if not strategy.legs:
        return None
    # A calendar's value on the near expiry depends on the far leg's remaining
    # extrinsic value, which no strike-based arithmetic can know. Refusing is
    # the only honest answer; a single-expiration figure would be confidently
    # wrong in whichever direction volatility happened to move.
    if strategy.is_multi_expiration:
        return None
    strikes: set[Decimal] = set()
    for leg in strategy.legs:
        if leg.is_option:
            if leg.strike is None:
                return None
            strikes.add(leg.strike)
    return [ZERO, *sorted(strikes)]


def _slope_above(strategy: Strategy, top: Decimal) -> Decimal:
    """P&L change per $1 of underlying above the highest strike."""
    return payoff_at(strategy, top + Decimal(1)) - payoff_at(strategy, top)


def _slope_below(strategy: Strategy, low: Decimal) -> Decimal:
    """P&L change per $1 of underlying below the lowest strike."""
    floor = low - Decimal(1) if low > Decimal(1) else ZERO
    if floor == low:
        return ZERO
    return (payoff_at(strategy, low) - payoff_at(strategy, floor)) / (low - floor)


def _outer_prices(points: list[Decimal]) -> tuple[Decimal, Decimal]:
    """The lowest and highest strike, or a probe price for a share-only book."""
    if len(points) > 1:
        return points[1], points[-1]
    return _REFERENCE_PRICE, _REFERENCE_PRICE


# --------------------------------------------------------------------------- #
# Max profit / max loss
# --------------------------------------------------------------------------- #


def max_profit(strategy: Strategy) -> Decimal | None:
    """Best possible outcome at expiration, or ``None`` if unbounded."""
    if strategy.is_multi_expiration:
        return None
    # For a pure option credit structure the cap is the credit itself, by
    # definition: nothing can pay you more than what you were paid. Taking the
    # shortcut means a strategy whose symbols failed to parse still reports the
    # one number that is certain. A covered call is in CREDIT_STRATEGIES but
    # owns shares, so its upside is the stock's too — it takes the long road.
    #
    # closing_cash_flow belongs in the cap for the same reason it belongs in
    # open_pnl: after a roll or a partial close, cash has already left or
    # entered the account and the remaining legs can never win it back. Quoting
    # the gross opening credit would name a ceiling the trade can no longer
    # reach, and every "% of max profit" measured against it would read low.
    # This keeps the shortcut agreeing with payoff_at, which counts that cash.
    if strategy.strategy_type in CREDIT_STRATEGIES and all(leg.is_option for leg in strategy.legs):
        return strategy.net_credit + strategy.closing_cash_flow

    points = _payoff_points(strategy)
    if points is None:
        return None
    _, top = _outer_prices(points)
    if _slope_above(strategy, top) > ZERO:
        return None  # long calls / long shares: no ceiling to report
    # A downward slope at the bottom (a long put, short shares) peaks at a zero
    # underlying, and zero is already the first point, so max() finds it.
    return max(payoff_at(strategy, price) for price in points)


def max_loss(strategy: Strategy) -> Decimal | None:
    """Worst outcome at expiration as a positive magnitude, or ``None``.

    ``None`` means "do not quote a number here": either the loss truly grows
    without limit (a naked short call), or the only thing stopping it is the
    underlying reaching zero (a naked short put, long shares, a covered call).
    That second case has a finite arithmetic answer, and reporting it as "max
    loss" is exactly the fiction this project refuses — see
    :func:`cash_secured_max_loss` for the explicit opt-in.

    A jade lizard lands here too. Its call spread may be fully financed by the
    credit, so there is genuinely no upside risk, but the naked put underneath
    leaves the downside undefined; the structure is not defined-risk just
    because one side of it is. :func:`jade_lizard_upside_covered` answers the
    upside question on its own.
    """
    points = _payoff_points(strategy)
    if points is None:
        return None
    low, top = _outer_prices(points)
    if _slope_above(strategy, top) < ZERO:
        return None
    if _slope_below(strategy, low) > ZERO:
        return None
    worst = min(payoff_at(strategy, price) for price in points)
    return -worst if worst < ZERO else ZERO


def cash_secured_max_loss(strategy: Strategy) -> Decimal | None:
    """Worst case assuming the underlying goes to zero. Opt-in, and explicit.

    This is the cash-secured worst case for a naked short put (strike x
    multiplier, less the credit) and the stock-to-zero worst case for a covered
    call or a share position. It is deliberately a separate function from
    :func:`max_loss`: the number is real, but treating it as a defined risk is
    how premium sellers talk themselves into positions they cannot survive.

    ``None`` when the upside is unbounded, because then no finite worst case
    exists in the first place. For a genuinely defined structure it returns the
    same figure as :func:`max_loss`.
    """
    points = _payoff_points(strategy)
    if points is None:
        return None
    _, top = _outer_prices(points)
    if _slope_above(strategy, top) < ZERO:
        return None
    worst = min(payoff_at(strategy, price) for price in points)
    return -worst if worst < ZERO else ZERO


def jade_lizard_upside_covered(strategy: Strategy) -> bool | None:
    """True when the credit taken in is at least the call spread's width.

    That is the whole point of the structure: collect more than the call wing
    is wide and the upside cannot lose, however far the underlying runs.
    ``None`` for anything that is not a jade lizard.
    """
    if strategy.strategy_type is not StrategyType.JADE_LIZARD:
        return None
    points = _payoff_points(strategy)
    if points is None:
        return None
    top = points[-1]
    return payoff_at(strategy, top) >= ZERO and _slope_above(strategy, top) >= ZERO


# --------------------------------------------------------------------------- #
# Breakevens
# --------------------------------------------------------------------------- #


def breakevens(strategy: Strategy) -> list[Decimal]:
    """Underlying prices where the trade breaks even at expiration.

    Walks the same piecewise-linear payoff: any sign change between two bends
    is one crossing, found by interpolation, and the ray past the highest
    strike is extrapolated from its slope. Empty when nothing can be said —
    a calendar, an unparsed symbol, or a payoff that never touches zero.
    """
    points = _payoff_points(strategy)
    if points is None:
        return []

    values = [payoff_at(strategy, price) for price in points]
    roots: list[Decimal] = []

    for price, value in zip(points, values, strict=True):
        if value == ZERO:
            roots.append(price)

    for i in range(len(points) - 1):
        y0, y1 = values[i], values[i + 1]
        if (y0 < ZERO < y1) or (y1 < ZERO < y0):
            x0, x1 = points[i], points[i + 1]
            roots.append(x0 + (x1 - x0) * (-y0) / (y1 - y0))

    # Beyond the last strike the payoff is a straight ray, so a crossing out
    # there is solved, not searched for.
    top = points[-1]
    slope = _slope_above(strategy, top)
    if slope != ZERO:
        root = top + (-values[-1]) / slope
        if root > top:
            roots.append(root)

    return sorted({root.quantize(_BREAKEVEN_QUANTUM) for root in roots if root >= ZERO})
