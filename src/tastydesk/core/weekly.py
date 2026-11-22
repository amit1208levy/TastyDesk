"""The week in review: what the account did, and whether it moved toward the plan.

A trading week is Monday to Friday, measured from the close before it. On a
Saturday or a Sunday the week is the one that just ended. Everything here is
arithmetic on figures the app already keeps; nothing is estimated, and a figure
that was not recorded is said to be missing rather than filled in.

The plan's targets are Tom King's, as the user set them (``tom.PLAN``):

- theta at least 0.3% of net liq a day,
- vega no more than 1.5x theta,
- beta-weighted delta within 0.2% of net liq,
- buying power used 40-50% of net liq,
- each share of the allowed buying power (11x, PMCC, spec, hedge) under its limit,
- and profit at Tom's 2-3% a month, about 0.5% a week.

A week is *correcting* when more of these moved toward the plan than away.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from tastydesk.core.models import ZERO
from tastydesk.core.tom import PLAN

#: Tom's 2% a month, spread over a month's weeks: the least a week should add.
WEEKLY_PACE = Decimal("0.02") * Decimal(12) / Decimal(52)

#: Moves smaller than these are "the same": noise, not a change of course.
_SAME = {
    "theta": Decimal("0.0001"),
    "vega": Decimal("0.05"),
    "delta": Decimal("0.0001"),
    "bp": Decimal("0.005"),
    "allocation": Decimal("0.005"),
}

#: A reading older than this before the week began does not stand for its start.
_STALE_DAYS = 10


@dataclass(frozen=True, slots=True)
class Week:
    monday: date
    friday: date

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def label(self) -> str:
        if self.monday.month == self.friday.month:
            return f"{self.monday.day}–{self.friday.day} {self.friday:%b %Y}"
        return f"{self.monday.day} {self.monday:%b} – {self.friday.day} {self.friday:%b %Y}"

    def holds(self, day: date) -> bool:
        return self.monday <= day <= self.sunday


def week_of(day: date) -> Week:
    """The Monday-to-Friday week a day belongs to; a weekend belongs to the week before it."""
    monday = day - timedelta(days=day.weekday())
    return Week(monday, monday + timedelta(days=4))


def snapshot_at(rows: Sequence[Mapping[str, object]], day: date) -> Mapping[str, object] | None:
    """The latest row recorded on or before ``day`` (rows carry a ``day``)."""
    found = None
    for row in rows:
        when = row["day"]
        assert isinstance(when, date)
        if when <= day and (found is None or when >= found["day"]):  # type: ignore[operator]
            found = row
    return found


def week_snapshots(
    rows: Sequence[Mapping[str, object]], week: Week
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None]:
    """The book as the week found it and as it left it.

    The start is the last reading before Monday, if it is recent enough to
    stand for it; the end is the last reading inside the week. Either can be
    missing: a reading is only written on a day the app ran.
    """
    start = snapshot_at(rows, week.monday - timedelta(days=1))
    if start is not None and (week.monday - start["day"]).days > _STALE_DAYS:  # type: ignore[operator]
        start = None
    end = snapshot_at(rows, week.sunday)
    if end is not None and not week.holds(end["day"]):  # type: ignore[arg-type]
        end = None
    return start, end


# ---------------------------------------------------------------- the account


@dataclass(frozen=True, slots=True)
class WeekChange:
    """The account over one week, from the close before it to its last close."""

    start_day: date
    end_day: date
    gain: Decimal
    growth: Decimal
    spy: Decimal | None
    deposits: Decimal
    net_liq_start: Decimal
    net_liq_end: Decimal


def week_change(days: Sequence[object], week: Week) -> WeekChange | None:
    """Slice one week out of an account curve (``analytics.CurveDay`` rows).

    The curve's gain and growth run from its own first day, so a week is the
    step from the last close before Monday to the last close inside the week.
    """
    # Closes only: the broker also writes a row on Saturday and Sunday, which
    # is Friday's close re-marked, not a session of its own.
    closes = [d for d in days if d.day.weekday() < 5]  # type: ignore[attr-defined]
    before = [d for d in closes if d.day < week.monday]  # type: ignore[attr-defined]
    inside = [d for d in closes if week.holds(d.day)]  # type: ignore[attr-defined]
    if not before or not inside:
        return None
    a, b = before[-1], inside[-1]
    one = Decimal(1)
    spy = None
    if a.spy is not None and b.spy is not None:  # type: ignore[attr-defined]
        spy = (one + b.spy) / (one + a.spy) - one  # type: ignore[attr-defined]
    return WeekChange(
        start_day=a.day,  # type: ignore[attr-defined]
        end_day=b.day,  # type: ignore[attr-defined]
        gain=b.gain - a.gain,  # type: ignore[attr-defined]
        growth=(one + b.growth) / (one + a.growth) - one,  # type: ignore[attr-defined]
        spy=spy,
        deposits=sum((d.flow for d in inside), ZERO),  # type: ignore[attr-defined]
        net_liq_start=a.net_liq,  # type: ignore[attr-defined]
        net_liq_end=b.net_liq,  # type: ignore[attr-defined]
    )


@dataclass(frozen=True, slots=True)
class BucketRow:
    bucket: str
    capital: Decimal
    share: Decimal | None  # of net liq
    limit: Decimal  # of net liq
    over: Decimal | None  # of net liq above the limit


BUCKETS = ("11x", "PMCC", "Spec", "Hedge", "Outside")


def allocation(
    capital: Mapping[str, Decimal], net_liq: Decimal | None, *, excused: Decimal = ZERO
) -> tuple[list[BucketRow], Decimal | None]:
    """Each share of the plan against its limit, and how far they ran over in all.

    Limits are the plan's shares of the buying power Tom allows himself (half
    of net liq). Trades outside the plan have no share; those the user keeps on
    purpose (``excused``) are not counted against it.
    """
    limits = {k: share * PLAN.max_allowed_bp for k, share in PLAN.allocation}
    rows: list[BucketRow] = []
    total: Decimal | None = ZERO if net_liq else None
    for k in BUCKETS:
        used = capital.get(k, ZERO)
        counted = max(used - excused, ZERO) if k == "Outside" else used
        share = counted / net_liq if net_liq else None
        limit = limits.get(k, ZERO)
        over = None if share is None else max(share - limit, ZERO)
        if over is not None and total is not None:
            total += over
        if used or k in limits:
            rows.append(BucketRow(k, used, None if not net_liq else used / net_liq, limit, over))
    return rows, total


# ---------------------------------------------------------------- scorecard


@dataclass(frozen=True, slots=True)
class Dimension:
    """One part of the plan, as the week found it and left it."""

    key: str
    label: str
    target: str
    start: Decimal | None
    end: Decimal | None
    start_text: str
    end_text: str
    # closer | further | same | on_target | met | missed | not_recorded
    direction: str
    note: str = ""


def _pct(v: Decimal | None, places: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{places}f}%"


def _share(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or not whole:
        return None
    return part / whole


def _move(key: str, start_gap: Decimal | None, end_gap: Decimal | None) -> str:
    """Which way a distance from the plan went (0 is on the plan)."""
    if start_gap is None or end_gap is None:
        return "not_recorded"
    if start_gap == ZERO and end_gap == ZERO:
        return "on_target"
    step = _SAME[key]
    if end_gap < start_gap - step:
        return "closer"
    if end_gap > start_gap + step:
        return "further"
    return "same"


def _theta(row: Mapping[str, object] | None) -> Decimal | None:
    if row is None:
        return None
    return _share(row.get("theta"), row.get("net_liq"))  # type: ignore[arg-type]


def _vega_ratio(row: Mapping[str, object] | None) -> Decimal | None:
    if row is None:
        return None
    vega, theta = row.get("vega"), row.get("theta")
    if vega is None or theta is None or theta <= ZERO:  # type: ignore[operator]
        return None
    return abs(vega) / theta  # type: ignore[arg-type,operator]


def _delta(row: Mapping[str, object] | None) -> Decimal | None:
    """Beta-weighted delta as a share of what the plan allows (1.0 is the limit)."""
    if row is None:
        return None
    delta, nlv = row.get("delta"), row.get("net_liq")
    if delta is None or not nlv:
        return None
    return abs(delta) / (PLAN.delta_share * nlv)  # type: ignore[arg-type,operator]


def _bp(row: Mapping[str, object] | None) -> Decimal | None:
    if row is None:
        return None
    return _share(row.get("bp_used"), row.get("net_liq"))  # type: ignore[arg-type]


def scorecard(
    start: Mapping[str, object] | None,
    end: Mapping[str, object] | None,
    *,
    overage_start: Decimal | None,
    overage_end: Decimal | None,
    week_return: Decimal | None,
) -> list[Dimension]:
    """Each part of the plan: where the week began, where it ended, and which way it went.

    ``overage_*`` is how far the shares of buying power ran over their limits,
    summed, as a share of net liq. ``week_return`` is the account's
    time-weighted return over the week, deposits taken out.
    """
    lo, _hi = PLAN.theta_target
    t0, t1 = _theta(start), _theta(end)
    v0, v1 = _vega_ratio(start), _vega_ratio(end)
    d0, d1 = _delta(start), _delta(end)
    b0, b1 = _bp(start), _bp(end)
    bp_lo, bp_hi = PLAN.bp_target

    def theta_gap(v: Decimal | None) -> Decimal | None:
        return None if v is None else max(lo - v, ZERO)

    def vega_gap(v: Decimal | None) -> Decimal | None:
        return None if v is None else max(v - PLAN.vega_to_theta, ZERO)

    def delta_gap(v: Decimal | None) -> Decimal | None:
        return None if v is None else max(v - 1, ZERO)

    def bp_gap(v: Decimal | None) -> Decimal | None:
        return None if v is None else max(bp_lo - v, ZERO) + max(v - bp_hi, ZERO)

    def times(v: Decimal | None) -> str:
        return "—" if v is None else f"{v:.1f}×"

    def deltas(row: Mapping[str, object] | None) -> str:
        if row is None or row.get("delta") is None:
            return "—"
        return f"{row['delta']:+.0f}"

    rows = [
        Dimension(
            "theta",
            "Theta",
            f"at least {_pct(lo)} of net liq a day",
            t0,
            t1,
            _pct(t0, 2),
            _pct(t1, 2),
            _move("theta", theta_gap(t0), theta_gap(t1)),
        ),
        Dimension(
            "vega",
            "Vega against theta",
            f"no more than {PLAN.vega_to_theta}×",
            v0,
            v1,
            times(v0),
            times(v1),
            _move("vega", vega_gap(v0), vega_gap(v1)),
        ),
        Dimension(
            "delta",
            "Beta-weighted delta",
            f"within ±{_pct(PLAN.delta_share)} of net liq",
            d0,
            d1,
            deltas(start),
            deltas(end),
            _move("delta", delta_gap(d0), delta_gap(d1)),
        ),
        Dimension(
            "bp",
            "Buying power used",
            f"{_pct(bp_lo, 0)}–{_pct(bp_hi, 0)} of net liq",
            b0,
            b1,
            _pct(b0, 0),
            _pct(b1, 0),
            _move("bp", bp_gap(b0), bp_gap(b1)),
        ),
        Dimension(
            "allocation",
            "Allocation",
            "each share under its limit",
            overage_start,
            overage_end,
            _over_text(overage_start),
            _over_text(overage_end),
            _move("allocation", overage_start, overage_end),
            "of net liq above the limits, summed",
        ),
    ]
    rows.append(
        Dimension(
            "profit",
            "Profit",
            f"Tom's 2–3% a month, about {_pct(WEEKLY_PACE)} a week",
            None,
            week_return,
            "",
            _pct(week_return, 2),
            "not_recorded" if week_return is None else ("met" if week_return >= WEEKLY_PACE else "missed"),
        )
    )
    return rows


def _over_text(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return "all inside" if v == ZERO else f"{_pct(v)} over"


TOWARD = frozenset({"closer", "met", "on_target"})
AWAY = frozenset({"further", "missed"})


@dataclass(frozen=True, slots=True)
class Verdict:
    label: str  # Correcting | Mixed | Not constructive | Not enough data
    tone: str  # good | mixed | bad | none
    toward: int
    away: int
    measured: int
    text: str
    greeks_recorded: bool = True
    notes: list[str] = field(default_factory=list)


def verdict(rows: Sequence[Dimension]) -> Verdict:
    measured = [r for r in rows if r.direction != "not_recorded"]
    toward = sum(1 for r in measured if r.direction in TOWARD)
    away = sum(1 for r in measured if r.direction in AWAY)
    greeks = any(r.direction != "not_recorded" for r in rows if r.key != "profit")
    notes = [] if greeks else ["Greeks and buying power were not recorded yet that week."]
    if not measured:
        nothing = "Nothing about this week was recorded."
        return Verdict("Not enough data", "none", 0, 0, 0, nothing, greeks, notes)
    text = f"{toward} of {len(measured)} moved toward the plan"
    if toward > away:
        return Verdict("Correcting", "good", toward, away, len(measured), text, greeks, notes)
    if away > toward:
        return Verdict("Not constructive", "bad", toward, away, len(measured), text, greeks, notes)
    return Verdict("Mixed", "mixed", toward, away, len(measured), text, greeks, notes)


# ---------------------------------------------------------------- takeaways


def _money(v: Decimal) -> str:
    return f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"


def takeaways(
    rows: Sequence[Dimension],
    *,
    gain: Decimal | None,
    week_return: Decimal | None,
    spy_return: Decimal | None,
    closed: int,
    banked: Decimal,
    big_losses: Sequence[str],
    losses: int,
    broken_rules: Sequence[tuple[str, int]],
) -> list[str]:
    """Up to five plain sentences, every one of them a figure from this week."""
    out: list[str] = []
    if gain is not None and week_return is not None:
        made = "made" if gain >= 0 else "lost"
        line = f"The account {made} ${abs(gain):,.0f} ({_pct(week_return, 2)})"
        if spy_return is not None:
            line += f" while SPY moved {_pct(spy_return, 2)}"
        out.append(line + ".")
    if closed:
        out.append(
            f"{closed} trade{'s' if closed != 1 else ''} closed for {_money(banked)} banked."
        )
    by_key = {r.key: r for r in rows}
    labels = {
        "theta": "Theta",
        "vega": "Vega against theta",
        "delta": "Delta",
        "bp": "Buying power used",
        "allocation": "Allocation",
    }
    for want, word in (("closer", "moved toward"), ("further", "moved away from")):
        for key in ("theta", "allocation", "bp", "vega", "delta"):
            r = by_key.get(key)
            if r is not None and r.direction == want:
                out.append(f"{labels[key]} {word} the plan: {r.start_text} → {r.end_text} ({r.target}).")
                break
    if big_losses:
        out.append(
            f"{len(big_losses)} trade{'s' if len(big_losses) != 1 else ''} lost more than 2% of net liq: "
            f"{', '.join(big_losses)}."
        )
    elif losses:
        out.append("Every loss this week stayed under 2% of net liq.")
    if broken_rules:
        title, count = max(broken_rules, key=lambda kv: kv[1])
        out.append(f"The rule broken most: {title} ({count} trade{'s' if count != 1 else ''}).")
    return out[:6]
