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

from collections.abc import Sequence
from decimal import Decimal

from tastydesk.core.models import (
    CREDIT_STRATEGIES,
    ZERO,
    Direction,
    Leg,
    OptionType,
    Strategy,
    StrategyPnL,
    StrategyType,
)

__all__ = [
    "broker_day_pct",
    "broker_pnl_pct",
    "called_away",
    "covering_legs",
    "day_change",
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


def covering_legs(strategy: Strategy) -> dict[int, Leg]:
    """Which long leg covers each short one, where one does.

    A long call covers a short call struck at or above it; a long put covers a
    short put struck at or below it; and the long must not expire first, or the
    cover is gone while the obligation remains. Longs are spent on the shorts
    nearest the money first, because those are the ones in trouble first, and
    each long is used once -- a short left over after the longs run out is
    naked and keeps every bit of its alarm.

    Returned as the legs themselves rather than as a yes or no, because the
    cover is not only a fact about the risk: it is what the position settles
    into if the short is assigned, and that outcome is a number.
    """
    pairs: dict[int, Leg] = {}
    for right in (OptionType.CALL, OptionType.PUT):
        shorts = [leg for leg in strategy.short_legs if leg.option_type is right and leg.strike]
        longs = [leg for leg in strategy.long_legs if leg.option_type is right and leg.strike]
        if not shorts or not longs:
            continue
        budget = {id(leg): leg.quantity * leg.multiplier for leg in longs}
        ordered = sorted(shorts, key=lambda leg: leg.strike or ZERO, reverse=right is OptionType.PUT)
        for short in ordered:
            needed = short.quantity * short.multiplier
            for long in longs:
                if budget[id(long)] < needed or long.strike is None or short.strike is None:
                    continue
                if right is OptionType.CALL and long.strike > short.strike:
                    continue
                if right is OptionType.PUT and long.strike < short.strike:
                    continue
                if (
                    long.expiration is not None
                    and short.expiration is not None
                    and long.expiration < short.expiration
                ):
                    continue
                budget[id(long)] -= needed
                pairs[id(short)] = long
                break
    return pairs


def called_away(strategy: Strategy) -> Decimal | None:
    """What the trade makes if every covered short is assigned and the cover delivers.

    A diagonal refuses to state a max profit or a max loss, and correctly: its
    legs expire on different days, so no strike arithmetic knows what the later
    one will be worth. But the question the holder of a poor man's covered call
    actually asks as the underlying climbs through his short strike is not
    "what is my theoretical maximum" -- it is "if I get called away here, what
    do I walk away with", and that one is exact. Assignment sells at the short
    strike; exercising the long buys at its strike; the difference, plus the
    cash already taken in, is the answer.

    ``None`` when nothing is covered by a long option, which is when the
    question does not arise.
    """
    covers = covering_legs(strategy)
    if not covers:
        return None
    total = strategy.net_credit + strategy.closing_cash_flow
    settled = ZERO
    for leg in strategy.short_legs:
        cover = covers.get(id(leg))
        if cover is None or leg.strike is None or cover.strike is None:
            continue
        width = leg.strike - cover.strike
        settled += width * leg.quantity * leg.multiplier
    if settled == ZERO and not covers:
        return None
    return total + settled


def day_change(strategy: Strategy) -> Decimal | None:
    """What the position has made or lost today, on the broker's own basis.

    Every platform measures a day from the same place: each contract's close
    price. For a position held overnight that is the previous session's close;
    for one opened today tastytrade backfills it with the fill price, so a
    trade put on this morning correctly shows its move since the fill and not
    since a close it was never part of.

    This app used to answer the question from its own snapshots instead, which
    made the figure depend on whether a background job had happened to run --
    it measured from whenever the last snapshot was taken, left out every
    position opened since, and disagreed with the number on the broker's own
    screen. Asking the broker for its close price removes all three problems.

    ``None`` when any leg is missing a mark or a close, because a total that
    silently omits a leg is not a smaller move, it is a wrong number.
    """
    if not strategy.legs:
        return None
    total = ZERO
    for leg in strategy.legs:
        if leg.mark is None or leg.prior_close is None:
            return None
        total += (leg.mark - leg.prior_close) * leg.notional_multiplier
    return total


def premium_at_risk(strategy: Strategy) -> Decimal:
    """The option premium this trade has collected — the scale its loss rules use.

    This is the denominator of ``pct_of_credit``, and getting it right took two
    wrong answers first, so the reasoning is worth writing down.

    **It is gross credit collected, not credit net of buybacks.** For a chain
    that has been rolled, ``net_credit`` sums every opening and adjusting row:
    a strangle opened for +300 and rolled (old legs bought back for -500, new
    legs sold for +400) reports 700, which is exactly the premium the trader has
    taken in across the life of the trade. "Down 100% of everything I collected"
    is the honest reading of that position, and it is how a premium seller
    actually talks about a rolled chain.

    Netting the buyback out — dividing by ``net_credit + closing_cash_flow`` —
    looks more principled and is a trap. A roll done for roughly scratch drives
    that figure to zero, and a trade down $2,400 then has no loss scale at all
    and scores OK; two dollars either side of scratch swings the reported
    percentage by six figures. Rolling a loser for about even is ordinary
    practice, so the unstable denominator fails precisely where it is needed.
    Gross credit collected is strictly positive for any credit structure and
    grows with each roll, which is both stable and true.

    **Shares are not premium.** A covered call's ``net_credit`` is dominated by
    the stock debit (-10,000 + 200 = -9,800), so a percentage measured against
    it reads -18% for a position down nine times the premium collected. No loss
    rule could ever fire, and ``max_loss`` is rightly ``None`` for a covered
    call, so ``pct_of_max_loss`` cannot cover for it either. Stock is a cost
    basis, not a credit; its opening cash flow comes back out, leaving what the
    options brought in. The stock's own downside is reported by
    :func:`cash_secured_max_loss`, explicitly and separately.

    Note what this figure deliberately is *not*: progress toward the best
    outcome still available. Once cash has been paid out to roll, full credit is
    unreachable, and pretending otherwise is why the two scales appeared to
    contradict each other. That question belongs to ``pct_of_max_profit``, and
    the 50%-profit rule reads it — the rule is, after all, named "manage at 50%
    of max profit".
    """
    share_cash = sum((leg.open_cash_flow for leg in strategy.legs if not leg.is_option), ZERO)
    return strategy.net_credit - share_cash


def has_premium_scale(strategy: Strategy) -> bool:
    """Whether "percent of credit" means anything for this trade.

    It does not for a position with no option in it. An outright futures
    contract's cash flows are settlement, not premium, and dividing a $495
    result by the $2 that happened to sit in ``net_credit`` produced a row
    reading "+22,736% of credit" in the user's own history — a number with no
    meaning that makes every honest number beside it harder to trust.
    """
    return any(leg.is_option for leg in strategy.legs) and premium_at_risk(strategy) != ZERO


def realized_pct_of_credit(strategy: Strategy) -> Decimal | None:
    """Realized result as a share of the premium collected, or None."""
    if not has_premium_scale(strategy):
        return None
    return strategy.realized_pnl / abs(premium_at_risk(strategy))


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

    # The premium seller's loss scale: -1.5 means down 150% of the premium this
    # trade has collected, which is what the 2x stop is written against. See
    # premium_at_risk() for why it is gross credit collected and not credit net
    # of buybacks. Only a position with no option premium at all (pure stock)
    # has no scale, and then the answer is None rather than a division by zero.
    premium = premium_at_risk(strategy)
    pct_of_credit = None
    if open_pnl is not None and has_premium_scale(strategy):
        pct_of_credit = open_pnl / abs(premium)

    # Progress toward the best outcome still available, which is the scale the
    # 50%-profit rule is named after. After a roll this is strictly less than
    # the credit collected, because the cash paid to roll can never come back —
    # so this is the only honest way to ask "am I halfway there yet?".
    # Reported while behind as well as ahead. It used to be positive-only, on
    # the argument that a negative fraction of max profit mixes two scales —
    # which left the column empty on eight of eleven open positions, and an
    # empty column teaches nothing at all. Below zero it says the same thing it
    # says above: where this trade stands against the best it could do.
    pct_of_max_profit = None
    if open_pnl is not None and best is not None and best > ZERO:
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
        realized_pct_of_credit=realized_pct_of_credit(strategy),
        is_credit=credit > ZERO,
        quoted_legs=sum(1 for leg in strategy.legs if leg.mark is not None),
        total_legs=len(strategy.legs),
        called_away=called_away(strategy),
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
    # Same convention as Leg.close_cash_flow: a future's cash is the move from
    # where it was bought, because nothing changed hands when it was.
    if leg.is_future:
        return (underlying_price - leg.open_price) * leg.notional_multiplier
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
    # Everything short and everything an option: the ceiling is the credit, and
    # it does not matter whether the legs share an expiry. Nothing can pay you
    # more than you were paid. The multi-expiration refusal below is about the
    # payoff diagram — where the near expiry's value depends on what extrinsic
    # the far leg still carries — and it was wrongly swallowing this case too,
    # which left a strangle sold across two months with no ceiling, no "% of
    # max profit", and no way for the 50% rule to fire on it.
    if strategy.legs and all(
        leg.is_option and leg.direction is Direction.SHORT for leg in strategy.legs
    ):
        return strategy.net_credit + strategy.closing_cash_flow
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


def broker_pnl_pct(legs: Sequence[Leg]) -> Decimal | None:
    """P&L as a percentage, the way tastytrade's P/L % column works it out.

    tastytrade divides P/L Open by Cost: the gross price paid or received for
    the position, a fixed figure, positive for anything sold and negative for
    anything bought. Across a whole position both are added up before the
    division, so a strangle sold for $1,175 and up $167.50 reads 14.3%, and a
    future's full price counts in the cost, as it does on their screen.

    ``None`` when any leg has no mark, or when the costs net to zero.
    """
    if not legs:
        return None
    cost = ZERO
    gain = ZERO
    for leg in legs:
        if leg.mark is None:
            return None
        cost += -leg.open_price * leg.notional_multiplier
        gain += (leg.mark - leg.open_price) * leg.notional_multiplier
    return None if cost == 0 else gain / abs(cost)


def broker_day_pct(legs: Sequence[Leg]) -> Decimal | None:
    """Today's P&L as a percentage, the way tastytrade's P/L Day % works it out.

    P/L Day divided by the position's value at last session's close, both
    added up across the legs first. A long option that closed at 1.00 and is
    1.25 now is +25%.
    """
    if not legs:
        return None
    prior = ZERO
    move = ZERO
    for leg in legs:
        if leg.mark is None or leg.prior_close is None:
            return None
        prior += -leg.prior_close * leg.notional_multiplier
        move += (leg.mark - leg.prior_close) * leg.notional_multiplier
    return None if prior == 0 else move / abs(prior)
