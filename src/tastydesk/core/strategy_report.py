"""One strategy's track record, told so it can be understood at a glance.

The Performance table answers "which group did best" in nine columns of
numbers. What it cannot answer is the question the user actually asked: *how*
does this strategy make and lose its money? Is the total carried by a few big
wins, or sunk by a few big losses? Does one loss eat five wins? Is it getting
better or worse? This module answers those for one group of closed trades at a
time, and says the answer in plain sentences beside the numbers.

Everything is measured on closed trades only, net of fees, in the order they
closed — the same conventions as :mod:`tastydesk.core.analytics`, so a figure
here always reconciles with the table above it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from tastydesk.core import analytics
from tastydesk.core.analytics import PerformanceStats, closed_strategies, max_profit_at_close
from tastydesk.core.models import ZERO, Strategy
from tastydesk.core.occ import product_root
from tastydesk.core.tom import PLAN, playbook_for

__all__ = ["StrategyReport", "report"]

_ONE = Decimal(1)

# Enough to see a strategy's shape without a hundred-row table.
_SHOWN_EXTREMES = 3
_RECENT = 10
_MAX_BINS = 14


@dataclass(frozen=True, slots=True)
class TradeRow:
    id: str
    product: str
    underlying: str
    structure: str
    opened: date
    closed: date
    days: int
    pnl: Decimal
    credit: Decimal | None
    rolls: int
    dte_at_entry: int | None


@dataclass(frozen=True, slots=True)
class CurvePoint:
    date: date
    pnl: Decimal
    cumulative: Decimal
    id: str


@dataclass(frozen=True, slots=True)
class Month:
    month: str  # YYYY-MM
    pnl: Decimal
    trades: int
    wins: int


@dataclass(frozen=True, slots=True)
class Bin:
    """Trades whose result fell in [low, high).

    The two end buckets can be open: ``open_low`` means the first bucket also
    holds every trade worse than ``low``, ``open_high`` that the last also
    holds every trade better than ``high``. That keeps a handful of outliers
    from stretching the scale until every ordinary trade lands in one bar.
    """

    low: Decimal
    high: Decimal
    trades: int
    pnl: Decimal
    open_low: bool = False
    open_high: bool = False


@dataclass(frozen=True, slots=True)
class Slice:
    key: str
    trades: int
    wins: int
    pnl: Decimal


@dataclass(frozen=True, slots=True)
class Lens:
    """How the strategy's results line up with Tom King's management rules."""

    rolled: int
    rolled_pnl: Decimal
    past_stop: int
    beyond_stop: Decimal
    held_past_target: int
    winners_judged: int
    over_two_pct: int
    over_two_pct_pnl: Decimal


@dataclass(slots=True)
class StrategyReport:
    key: str
    name: str
    product: str | None
    stats: PerformanceStats
    first: date | None
    last: date | None
    months_active: Decimal
    per_month: Decimal | None
    verdict: str  # making money | losing money | about even
    thin: bool
    summary: list[str]
    curve: list[CurvePoint]
    months: list[Month]
    distribution: list[Bin]
    best: list[TradeRow]
    worst: list[TradeRow]
    without_worst: Decimal
    recent_trades: int
    recent_pnl: Decimal
    recent_wins: int
    breakdown_label: str
    breakdown: list[Slice]
    lens: Lens
    trades: list[TradeRow] = field(default_factory=list)


def _money(v: Decimal) -> str:
    sign = "-" if v < 0 else "+"
    return f"{sign}${abs(v):,.0f}"


def _plain(v: Decimal) -> str:
    return f"${abs(v):,.0f}"


def _row(s: Strategy) -> TradeRow:
    assert s.closed_at is not None
    return TradeRow(
        id=s.id,
        product=product_root(s.underlying),
        underlying=s.underlying,
        structure=s.strategy_type.value,
        opened=s.opened_at.date(),
        closed=s.closed_at.date(),
        days=max((s.closed_at - s.opened_at).days, 0),
        pnl=s.realized_pnl,
        credit=max_profit_at_close(s),
        rolls=s.roll_count,
        dte_at_entry=s.dte_at_entry if s.dte_at_entry is not None else s.front_entry_dte,
    )


def _nice_step(span: Decimal) -> Decimal:
    """A round bin width — 50, 100, 250, 500, 1,000 … — that keeps the bins few."""
    if span <= 0:
        return Decimal(100)
    raw = float(span) / _MAX_BINS
    exponent = math.floor(math.log10(raw)) if raw > 0 else 0
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * 10**exponent
        if step >= raw:
            return Decimal(str(step)).normalize()
    return Decimal(str(10 ** (exponent + 1)))


def _flat(value: Decimal) -> Decimal:
    """3000, not 3.0E+3: Decimal keeps the exponent of the arithmetic."""
    return Decimal(format(value, "f"))


def distribution(pnls: Sequence[Decimal]) -> list[Bin]:
    """Trades counted into round-dollar buckets, with zero always on an edge.

    Zero is an edge so no bucket mixes winners with losers: the left of the
    chart is losses and the right is wins, whatever the colour says. With
    twenty or more trades the scale is set by the middle 94% of them and the
    rest fold into the two end buckets, which say so.
    """
    if not pnls:
        return []
    ordered = sorted(pnls)
    n = len(ordered)
    if n >= 20:
        lo, hi = ordered[int(n * 0.03)], ordered[math.ceil(n * 0.97) - 1]
    else:
        lo, hi = ordered[0], ordered[-1]
    # Clipping must never fold a loss into a winners' bucket or the other way
    # round, so the clip stops at the nearest trade on the far side of zero.
    if ordered[0] < ZERO <= lo:
        lo = max(p for p in ordered if p < ZERO)
    if ordered[-1] > ZERO >= hi:
        hi = min(p for p in ordered if p > ZERO)
    step = _nice_step(max(hi, ZERO) - min(lo, ZERO))
    first = math.floor(lo / step)
    last = math.floor(hi / step)
    bins: dict[int, list[Decimal]] = {i: [] for i in range(first, last + 1)}
    for p in ordered:
        bins[min(max(math.floor(p / step), first), last)].append(p)
    return [
        Bin(
            low=_flat(step * i),
            high=_flat(step * (i + 1)),
            trades=len(v),
            pnl=sum(v, ZERO),
            open_low=i == first and ordered[0] < step * first,
            open_high=i == last and ordered[-1] >= step * (last + 1),
        )
        for i, v in sorted(bins.items())
    ]


def _lens(
    trades: Sequence[Strategy],
    net_liq_on: Callable[[date], Decimal | None],
) -> Lens:
    rolled = [s for s in trades if s.roll_count > 0 and playbook_for(s).key not in ("pmcc", "covered")]
    past = 0
    beyond = ZERO
    held = judged = 0
    big = 0
    big_pnl = ZERO
    for s in trades:
        book = playbook_for(s)
        credit = max_profit_at_close(s)
        if book.stop_multiple is not None and credit and credit > ZERO and s.realized_pnl < ZERO:
            allowed = (book.stop_multiple - _ONE) * credit
            if -s.realized_pnl > allowed * Decimal("1.1"):
                past += 1
                beyond += -s.realized_pnl - allowed
        if book.profit_target is not None and credit and credit > ZERO and s.realized_pnl > ZERO:
            judged += 1
            if s.realized_pnl / credit > book.profit_target + Decimal("0.15"):
                held += 1
        nlv = net_liq_on(s.opened_at.date())
        if nlv and -s.realized_pnl > PLAN.max_loss * nlv:
            big += 1
            big_pnl += s.realized_pnl
    return Lens(
        rolled=len(rolled),
        rolled_pnl=sum((s.realized_pnl for s in rolled), ZERO),
        past_stop=past,
        beyond_stop=beyond,
        held_past_target=held,
        winners_judged=judged,
        over_two_pct=big,
        over_two_pct_pnl=big_pnl,
    )


def _summary(
    stats: PerformanceStats,
    rows: Sequence[TradeRow],
    *,
    first: date,
    last: date,
    per_month: Decimal | None,
    worst: Sequence[TradeRow],
    without_worst: Decimal,
    recent: Sequence[TradeRow],
    lens: Lens,
) -> list[str]:
    """The record in four or five short sentences, the biggest point first."""
    out: list[str] = []
    span = f"from {first:%b %d, %Y} to {last:%b %d, %Y}" if first != last else f"on {first:%b %d, %Y}"
    sentence = (
        f"{stats.trades} trade{'s' if stats.trades != 1 else ''} {span} made "
        f"{_money(stats.total_pnl)} after fees — {_money(stats.expectancy)} a trade on average"
    )
    if per_month is not None and stats.trades >= 3:
        sentence += f", or {_money(per_month)} a month"
    out.append(sentence + ".")

    if stats.wins and stats.losses:
        ratio = stats.avg_loss / stats.avg_win if stats.avg_win else None
        balance = (
            f"It wins {stats.win_rate * 100:.0f}% of the time. The average win is {_plain(stats.avg_win)} "
            f"and the average loss is {_plain(stats.avg_loss)}"
        )
        if ratio is not None and ratio >= Decimal("1.2"):
            balance += f" — {ratio:.1f}× bigger, so one loss takes about {math.ceil(ratio)} wins to pay back."
        elif ratio is not None and ratio <= Decimal("0.8"):
            balance += ", smaller than the wins, so it does not need a high win rate to work."
        else:
            balance += ", about the same size as the wins."
        out.append(balance)
    elif stats.wins and not stats.losses:
        out.append("Every trade so far has been a winner.")
    elif stats.losses and not stats.wins:
        out.append("Every trade so far has been a loser.")

    if len(rows) >= 6 and worst:
        worst_total = sum((r.pnl for r in worst), ZERO)
        gross_win = sum((r.pnl for r in rows if r.pnl > ZERO), ZERO)
        if worst_total < ZERO and (
            -worst_total > gross_win * Decimal("0.3") or (stats.total_pnl < 0 < without_worst)
        ):
            out.append(
                f"The {len(worst)} worst trades lost {_plain(worst_total)}. Without them it would be "
                f"{_money(without_worst)} — the big losses decide this strategy."
            )

    if len(rows) >= _RECENT * 2:
        recent_pnl = sum((r.pnl for r in recent), ZERO)
        wins = sum(1 for r in recent if r.pnl > ZERO)
        out.append(f"The last {len(recent)} trades made {_money(recent_pnl)}, with {wins} winners.")

    habits = []
    if lens.past_stop:
        habits.append(
            f"{lens.past_stop} loss{'es' if lens.past_stop != 1 else ''} ran past Tom's stop "
            f"({_plain(lens.beyond_stop)} beyond it)"
        )
    if lens.rolled:
        habits.append(
            f"{lens.rolled} trade{'s were' if lens.rolled != 1 else ' was'} rolled "
            f"({_money(lens.rolled_pnl)})"
        )
    if habits:
        joined = " and ".join(habits)
        out.append(joined[0].upper() + joined[1:] + ".")
    return out


def report(
    key: str,
    name: str,
    product: str | None,
    strategies: Sequence[Strategy],
    *,
    breakdown_by: str,
    today: date,
    net_liq_on: Callable[[date], Decimal | None] = lambda _d: None,
) -> StrategyReport:
    """Everything about how one group of trades performed. ``breakdown_by`` is
    ``"product"`` or ``"structure"`` — whichever the group is not already."""
    done = closed_strategies(strategies)
    stats = analytics.performance(done)
    rows = [_row(s) for s in done]

    cumulative = ZERO
    curve: list[CurvePoint] = []
    for r in rows:
        cumulative += r.pnl
        curve.append(CurvePoint(date=r.closed, pnl=r.pnl, cumulative=cumulative, id=r.id))

    months: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        months[f"{r.closed:%Y-%m}"].append(r)
    month_rows = [
        Month(
            month=m, pnl=sum((r.pnl for r in v), ZERO), trades=len(v), wins=sum(1 for r in v if r.pnl > ZERO)
        )
        for m, v in sorted(months.items())
    ]

    by_pnl = sorted(rows, key=lambda r: r.pnl)
    worst = [r for r in by_pnl[:_SHOWN_EXTREMES] if r.pnl < ZERO]
    best = [r for r in reversed(by_pnl[-_SHOWN_EXTREMES:]) if r.pnl > ZERO]
    without_worst = stats.total_pnl - sum((r.pnl for r in worst), ZERO)
    recent = rows[-_RECENT:]

    slices: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        slices[r.product if breakdown_by == "product" else r.structure].append(r)
    breakdown = sorted(
        (
            Slice(
                key=k,
                trades=len(v),
                wins=sum(1 for r in v if r.pnl > ZERO),
                pnl=sum((r.pnl for r in v), ZERO),
            )
            for k, v in slices.items()
        ),
        key=lambda s: -s.trades,
    )

    lens = _lens(done, net_liq_on)
    first = min((r.opened for r in rows), default=None)
    last = rows[-1].closed if rows else None
    months_active = (
        max(Decimal((last - first).days) / Decimal("30.4375"), _ONE) if first and last else Decimal(0)
    )
    per_month = stats.total_pnl / months_active if months_active else None

    if stats.trades == 0:
        verdict = "no closed trades"
    elif abs(stats.total_pnl) < max(Decimal(50), Decimal(stats.trades) * 5):
        verdict = "about even"
    else:
        verdict = "making money" if stats.total_pnl > 0 else "losing money"

    summary = (
        _summary(
            stats,
            rows,
            first=first,
            last=last,
            per_month=per_month,
            worst=worst,
            without_worst=without_worst,
            recent=recent,
            lens=lens,
        )
        if rows and first and last
        else []
    )

    return StrategyReport(
        key=key,
        name=name,
        product=product,
        stats=stats,
        first=first,
        last=last,
        months_active=months_active,
        per_month=per_month,
        verdict=verdict,
        thin=stats.trades < 20,
        summary=summary,
        curve=curve,
        months=month_rows,
        distribution=distribution([r.pnl for r in rows]),
        best=best,
        worst=worst,
        without_worst=without_worst,
        recent_trades=len(recent),
        recent_pnl=sum((r.pnl for r in recent), ZERO),
        recent_wins=sum(1 for r in recent if r.pnl > ZERO),
        breakdown_label="By product" if breakdown_by == "product" else "By structure",
        breakdown=breakdown,
        lens=lens,
        trades=list(reversed(rows)),
    )


def group(strategies: Sequence[Strategy], how: str) -> Mapping[str, list[Strategy]]:
    """Closed trades by structure or by product."""
    out: dict[str, list[Strategy]] = defaultdict(list)
    for s in closed_strategies(strategies):
        out[s.strategy_type.value if how == "structure" else product_root(s.underlying)].append(s)
    return out
