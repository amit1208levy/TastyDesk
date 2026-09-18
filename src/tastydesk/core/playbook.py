"""Strategies the user defines by hand, and finding older trades like them.

The journal reconstructs trades from the broker's fills, which is the only way
to get cash flows right. It is not a good way to decide what a *strategy* is.
The broker knows that four legs were filled on one order; it does not know that
you think of them as "my /ZB 112 straddle" and that you have put the same trade
on eleven times since March.

So this module inverts the relationship. You group legs and name the group. The
name is yours — read for intent and shown back, but never trusted to decide a
match, because a name is shorthand and shorthand leaves things out. What decides
a match is the shape:

* the same underlying product, always — a /ZB idea never matches a /CL trade
* the same legs: how many, put or call, long or short, in the same ratio
* the same relationship between their expirations — all one expiry, or split
* opened inside a comparable window

Strikes and deltas are deliberately excluded. Selling the 0.20 delta and selling
the 0.35 delta is the same strategy executed differently, and folding the
distance into the identity would split one idea into a dozen.

Nothing is matched below ``MATCH_THRESHOLD``. A near-miss is listed as a
possibility for the user to confirm, never counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from math import gcd
from typing import Any

from tastydesk.core.models import Direction, Leg, Strategy

__all__ = [
    "MATCH_THRESHOLD",
    "LegShape",
    "Signature",
    "NamedStrategy",
    "Match",
    "signature_of",
    "product_of",
    "read_name",
    "score_match",
    "find_matches",
]

# How sure the shape has to be before a trade is counted rather than suggested.
MATCH_THRESHOLD = 0.95

# Opens this far apart are treated as one entry window regardless of the
# original's timing; beyond it, the trades are a different rhythm.
WINDOW_TOLERANCE = timedelta(hours=4)

_MONTH_CODES = "FGHJKMNQUVXZ"


def product_of(underlying: str) -> str:
    """The tradable product behind a contract month.

    ``/ZSF7`` and ``/ZSX6`` are the same idea in different months, and a
    strategy defined on one must match the other. Equities are already products.
    """
    key = (underlying or "").strip().upper()
    if not key.startswith("/"):
        return key
    # Strip a trailing month code + year digit(s): ZSF7 -> ZS, MESZ6 -> MES.
    trimmed = re.sub(rf"[{_MONTH_CODES}]\d{{1,2}}$", "", key[1:])
    return f"/{trimmed}" if trimmed else key


@dataclass(frozen=True, slots=True, order=True)
class LegShape:
    """One leg, stripped of everything that is execution rather than identity."""

    right: str  # "C", "P" or "S" for shares
    side: str  # "long" | "short"
    ratio: int  # quantity relative to the smallest leg in the group

    def describe(self) -> str:
        kind = {"C": "call", "P": "put", "S": "shares"}[self.right]
        size = "" if self.ratio == 1 else f"{self.ratio}x "
        return f"{size}{self.side} {kind}"


@dataclass(frozen=True, slots=True)
class Signature:
    """What makes two trades the same strategy."""

    product: str
    legs: tuple[LegShape, ...]
    expiry_pattern: str  # "single" | "split"
    window_minutes: int

    def describe(self) -> str:
        legs = ", ".join(leg.describe() for leg in self.legs)
        when = "one expiry" if self.expiry_pattern == "single" else "split expiries"
        return f"{self.product}: {legs} ({when})"


@dataclass(slots=True)
class NamedStrategy:
    """A strategy the user defined, and what they called it."""

    id: str
    name: str
    product: str
    signature: Signature
    member_ids: list[str] = field(default_factory=list)
    note: str | None = None
    created_at: datetime | None = None
    # What the name appears to mean. Shown to the user, never used to match.
    name_reading: str | None = None


@dataclass(frozen=True, slots=True)
class Match:
    """A historical trade that looks like a named strategy."""

    strategy_id: str
    trade_ids: tuple[str, ...]
    score: float
    confident: bool
    reasons: tuple[str, ...]
    misses: tuple[str, ...]
    opened_at: datetime
    realized_pnl: Decimal


def _option_legs(strategy: Strategy) -> list[Leg]:
    return [leg for leg in strategy.legs if leg.option_type is not None]


def _ratios(quantities: list[Decimal]) -> list[int]:
    """Whole-number leg ratios, so 2:2 and 1:1 are the same shape."""
    ints = [int(q) if q == int(q) else 0 for q in quantities]
    if 0 in ints or not ints:
        return [1 for _ in quantities]
    divisor = 0
    for value in ints:
        divisor = gcd(divisor, value)
    return [value // divisor for value in ints] if divisor else ints


def signature_of(trades: list[Strategy]) -> Signature:
    """Derive the shape from the trades the user grouped together."""
    legs: list[Leg] = []
    for trade in trades:
        legs.extend(trade.legs)
    if not legs:
        raise ValueError("A strategy needs at least one leg")

    products = {product_of(trade.underlying) for trade in trades}
    product = products.pop() if len(products) == 1 else sorted(products)[0]

    quantities = [leg.quantity for leg in legs]
    ratios = _ratios(quantities)

    shapes = sorted(
        LegShape(
            right=leg.option_type.value if leg.option_type else "S",
            side="short" if leg.direction is Direction.SHORT else "long",
            ratio=ratio,
        )
        for leg, ratio in zip(legs, ratios, strict=True)
    )

    expirations = {leg.expiration for leg in legs if leg.expiration}
    pattern = "single" if len(expirations) <= 1 else "split"

    opens = sorted(trade.opened_at for trade in trades)
    window = int((opens[-1] - opens[0]).total_seconds() // 60)

    return Signature(
        product=product,
        legs=tuple(shapes),
        expiry_pattern=pattern,
        window_minutes=window,
    )


# Words that carry a structure, used only to show the user what their name
# appears to say. Never consulted when matching.
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    (r"\bpmcc\b|poor man", "a poor man's covered call"),
    (r"\bstrangle\b", "a strangle"),
    (r"\bstraddle\b", "a straddle"),
    (r"\bcondor\b", "an iron condor"),
    (r"\bfly\b|\bbutterfly\b", "a butterfly"),
    (r"\bdiagonal\b", "a diagonal"),
    (r"\bcalendar\b", "a calendar"),
    (r"\bcredit spread\b|\bcsp?\b", "a credit spread"),
    (r"\bdebit spread\b", "a debit spread"),
    (r"\bcovered call\b|\bcc\b", "a covered call"),
    (r"\bwheel\b", "the wheel"),
    (r"\bjade\b", "a jade lizard"),
    (r"\bratio\b", "a ratio spread"),
    (r"\bnaked\b", "a naked short"),
    (r"\bhedge\b|\bprotect", "a hedge"),
    (r"\broll\b", "a roll"),
)


def read_name(name: str) -> str | None:
    """What the name appears to mean, for display only.

    Shorthand leaves things out, and the user was explicit that a name does not
    always say everything — so this is shown beside the shape rather than used
    in place of it, and it is never an input to a match.
    """
    lowered = (name or "").lower()
    found = [meaning for pattern, meaning in _NAME_HINTS if re.search(pattern, lowered)]
    if not found:
        return None
    return found[0] if len(found) == 1 else f"{found[0]} (the name also mentions {found[1]})"


def score_match(signature: Signature, candidate: Signature) -> tuple[float, list[str], list[str]]:
    """How alike two shapes are, with the agreements and the differences named.

    The product is a gate rather than a score: a different underlying is not a
    weaker match, it is a different trade.
    """
    if signature.product != candidate.product:
        return 0.0, [], [f"different product ({candidate.product}, not {signature.product})"]

    reasons = [f"same product ({signature.product})"]
    misses: list[str] = []
    score = 0.0

    # The legs are most of the identity.
    if signature.legs == candidate.legs:
        score += 0.70
        reasons.append(f"identical legs ({', '.join(shape.describe() for shape in signature.legs)})")
    else:
        sides = {(leg.right, leg.side) for leg in signature.legs}
        other = {(leg.right, leg.side) for leg in candidate.legs}
        if sides == other and len(signature.legs) == len(candidate.legs):
            score += 0.45
            misses.append("same legs but a different size ratio")
        else:
            missing = sides - other
            extra = other - sides
            detail = []
            if missing:
                detail.append("missing " + ", ".join(f"{s} {r}" for r, s in sorted(missing)))
            if extra:
                detail.append("extra " + ", ".join(f"{s} {r}" for r, s in sorted(extra)))
            misses.append("; ".join(detail) or "different legs")

    if signature.expiry_pattern == candidate.expiry_pattern:
        score += 0.20
        reasons.append(
            "all one expiry" if signature.expiry_pattern == "single" else "expiries split the same way"
        )
    else:
        misses.append(f"expiries {candidate.expiry_pattern}, not {signature.expiry_pattern}")

    # Timing is corroboration, not identity: it separates one decision from two
    # that happened to look alike a week apart.
    tolerance = max(WINDOW_TOLERANCE.total_seconds() // 60, signature.window_minutes * 2)
    if candidate.window_minutes <= tolerance:
        score += 0.10
        reasons.append(
            "opened together"
            if candidate.window_minutes < 5
            else f"opened within {candidate.window_minutes} minutes"
        )
    else:
        misses.append(f"legs opened {candidate.window_minutes} minutes apart")

    return round(score, 4), reasons, misses


def find_matches(
    strategy: NamedStrategy,
    trades: list[Strategy],
    *,
    exclude_ids: set[str] | None = None,
) -> list[Match]:
    """Older trades whose shape matches a named strategy.

    Every trade is scored on its own. Combinations of separate trades are not
    assembled here: guessing that two unrelated fills were meant as one
    structure is exactly the inference the user asked this app to stop making.
    """
    exclude = exclude_ids or set()
    out: list[Match] = []

    for trade in trades:
        if trade.id in exclude or trade.id in strategy.member_ids:
            continue
        if not trade.legs:
            continue
        if product_of(trade.underlying) != strategy.product:
            continue

        candidate = signature_of([trade])
        score, reasons, misses = score_match(strategy.signature, candidate)
        if score <= 0.0:
            continue

        out.append(
            Match(
                strategy_id=strategy.id,
                trade_ids=(trade.id,),
                score=score,
                confident=score >= MATCH_THRESHOLD,
                reasons=tuple(reasons),
                misses=tuple(misses),
                opened_at=trade.opened_at,
                realized_pnl=trade.realized_pnl,
            )
        )

    out.sort(key=lambda m: (-m.score, m.opened_at))
    return out


def to_row(strategy: NamedStrategy) -> dict[str, Any]:
    """Flatten for storage."""
    return {
        "id": strategy.id,
        "name": strategy.name,
        "product": strategy.product,
        "note": strategy.note,
        "signature": {
            "product": strategy.signature.product,
            "legs": [
                {"right": leg.right, "side": leg.side, "ratio": leg.ratio} for leg in strategy.signature.legs
            ],
            "expiry_pattern": strategy.signature.expiry_pattern,
            "window_minutes": strategy.signature.window_minutes,
        },
        "member_ids": list(strategy.member_ids),
    }


def from_row(row: dict[str, Any]) -> NamedStrategy:
    sig = row["signature"]
    return NamedStrategy(
        id=row["id"],
        name=row["name"],
        product=row["product"],
        signature=Signature(
            product=sig["product"],
            legs=tuple(
                LegShape(right=leg["right"], side=leg["side"], ratio=int(leg["ratio"])) for leg in sig["legs"]
            ),
            expiry_pattern=sig["expiry_pattern"],
            window_minutes=int(sig["window_minutes"]),
        ),
        member_ids=list(row.get("member_ids") or []),
        note=row.get("note"),
        created_at=row.get("created_at"),
        name_reading=read_name(row["name"]),
    )
