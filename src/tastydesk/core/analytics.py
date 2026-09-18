"""Performance analytics — the "how do I improve" half of Tasty Desk.

Everything here reads :class:`~tastydesk.core.models.Strategy` objects and
answers two questions a premium seller actually needs answered:

1. *What is working?*  :func:`performance` and the ``by_*`` breakdowns.
2. *Am I following my own rules, and does it pay?*  :func:`rule_adherence`.

Scope
-----
Only **closed** strategies are analysed. An open trade's ``realized_pnl`` is a
partial number (it moves when you roll or close half the position), so mixing
it into win rate or expectancy would quietly poison every aggregate. Callers
that want live numbers want the P&L module, not this one.

Sample size is part of every answer
-----------------------------------
A 100% win rate over three trades is not a finding. Every
:class:`PerformanceStats` carries ``trades``, and each metric computed over a
*subset* (the ones with buying power recorded, the ones with a knowable max
profit) carries its own ``*_n`` so the UI can refuse to draw a conclusion.

Sign conventions are inherited from ``models``: money is account cash flow,
positive means cash came in. Two deliberate exceptions are documented on the
fields that use them (``avg_loss`` is a positive magnitude because expectancy
is conventionally written that way; ``largest_loss`` keeps its natural
negative sign).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from tastydesk.core.models import CREDIT_STRATEGIES, ZERO, Strategy, StrategyType

__all__ = [
    "PerformanceStats",
    "RuleSet",
    "RuleAdherence",
    "DEFAULT_RULES",
    "DTE_BUCKETS",
    "IV_RANK_BUCKETS",
    "SHORT_DELTA_BUCKETS",
    "UNKNOWN_BUCKET",
    "BUCKET_DIMENSIONS",
    "performance",
    "by_strategy_type",
    "by_underlying",
    "by_bucket",
    "rule_adherence",
    "max_profit_at_close",
    "closed_strategies",
]

_HUNDRED = Decimal(100)
_ONE = Decimal(1)
_SECONDS_PER_DAY = Decimal(86400)

# A trade opened and closed inside one session still tied up buying power for a
# day as far as the broker is concerned. Without a floor, a 20-minute scalp
# divides by ~0.014 days and produces a per-day return in the hundreds, which
# would dominate every ranking it appears in.
_MIN_BP_DAYS = Decimal(1)


# --------------------------------------------------------------------------
# Aggregate statistics
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    """Realized performance over a set of closed trades.

    Win/loss/scratch split follows the obvious rule: ``realized_pnl > 0`` is a
    win, ``< 0`` a loss, exactly ``0`` a scratch. Scratches stay in ``trades``
    and therefore drag ``win_rate`` down, which is the honest treatment — a
    trade that cost you a week of buying power to break even happened.

    Expectancy convention
    ---------------------
    ``expectancy = win_rate * avg_win - loss_rate * avg_loss`` where
    ``avg_loss`` is a **positive magnitude** and ``loss_rate = losses /
    trades``. It is the expected dollars per trade taken, and is algebraically
    identical to ``total_pnl / trades``; it is computed the long way because
    that is the form the UI shows and the form the user reasons in.
    """

    trades: int

    wins: int
    losses: int
    scratches: int
    win_rate: float

    # Both positive magnitudes. Zero when the corresponding count is zero —
    # check ``wins``/``losses`` before showing them.
    avg_win: Decimal
    avg_loss: Decimal

    expectancy: Decimal
    total_pnl: Decimal

    # Gross profit / gross loss. None when there were no losses at all: that is
    # an undefined ratio, not an infinite one and certainly not zero.
    profit_factor: Decimal | None

    avg_days_in_trade: float | None

    # realized_pnl / max_profit at close, averaged. Only credit structures have
    # a knowable max profit from the trade record alone, hence the separate n.
    avg_pct_of_max_profit_captured: Decimal | None

    # The metric that actually ranks strategies for a premium seller: dollars
    # earned per dollar of buying power per day. $50 on $500 over 10 days
    # (0.010) beats $80 on $2000 over 40 days (0.001), and win rate cannot see
    # the difference at all.
    pnl_per_bp_day: Decimal | None

    # Signed. largest_win is positive, largest_loss is negative. None means
    # there were no wins / no losses to take a maximum over.
    largest_win: Decimal | None
    largest_loss: Decimal | None

    # Sample sizes for the metrics computed over a subset of ``trades``.
    pct_of_max_profit_n: int = 0
    pnl_per_bp_day_n: int = 0
    days_in_trade_n: int = 0

    @property
    def loss_rate(self) -> float:
        return self.losses / self.trades if self.trades else 0.0

    @property
    def is_significant(self) -> bool:
        """Crude guard so the UI can label thin samples. Not a p-value."""
        return self.trades >= 20


def closed_strategies(strategies: Sequence[Strategy], *, verified_only: bool = True) -> list[Strategy]:
    """The closed subset, in close order. See the module docstring for why.

    Trades closed by expiry with no closing transaction to confirm the outcome
    are left out by default. One such position in this user's book was a deep
    in-the-money LEAP that had clearly been exercised into shares; taking its
    recorded cash flows at face value booked a $31,861 loss that never happened
    and moved the year's realized figure by four times its true value. A win
    rate is worth nothing if one unresolved trade can swing it, so they are
    counted separately and reported rather than averaged in.
    """
    done = [s for s in strategies if s.closed_at is not None and not (verified_only and s.outcome_unverified)]
    done.sort(key=lambda s: (s.closed_at, s.id))  # type: ignore[arg-type,return-value]
    return done


def unverified_strategies(strategies: Sequence[Strategy]) -> list[Strategy]:
    """Closed trades whose outcome could not be confirmed, so they need a look."""
    return [s for s in strategies if s.closed_at is not None and s.outcome_unverified]


def max_profit_at_close(strategy: Strategy) -> Decimal | None:
    """Max profit the trade could have made, or None when it is unknowable.

    For every credit structure the user trades the profit is capped at the
    credit taken in, which is why "% of max profit" and "% of credit" are the
    same number for a strangle or an iron condor. For debit structures max
    profit depends on strike width and where the underlying finished, neither
    of which the strategy record carries, so we return None instead of
    inventing a denominator that would silently distort the average.
    """
    # A position taken away by assignment captured nothing, whatever the option
    # leg's own cash says. The assigned put in the user's own scenario kept its
    # full $198 credit and read as a flawless 100% capture while the shares it
    # delivered cost the account $8,005 — the win-rate lie in a different column.
    if strategy.closed_by_assignment:
        return None
    if strategy.strategy_type in CREDIT_STRATEGIES and strategy.net_credit > ZERO:
        return strategy.net_credit
    return None


def _days_held(strategy: Strategy) -> Decimal | None:
    """Fractional calendar days from open to close. None while still open."""
    if strategy.closed_at is None:
        return None
    days = Decimal((strategy.closed_at - strategy.opened_at).total_seconds()) / _SECONDS_PER_DAY
    return days if days > ZERO else ZERO


def _empty_stats() -> PerformanceStats:
    return PerformanceStats(
        trades=0,
        wins=0,
        losses=0,
        scratches=0,
        win_rate=0.0,
        avg_win=ZERO,
        avg_loss=ZERO,
        expectancy=ZERO,
        total_pnl=ZERO,
        profit_factor=None,
        avg_days_in_trade=None,
        avg_pct_of_max_profit_captured=None,
        pnl_per_bp_day=None,
        largest_win=None,
        largest_loss=None,
    )


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, ZERO) / Decimal(len(values))


def performance(strategies: Sequence[Strategy]) -> PerformanceStats:
    """Realized performance over the closed trades in ``strategies``.

    Open trades are ignored, not zero-filled. Trades missing an input for a
    derived metric (no buying power recorded, no knowable max profit) are
    skipped for that metric only and counted in its ``*_n``.
    """
    done = closed_strategies(strategies)
    if not done:
        return _empty_stats()

    pnls = [s.realized_pnl for s in done]
    wins = [p for p in pnls if p > ZERO]
    losses = [p for p in pnls if p < ZERO]
    trades = len(pnls)
    scratches = trades - len(wins) - len(losses)

    gross_profit = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)  # positive magnitude

    avg_win = gross_profit / Decimal(len(wins)) if wins else ZERO
    avg_loss = gross_loss / Decimal(len(losses)) if losses else ZERO

    n = Decimal(trades)
    expectancy = (Decimal(len(wins)) / n) * avg_win - (Decimal(len(losses)) / n) * avg_loss

    # No losses means the ratio has no denominator. Reporting 0 would read as
    # "terrible", reporting infinity would poison every sort the UI does.
    profit_factor = gross_profit / gross_loss if gross_loss > ZERO else None

    held = [d for d in (_days_held(s) for s in done) if d is not None]
    avg_days = float(sum(held, ZERO) / Decimal(len(held))) if held else None

    captured: list[Decimal] = []
    for s in done:
        cap = max_profit_at_close(s)
        if cap is not None and cap > ZERO:
            captured.append(s.realized_pnl / cap)

    # Skip, never substitute zero: a trade with no recorded buying power would
    # otherwise divide by zero, and treating it as "earned nothing per dollar"
    # is a different and wrong claim from "we do not know".
    per_bp_day: list[Decimal] = []
    for s in done:
        bp = s.buying_power_used
        days = _days_held(s)
        if bp is None or bp <= ZERO or days is None:
            continue
        per_bp_day.append(s.realized_pnl / (bp * max(days, _MIN_BP_DAYS)))

    return PerformanceStats(
        trades=trades,
        wins=len(wins),
        losses=len(losses),
        scratches=scratches,
        win_rate=len(wins) / trades,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        total_pnl=sum(pnls, ZERO),
        profit_factor=profit_factor,
        avg_days_in_trade=avg_days,
        avg_pct_of_max_profit_captured=_mean(captured),
        pnl_per_bp_day=_mean(per_bp_day),
        largest_win=max(wins) if wins else None,
        largest_loss=min(losses) if losses else None,
        pct_of_max_profit_n=len(captured),
        pnl_per_bp_day_n=len(per_bp_day),
        days_in_trade_n=len(held),
    )


def by_strategy_type(strategies: Sequence[Strategy]) -> dict[StrategyType, PerformanceStats]:
    """Performance split by structure, ordered by strategy type name."""
    groups: dict[StrategyType, list[Strategy]] = {}
    for s in closed_strategies(strategies):
        groups.setdefault(s.strategy_type, []).append(s)
    return {k: performance(v) for k, v in sorted(groups.items(), key=lambda kv: kv[0].value)}


def by_underlying(strategies: Sequence[Strategy]) -> dict[str, PerformanceStats]:
    """Performance split by ticker, alphabetically."""
    groups: dict[str, list[Strategy]] = {}
    for s in closed_strategies(strategies):
        groups.setdefault(s.underlying, []).append(s)
    return {k: performance(v) for k, v in sorted(groups.items())}


# --------------------------------------------------------------------------
# Entry-condition buckets
# --------------------------------------------------------------------------

# DTE buckets are whole-day ranges, inclusive at both ends: 21 DTE lands in
# "8-21", 22 in "22-45". The continuous dimensions below are lower-inclusive
# and upper-exclusive instead, so a 0.30 delta short strike is "0.30+" — the
# boundary belongs to the riskier bucket, which is the direction you want to
# err in when the whole point is spotting where losses come from.
DTE_BUCKETS = ("0-7", "8-21", "22-45", "46-90", "91+")
IV_RANK_BUCKETS = ("<20", "20-35", "35-50", "50+")
SHORT_DELTA_BUCKETS = ("<0.10", "0.10-0.20", "0.20-0.30", "0.30+")
# IV rank and short-strike delta at entry are not in the transaction record, so
# they exist only for positions this app was running when they opened. Naming
# the bucket for that keeps a slice with no data yet from reading like a finding
# about the trades themselves.
UNKNOWN_BUCKET = "not recorded at entry"


def _dte_bucket(strategy: Strategy) -> str | None:
    dte = strategy.dte_at_entry
    if dte is None:
        return None
    if dte <= 7:
        return "0-7"
    if dte <= 21:
        return "8-21"
    if dte <= 45:
        return "22-45"
    if dte <= 90:
        return "46-90"
    return "91+"


def _iv_rank_pct(value: Decimal) -> Decimal:
    """Normalise IV rank onto a 0-100 percent scale.

    tastytrade returns IV rank as a Decimal fraction (0.35 means 35%), but a
    hand-entered journal figure is usually the percent. A fraction cannot
    exceed 1.0, so anything above that is unambiguously already a percent.
    """
    return value * _HUNDRED if value <= _ONE else value


def _iv_rank_bucket(strategy: Strategy) -> str | None:
    raw = strategy.iv_rank_at_entry
    if raw is None:
        return None
    pct = _iv_rank_pct(raw)
    if pct < Decimal(20):
        return "<20"
    if pct < Decimal(35):
        return "20-35"
    if pct < Decimal(50):
        return "35-50"
    return "50+"


def _short_delta_bucket(strategy: Strategy) -> str | None:
    raw = strategy.short_delta_at_entry
    if raw is None:
        return None
    # A short put's delta is negative and a short call's is positive; only the
    # distance from the money matters for bucketing.
    delta = abs(raw)
    if delta < Decimal("0.10"):
        return "<0.10"
    if delta < Decimal("0.20"):
        return "0.10-0.20"
    if delta < Decimal("0.30"):
        return "0.20-0.30"
    return "0.30+"


BUCKET_DIMENSIONS: Mapping[str, tuple[tuple[str, ...], Callable[[Strategy], str | None]]] = {
    "dte_at_entry": (DTE_BUCKETS, _dte_bucket),
    "iv_rank_at_entry": (IV_RANK_BUCKETS, _iv_rank_bucket),
    "short_delta_at_entry": (SHORT_DELTA_BUCKETS, _short_delta_bucket),
}


def by_bucket(strategies: Sequence[Strategy], dimension: str) -> dict[str, PerformanceStats]:
    """Performance split by an entry condition.

    ``dimension`` is one of ``dte_at_entry``, ``iv_rank_at_entry``,
    ``short_delta_at_entry``. Buckets with no trades are omitted; trades that
    never recorded the value land in an explicit ``"unknown"`` bucket rather
    than vanishing, so the counts still add up to the number of closed trades.
    """
    try:
        order, classify = BUCKET_DIMENSIONS[dimension]
    except KeyError:
        raise ValueError(
            f"unknown bucket dimension {dimension!r}; expected one of {sorted(BUCKET_DIMENSIONS)}"
        ) from None

    groups: dict[str, list[Strategy]] = {}
    for s in closed_strategies(strategies):
        groups.setdefault(classify(s) or UNKNOWN_BUCKET, []).append(s)

    ordered = [*order, UNKNOWN_BUCKET]
    return {label: performance(groups[label]) for label in ordered if label in groups}


# --------------------------------------------------------------------------
# Rule adherence
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleSet:
    """The user's actual management rules.

    The three headline numbers are the rules as the user states them. The two
    bands are tolerances, not rules: a real fill never lands exactly on 50% of
    max profit, so without a band every single trade would score as a
    violation and the metric would say nothing.
    """

    profit_target_pct: Decimal = Decimal("0.50")  # manage winners at 50% of max profit
    dte_exit: int = 21  # close or roll at 21 DTE
    stop_loss_multiple: Decimal = Decimal("2.0")  # stop at 2x the credit received

    # Expressed in units of max profit: a winner closed anywhere from 50% to
    # 65% of max profit counts as managed at the target.
    profit_target_band: Decimal = Decimal("0.15")
    # Expressed as a fraction of the stop level: closing between 1.5x and 2.5x
    # the credit counts as having honoured a 2x stop.
    stop_band: Decimal = Decimal("0.25")


DEFAULT_RULES = RuleSet()


@dataclass(frozen=True, slots=True)
class RuleAdherence:
    """How often one rule was obeyed, and what obeying it was worth.

    ``pnl_when_followed`` / ``pnl_when_violated`` are **sums**, not averages;
    divide by ``followed`` / ``violated`` for a per-trade figure.

    The counterfactual answers "what would this rule have produced if applied
    mechanically". It is deliberately scoped: ``counterfactual_trades`` is the
    number of trades it actually covers and ``counterfactual_excluded`` the
    number it could not be computed for. Never show the counterfactual without
    those two numbers — a partial counterfactual presented as complete is
    worse than no counterfactual.
    """

    rule: str
    description: str

    # False when the data needed to judge the rule at all is missing. Every
    # count below is zero in that case; show ``unmeasurable_reason`` instead.
    measurable: bool
    unmeasurable_reason: str | None

    trades_considered: int  # closed trades examined
    not_applicable: int  # examined, but the rule never fired or cannot be judged

    followed: int
    violated: int
    adherence_rate: float | None  # None when no trade could be judged

    pnl_when_followed: Decimal
    pnl_when_violated: Decimal

    counterfactual_pnl: Decimal | None
    counterfactual_actual_pnl: Decimal | None  # actual P&L over the SAME trades
    counterfactual_delta: Decimal | None  # counterfactual - actual
    counterfactual_trades: int
    counterfactual_excluded: int

    distribution: tuple[tuple[str, int], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def judged(self) -> int:
        return self.followed + self.violated


def _unmeasurable(rule: str, description: str, reason: str, considered: int) -> RuleAdherence:
    return RuleAdherence(
        rule=rule,
        description=description,
        measurable=False,
        unmeasurable_reason=reason,
        trades_considered=considered,
        not_applicable=considered,
        followed=0,
        violated=0,
        adherence_rate=None,
        pnl_when_followed=ZERO,
        pnl_when_violated=ZERO,
        counterfactual_pnl=None,
        counterfactual_actual_pnl=None,
        counterfactual_delta=None,
        counterfactual_trades=0,
        counterfactual_excluded=considered,
    )


def _rate(followed: int, violated: int) -> float | None:
    judged = followed + violated
    return followed / judged if judged else None


def _profit_target_rule(done: Sequence[Strategy], rules: RuleSet) -> RuleAdherence:
    """Did winners get taken off at the profit target, or ridden past it?

    Holding a winner past the target is a violation *even though it ended
    green*: the rule exists to convert a 50% winner into cash and free the
    buying power, and the trades that punish you for ignoring it are the ones
    that reverse — which by definition are not in the winners list.

    A winner that never reached the target never triggered the rule, so it is
    counted as not applicable rather than as a violation; closing it early at
    21 DTE is the other rule's business.
    """
    target = rules.profit_target_pct
    ceiling = target + rules.profit_target_band

    followed = violated = not_applicable = 0
    pnl_followed = pnl_violated = ZERO
    cf_pnl = cf_actual = ZERO
    cf_trades = cf_excluded = 0

    for s in done:
        cap = max_profit_at_close(s)
        pnl = s.realized_pnl
        if cap is None or cap <= ZERO or pnl <= ZERO or pnl / cap < target:
            not_applicable += 1
            cf_excluded += 1
            continue

        captured = pnl / cap
        cf_trades += 1
        cf_pnl += target * cap  # mechanically: closed the moment the target printed
        cf_actual += pnl

        if captured <= ceiling:
            followed += 1
            pnl_followed += pnl
        else:
            violated += 1
            pnl_violated += pnl

    notes = (
        f"Judged against {target:%} of max profit with a {rules.profit_target_band:%} band; "
        f"a winner closed beyond {ceiling:%} of max profit was held past the target.",
        f"{cf_excluded} trade(s) excluded from the counterfactual: max profit unknown (debit "
        "structure) or the target was never reached by the close.",
        "The counterfactual is conservative AGAINST the rule. It can only see winners that "
        "finished at or beyond the target, so it charges the rule for the upside it would have "
        "given up while crediting it with nothing for the losers that were up 50% intraday and "
        "reversed. Daily snapshots of open P&L would be needed to see those.",
    )

    return RuleAdherence(
        rule="profit_target",
        description=f"Manage winners at {target:%} of max profit",
        measurable=True,
        unmeasurable_reason=None,
        trades_considered=len(done),
        not_applicable=not_applicable,
        followed=followed,
        violated=violated,
        adherence_rate=_rate(followed, violated),
        pnl_when_followed=pnl_followed,
        pnl_when_violated=pnl_violated,
        counterfactual_pnl=cf_pnl if cf_trades else None,
        counterfactual_actual_pnl=cf_actual if cf_trades else None,
        counterfactual_delta=(cf_pnl - cf_actual) if cf_trades else None,
        counterfactual_trades=cf_trades,
        counterfactual_excluded=cf_excluded,
        notes=notes,
    )


def _dte_close_bins(dte_exit: int) -> tuple[tuple[str, int, int], ...]:
    """Label, low, high (both inclusive) bins for DTE at close."""
    bins: list[tuple[str, int, int]] = [(f"{dte_exit}+", dte_exit, 10_000)]
    if dte_exit > 8:
        bins.append((f"8-{dte_exit - 1}", 8, dte_exit - 1))
    bins.append(("1-7", 1, 7))
    bins.append(("0 or expired", -10_000, 0))
    return tuple(bins)


def _dte_rule(done: Sequence[Strategy], rules: RuleSet) -> RuleAdherence:
    """Was the trade out of the way by 21 DTE?

    DTE at close is measured against the nearest expiration still in the
    strategy, because that is the leg that carries the gamma. A roll keeps the
    same strategy alive, so a rolled trade is judged on where it finally
    closed — which is the right test: rolling at 21 DTE is compliance.
    """
    exit_dte = rules.dte_exit
    bins = _dte_close_bins(exit_dte)
    counts = dict.fromkeys((label for label, _, _ in bins), 0)

    followed = violated = not_applicable = 0
    pnl_followed = pnl_violated = ZERO

    for s in done:
        exps = s.expirations
        if not exps or s.closed_at is None:
            not_applicable += 1  # equity positions have no expiration to be late on
            continue

        dte_at_close = (exps[0] - s.closed_at.date()).days
        for label, low, high in bins:
            if low <= dte_at_close <= high:
                counts[label] += 1
                break

        if dte_at_close >= exit_dte:
            followed += 1
            pnl_followed += s.realized_pnl
        else:
            violated += 1
            pnl_violated += s.realized_pnl

    notes = (
        f"Violated means the trade was still on inside {exit_dte} DTE — it was neither closed "
        "nor rolled at the line. Expiring or being assigned counts as held to zero.",
        f"No counterfactual: pricing the exit requires the strategy's mark on its {exit_dte} DTE "
        f"date, which needs daily snapshots. {violated} violating trade(s) would need it.",
    )

    return RuleAdherence(
        rule="dte_exit",
        description=f"Close or roll at {exit_dte} DTE",
        measurable=True,
        unmeasurable_reason=None,
        trades_considered=len(done),
        not_applicable=not_applicable,
        followed=followed,
        violated=violated,
        adherence_rate=_rate(followed, violated),
        pnl_when_followed=pnl_followed,
        pnl_when_violated=pnl_violated,
        counterfactual_pnl=None,
        counterfactual_actual_pnl=None,
        counterfactual_delta=None,
        counterfactual_trades=0,
        counterfactual_excluded=violated,
        distribution=tuple((label, counts[label]) for label, _, _ in bins),
        notes=notes,
    )


def _stop_rule(
    done: Sequence[Strategy],
    rules: RuleSet,
    mae_by_strategy: Mapping[str, Decimal] | None,
) -> RuleAdherence:
    """Did trades that hit the 2x stop actually get stopped out?

    This is the one rule the trade record cannot answer on its own: a closed
    strategy shows where it ended, never how bad it got. Max adverse excursion
    comes from daily snapshots of open P&L, so without ``mae_by_strategy`` the
    honest answer is "not measurable yet", not a guess.

    ``mae_by_strategy`` maps ``Strategy.id`` to the worst open P&L the trade
    ever printed, in account cash flow terms (so negative). The magnitude is
    what is compared, so a caller that stores drawdowns as positive numbers
    gets the same answer.
    """
    multiple = rules.stop_loss_multiple
    description = f"Stop at {multiple}x the credit received"

    if mae_by_strategy is None:
        return _unmeasurable(
            "stop_loss",
            description,
            "Not measurable yet: judging a stop needs max adverse excursion, which comes from "
            "daily snapshots of open P&L. No snapshots were supplied, and a closed trade's "
            "final P&L cannot show how far underwater it went on the way.",
            len(done),
        )

    low = _ONE - rules.stop_band
    high = _ONE + rules.stop_band

    followed = violated = not_applicable = 0
    pnl_followed = pnl_violated = ZERO
    cf_pnl = cf_actual = ZERO
    cf_trades = cf_excluded = 0
    missing_mae = 0

    for s in done:
        credit = max_profit_at_close(s)
        mae = mae_by_strategy.get(s.id)
        if credit is None or credit <= ZERO or mae is None:
            not_applicable += 1
            cf_excluded += 1
            if mae is None:
                missing_mae += 1
            continue

        stop_at = multiple * credit  # positive magnitude of the allowed loss
        drawdown = abs(mae)
        if drawdown < stop_at:
            # The stop never triggered, so there was nothing to obey. These
            # trades are untouched by the rule and stay out of both the counts
            # and the counterfactual.
            not_applicable += 1
            continue

        loss = -s.realized_pnl  # positive when the trade actually lost money
        cf_trades += 1
        cf_pnl += -stop_at  # mechanically: closed at the stop, no slippage
        cf_actual += s.realized_pnl

        if stop_at * low <= loss <= stop_at * high:
            followed += 1
            pnl_followed += s.realized_pnl
        else:
            # Either it recovered (held through the stop and got bailed out) or
            # it ran well past it. Both are the same sin: the stop was hit and
            # the trade was still on.
            violated += 1
            pnl_violated += s.realized_pnl

    notes = (
        f"Only trades whose worst point reached {multiple}x the credit are judged; "
        f"{not_applicable} trade(s) are not applicable (stop never hit, credit unknown, or no "
        f"MAE recorded — {missing_mae} of those had no MAE).",
        "The counterfactual assumes a fill exactly at the stop with no slippage and no gap "
        "through it, so it flatters the rule on fast moves.",
        "It covers only the trades that breached the stop; trades that never breached are "
        "unaffected by the rule, so total P&L under the rule is today's total plus the delta.",
    )

    return RuleAdherence(
        rule="stop_loss",
        description=description,
        measurable=True,
        unmeasurable_reason=None,
        trades_considered=len(done),
        not_applicable=not_applicable,
        followed=followed,
        violated=violated,
        adherence_rate=_rate(followed, violated),
        pnl_when_followed=pnl_followed,
        pnl_when_violated=pnl_violated,
        counterfactual_pnl=cf_pnl if cf_trades else None,
        counterfactual_actual_pnl=cf_actual if cf_trades else None,
        counterfactual_delta=(cf_pnl - cf_actual) if cf_trades else None,
        counterfactual_trades=cf_trades,
        counterfactual_excluded=cf_excluded,
        notes=notes,
    )


def rule_adherence(
    strategies: Sequence[Strategy],
    rules: RuleSet = DEFAULT_RULES,
    mae_by_strategy: Mapping[str, Decimal] | None = None,
) -> dict[str, RuleAdherence]:
    """Score the closed trades against each management rule.

    Returns one :class:`RuleAdherence` per rule, keyed ``"profit_target"``,
    ``"dte_exit"`` and ``"stop_loss"``. The stop rule reports itself as not
    measurable unless ``mae_by_strategy`` (strategy id -> worst open P&L) is
    supplied, because nothing in a closed trade record reveals how far
    underwater it went.
    """
    done = closed_strategies(strategies)
    return {
        "profit_target": _profit_target_rule(done, rules),
        "dte_exit": _dte_rule(done, rules),
        "stop_loss": _stop_rule(done, rules, mae_by_strategy),
    }
