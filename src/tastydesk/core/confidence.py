"""How sure the app is that an old trade belongs to a strategy you named.

Why this exists
---------------
The user cannot go through years of fills and label each one by hand, and said
so. But he also cannot have trades guessed into a strategy's history, because a
history with one wrong trade in it produces a win rate he would act on and
should not. So the app guesses — and publishes how sure it is, with the reasons
and the gaps, and shows nothing below a threshold he controls.

What the evidence is
--------------------
Everything here is read off the fills. Nothing is inferred from a price the app
did not record at the time.

============  ======  ====================================================
Dimension     Weight  What it compares
============  ======  ====================================================
legs          0.35    rights, sides and ratios — is it the same structure
expiries      0.12    one expiry or several, the same way
entry window  0.05    legs filled together rather than a week apart
size          0.10    contracts, against the sizes already in the strategy
DTE at entry  0.15    days to expiry when it was opened
strike spacing 0.15   width between strikes as a fraction of strike level
entry delta   0.08    short-strike delta, when it was recorded on both
============  ======  ====================================================

Strike spacing is the honest stand-in for "what delta do I sell". The app does
not hold the underlying's price on the day a 2024 trade was opened, so the true
delta of that trade cannot be recovered; what it can see is that a 1300/1200
soybean strangle and a 1350/1250 one are both about 8% wide, and that a 5%-wide
one is a different trade. Where a structure has a single strike there is nothing
to measure against and the dimension is reported as unknown rather than filled
in with a number that would look like evidence.

The comparison is against the trades already in the strategy — the user's own
examples — not against an ideal. That is what "other trades made under the same
ticker" means in practice: the strategy learns its own typical size, tenor and
spacing from the trades he put in it.

How the number is produced
--------------------------
Each checkable dimension scores 0 to 1. The confidence is their weighted mean
over the dimensions that could actually be checked, so a missing dimension does
not quietly count as agreement. Then:

* every dimension that could not be checked costs ``UNKNOWN_PENALTY``, because
  unexamined evidence is not the same as confirmed evidence;
* a name that names a structure the trade is not caps the result at
  ``NAME_CONFLICT_CAP``. The name is never used to *raise* confidence — the user
  was explicit that a name leaves things out — but a name that contradicts the
  fills is a reason to stop and look;
* a trade that opened just as a member closed, in the same product, is reported
  as a likely roll. It is evidence of continuation, and it is shown, but it is
  not allowed to carry a match on its own.

Nothing here decides anything. It produces a number, the reasons behind it, and
what could not be checked; the threshold is the user's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from statistics import median

from tastydesk.core.models import Direction, Strategy
from tastydesk.core.occ import product_root

__all__ = [
    "DEFAULT_THRESHOLD",
    "UNKNOWN_PENALTY",
    "NAME_CONFLICT_CAP",
    "Assessment",
    "TradeProfile",
    "profile_of",
    "assess",
]

# What the user asked for: below this, a trade is not in the history.
DEFAULT_THRESHOLD = 0.97

# What one unexaminable dimension costs. Small, because most of the evidence is
# in the fills; real, because "not checked" must never read as "checked and fine".
UNKNOWN_PENALTY = 0.02

# A name that contradicts the structure cannot be waved through on shape alone.
NAME_CONFLICT_CAP = 0.90

# Sizes, tenors and spacings this close count as the same choice.
SIZE_TOLERANCE = Decimal("1")  # one contract either way of the sizes already used
DTE_TOLERANCE = 15  # days
SPACING_TOLERANCE = Decimal("0.30")  # of the usual width
DELTA_TOLERANCE = Decimal("0.07")

# How close a close and an open have to be to look like one continued position.
ROLL_WINDOW = timedelta(days=3)

_WEIGHTS = {
    "legs": 0.35,
    "expiries": 0.12,
    "window": 0.05,
    "size": 0.10,
    "dte": 0.15,
    "spacing": 0.15,
    "delta": 0.08,
}

# Structure words in a name, and the strategy types that would contradict them.
# A word that is not here is not evidence either way: most of the user's names
# are shorthand and say nothing about structure at all.
_NAME_STRUCTURES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("strangle", r"\bstrangle\b", frozenset({"Short Strangle", "Long Strangle"})),
    ("straddle", r"\bstraddle\b", frozenset({"Short Straddle", "Long Straddle"})),
    ("condor", r"\bcondor\b", frozenset({"Iron Condor"})),
    ("butterfly", r"\bbutterfly\b|\bfly\b", frozenset({"Iron Fly", "Butterfly"})),
    ("calendar", r"\bcalendar\b", frozenset({"Calendar"})),
    ("diagonal", r"\bdiagonal\b", frozenset({"Diagonal"})),
)


@dataclass(frozen=True, slots=True)
class TradeProfile:
    """The measurable facts of one trade, as this module compares them."""

    trade_id: str
    product: str
    structure: str  # the classifier's read of the whole thing
    legs: tuple[tuple[str, str, int], ...]  # (right, side, ratio)
    option_legs: int
    expiry_pattern: str
    window_minutes: int
    contracts: Decimal
    dte_at_entry: int | None
    strike_spacing: Decimal | None
    short_delta: Decimal | None


@dataclass(slots=True)
class Assessment:
    """A confidence, and everything behind it."""

    trade_id: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    # Evidence that exists but could not be read. These cost confidence.
    unknowns: list[str] = field(default_factory=list)
    # Evidence that does not exist for this shape at all — a naked put has no
    # strike spacing to compare. Not knowing it is not a gap, so it is free.
    not_applicable: list[str] = field(default_factory=list)
    roll_of: str | None = None

    @property
    def verdict(self) -> str:
        if self.confidence >= DEFAULT_THRESHOLD:
            return "confident"
        if self.confidence >= 0.80:
            return "likely"
        return "unsure"


def _ratios(quantities: list[Decimal]) -> list[int]:
    ints = [int(q) for q in quantities if q == int(q)]
    if len(ints) != len(quantities) or not ints or 0 in ints:
        return [1] * len(quantities)
    smallest = min(ints)
    return [max(1, round(v / smallest)) for v in ints]


def profile_of(trade: Strategy) -> TradeProfile:
    """Reduce a trade to what can be compared across years."""
    option_legs = [leg for leg in trade.legs if leg.option_type is not None]
    quantities = [leg.quantity for leg in trade.legs]
    ratios = _ratios(quantities)

    legs = tuple(
        sorted(
            (
                leg.option_type.value if leg.option_type else "S",
                "short" if leg.direction is Direction.SHORT else "long",
                ratio,
            )
            for leg, ratio in zip(trade.legs, ratios, strict=True)
        )
    )

    expirations = sorted({leg.expiration for leg in option_legs if leg.expiration})
    pattern = "single" if len(expirations) <= 1 else "split"

    # DTE at entry is recomputed rather than read off the record: the stored
    # field is only ever filled for positions opened in the last few days, and
    # the whole job here is judging trades from years ago.
    dte = (expirations[0] - trade.opened_at.date()).days if expirations else None

    strikes = sorted({leg.strike for leg in option_legs if leg.strike is not None})
    spacing: Decimal | None = None
    if len(strikes) >= 2:
        level = (strikes[0] + strikes[-1]) / 2
        if level > 0:
            spacing = (strikes[-1] - strikes[0]) / level

    deltas = [abs(leg.delta) for leg in trade.legs if leg.direction is Direction.SHORT and leg.delta]
    short_delta = trade.short_delta_at_entry
    if short_delta is None and deltas:
        short_delta = max(deltas)

    contracts = max(quantities) if quantities else Decimal(0)

    # The legs of one trade can be filled over a few seconds; the window that
    # matters is the one the grouping already settled, so anything inside one
    # trade counts as together.
    return TradeProfile(
        trade_id=trade.id,
        product=product_root(trade.underlying),
        structure=trade.strategy_type.value,
        legs=legs,
        option_legs=len(option_legs),
        expiry_pattern=pattern,
        window_minutes=0,
        contracts=contracts,
        dte_at_entry=dte,
        strike_spacing=spacing,
        short_delta=short_delta,
    )


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(str(median(float(v) for v in values)))


def _in_range(value: Decimal, seen: list[Decimal], tolerance: Decimal) -> float:
    """How normal ``value`` is for someone whose past choices were ``seen``.

    The standard is the range the user actually traded, not a point estimate.
    He sold /CL strangles at 68, 73, 82, 94 and 105 days to expiry; measuring a
    new one against the median of 82 and a fixed window would call 94 days
    unusual, when in fact it is squarely inside his own habit. So anything
    between the smallest and largest he has done scores full marks, and the
    score falls away outside that band. With a single example there is no band,
    and it degrades to a plain tolerance around that one trade.
    """
    if not seen:
        return 1.0
    low, high = min(seen), max(seen)
    if low <= value <= high:
        return 1.0
    gap = (low - value) if value < low else (value - high)
    # Outside the band, allow the wider of the fixed tolerance and half the
    # band's own width before the score starts falling.
    margin = max(tolerance, (high - low) / 2)
    if margin <= 0:
        return 0.0
    if gap <= margin:
        return 1.0
    reach = margin * 2
    if gap >= reach:
        return 0.0
    return float(1 - (gap - margin) / (reach - margin))


def _name_conflict(name: str, structure: str) -> str | None:
    """A name that names a structure the trade is not. Never the reverse."""
    lowered = (name or "").lower()
    for label, pattern, allowed in _NAME_STRUCTURES:
        if re.search(pattern, lowered) and structure not in allowed:
            return f"you called this a {label} and the trade is a {structure.lower()}"
    return None


def assess(
    candidate: Strategy,
    members: list[Strategy],
    *,
    name: str = "",
) -> Assessment:
    """How sure we are that ``candidate`` belongs with ``members``.

    ``members`` are the trades the user has already put in the strategy. They
    are the standard: an empty list means there is nothing to compare against
    and nothing can be claimed.
    """
    result = Assessment(trade_id=candidate.id, confidence=0.0)

    if not members:
        result.unknowns.append("the strategy has no trades to compare against yet")
        return result

    theirs = [profile_of(m) for m in members]
    mine = profile_of(candidate)

    if any(p.product != mine.product for p in theirs):
        # Members of one strategy are always one product, so this only fires on
        # a candidate from elsewhere.
        result.misses.append(f"different product ({mine.product})")
        return result

    scored: dict[str, float] = {}

    # --- legs -------------------------------------------------------------
    # Exact leg agreement is the strongest evidence, but it is not the only
    # form of it. A strangle rolled four times inside one trade record carries
    # ten legs and will never equal a two-leg shape, while still being the same
    # strategy — which is why the classifier's read of the whole structure is
    # accepted as the next-best agreement rather than scored as a mismatch.
    shapes = {p.legs for p in theirs}
    structures = {p.structure for p in theirs}
    if mine.legs in shapes:
        scored["legs"] = 1.0
        result.reasons.append("same legs, same ratio")
    elif mine.structure in structures:
        scored["legs"] = 0.85
        typical_legs = int(_median([Decimal(p.option_legs) for p in theirs]) or 0)
        if mine.option_legs > typical_legs:
            result.reasons.append(
                f"same structure ({mine.structure.lower()}), carrying "
                f"{mine.option_legs} legs after adjustments"
            )
        else:
            result.reasons.append(f"same structure ({mine.structure.lower()})")
    else:
        sides = {(r, s) for r, s, _ in mine.legs}
        theirs_sides = {frozenset((r, s) for r, s, _ in p.legs) for p in theirs}
        if frozenset(sides) in theirs_sides and any(len(p.legs) == len(mine.legs) for p in theirs):
            scored["legs"] = 0.6
            result.misses.append("same legs in a different ratio")
        else:
            scored["legs"] = 0.0
            result.misses.append(f"a different structure ({mine.structure.lower()})")

    # --- expiry pattern ---------------------------------------------------
    patterns = {p.expiry_pattern for p in theirs}
    if mine.expiry_pattern in patterns:
        scored["expiries"] = 1.0
        result.reasons.append(
            "all one expiry" if mine.expiry_pattern == "single" else "expiries split the same way"
        )
    else:
        scored["expiries"] = 0.0
        result.misses.append(f"expiries {mine.expiry_pattern}, not {'/'.join(sorted(patterns))}")

    # --- entry window -----------------------------------------------------
    scored["window"] = 1.0
    result.reasons.append("filled as one order")

    # --- size -------------------------------------------------------------
    sizes = [p.contracts for p in theirs if p.contracts > 0]
    if not sizes:
        result.unknowns.append("no size to compare against")
    else:
        scored["size"] = _in_range(mine.contracts, sizes, SIZE_TOLERANCE)
        if scored["size"] >= 0.99:
            result.reasons.append(f"size {mine.contracts:g}, in line with this strategy")
        else:
            result.misses.append(
                f"size {mine.contracts:g} against your {min(sizes):g}-{max(sizes):g}"
            )

    # --- days to expiry at entry -----------------------------------------
    dtes = [Decimal(p.dte_at_entry) for p in theirs if p.dte_at_entry is not None]
    if not dtes or mine.dte_at_entry is None:
        result.unknowns.append("no days-to-expiry to compare")
    else:
        scored["dte"] = _in_range(Decimal(mine.dte_at_entry), dtes, Decimal(DTE_TOLERANCE))
        if scored["dte"] >= 0.99:
            result.reasons.append(f"opened at {mine.dte_at_entry} DTE, your usual tenor")
        else:
            result.misses.append(
                f"opened at {mine.dte_at_entry} DTE against your "
                f"{int(min(dtes))}-{int(max(dtes))}"
            )

    # --- strike spacing, the stand-in for delta selection -----------------
    spacings = [p.strike_spacing for p in theirs if p.strike_spacing is not None]
    typical_spacing = _median(spacings)
    typical_legs = int(_median([Decimal(p.option_legs) for p in theirs]) or 0)
    if mine.option_legs > max(typical_legs, 2):
        # Strikes added by rolls widen the outer pair without saying anything
        # about the delta originally sold. Better to admit the measurement does
        # not survive an adjusted trade than to compare a distorted number.
        result.unknowns.append(
            f"adjusted to {mine.option_legs} legs — strike spacing no longer reads as delta"
        )
    elif mine.strike_spacing is None or typical_spacing is None:
        result.not_applicable.append("one strike — nothing to space it against")
    else:
        tolerance = max(typical_spacing * SPACING_TOLERANCE, Decimal("0.01"))
        scored["spacing"] = _in_range(mine.strike_spacing, spacings, tolerance)
        wide = f"{mine.strike_spacing * 100:.0f}%"
        if scored["spacing"] >= 0.99:
            result.reasons.append(f"strikes {wide} apart, the width you sell on this")
        else:
            usual = f"{min(spacings) * 100:.0f}-{max(spacings) * 100:.0f}%"
            result.misses.append(f"strikes {wide} apart against your {usual}")

    # --- recorded entry delta, when there is one on both sides ------------
    deltas = [p.short_delta for p in theirs if p.short_delta is not None]
    if not deltas or mine.short_delta is None:
        result.unknowns.append("no recorded delta at entry")
    else:
        scored["delta"] = _in_range(mine.short_delta, deltas, DELTA_TOLERANCE)
        if scored["delta"] >= 0.99:
            result.reasons.append(f"short delta {mine.short_delta:.2f}, in your usual band")
        else:
            result.misses.append(
                f"short delta {mine.short_delta:.2f} against your "
                f"{min(deltas):.2f}-{max(deltas):.2f}"
            )

    # --- weighted mean over what could be checked -------------------------
    total_weight = sum(_WEIGHTS[k] for k in scored)
    if total_weight <= 0:
        return result
    confidence = sum(_WEIGHTS[k] * v for k, v in scored.items()) / total_weight
    confidence -= UNKNOWN_PENALTY * len(result.unknowns)

    conflict = _name_conflict(name, candidate.strategy_type.value)
    if conflict:
        result.misses.append(conflict)
        confidence = min(confidence, NAME_CONFLICT_CAP)

    # --- continuation of a position already in the strategy ---------------
    for member in members:
        if member.closed_at is None:
            continue
        gap = candidate.opened_at - member.closed_at
        if timedelta(0) <= gap <= ROLL_WINDOW:
            result.roll_of = member.id
            result.reasons.append(
                f"opened {gap.days}d after a trade in this strategy closed — looks like a roll"
            )
            break

    result.confidence = max(0.0, min(1.0, round(confidence, 4)))
    return result
