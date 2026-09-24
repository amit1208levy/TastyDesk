"""Deciding which legs belong to the same trade.

This is the module the whole journal's accuracy rests on. ``grouping.py`` puts
legs together when the broker filled them under one order id, which is correct
as far as it goes and misses the most common thing a discretionary trader does:
leg in. Sell the call, then sell the put a minute later. Buy the long-dated
call, then sell the short-dated one against it.

Counted separately, those come apart in a specific and misleading way. The short
leg banks its credit and reads as a winner; the long leg is a hedge that was
never meant to make money on its own and reads as a loser. In one real book that
turned a poor man's covered call into a $5,821 loss alongside a $583 win, and it
made "naked calls lose money" look like a finding when it was an artifact of the
bookkeeping.

The approach here is deliberately unequal in its confidence:

*Certain* — the legs form a named structure at the same expiration, in the same
underlying, in the same size, opened within minutes. Nobody sells an unrelated
put on the same underlying sixty seconds after selling a call at the same
expiry. These are merged, and the merge is recorded so it can be undone.

*Likely* — a relationship that is real most of the time and not always. A
long-dated call and a short-dated call against it is usually one diagonal, but
it can genuinely be two decisions. These are proposed, never assumed.

*Left alone* — everything else. A wrong merge is worse than a missed one,
because a missed one is visible as two trades and a wrong one is invisible.

A confirmation is remembered as a *pattern*, not as a one-off: answering "these
two are one diagonal" for IWM teaches the rule for every pair shaped like it, so
a few hundred trades become a handful of questions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from tastydesk.core.classify import classify
from tastydesk.core.models import Leg, OptionType, Strategy, StrategyType

__all__ = [
    "Confidence",
    "PairKind",
    "PairCandidate",
    "PairingRules",
    "find_candidates",
    "pattern_key",
    "apply_pairings",
]

# Legs of one decision arrive seconds apart, not hours. Beyond a few minutes the
# case for "same trade" rests on the structure alone, which is the sort of guess
# this module refuses to make silently.
CERTAIN_WINDOW = timedelta(minutes=5)
LIKELY_WINDOW = timedelta(hours=4)


class Confidence(StrEnum):
    CERTAIN = "certain"
    LIKELY = "likely"


class PairKind(StrEnum):
    """What the combined legs appear to be."""

    STRANGLE = "strangle"
    STRADDLE = "straddle"
    VERTICAL = "vertical"
    DIAGONAL = "diagonal"
    CALENDAR = "calendar"
    RATIO = "ratio"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class PairCandidate:
    """Two trades that look like one, with the reasoning attached."""

    left_id: str
    right_id: str
    underlying: str
    kind: PairKind
    confidence: Confidence
    gap_seconds: int
    reason: str
    pattern: str
    combined_pnl: Decimal
    # What the merged legs would be called, so the user sees the consequence.
    merged_type: StrategyType


@dataclass(frozen=True, slots=True)
class PairingRules:
    """Answers the user has already given, keyed by pattern.

    ``"merge"`` means every future pair shaped like this is one trade;
    ``"separate"`` means they never are. A pattern absent from here has not been
    decided and keeps being asked about.
    """

    decisions: dict[str, Literal["merge", "separate"]]

    def verdict(self, pattern: str) -> Literal["merge", "separate"] | None:
        return self.decisions.get(pattern)


def _option_legs(strategy: Strategy) -> list[Leg]:
    return [leg for leg in strategy.legs if leg.option_type is not None]


def _single_expiry(legs: list[Leg]) -> bool:
    return len({leg.expiration for leg in legs}) == 1


def _quantity(legs: list[Leg]) -> Decimal:
    return sum((leg.quantity for leg in legs), Decimal(0))


def pattern_key(candidate_kind: PairKind, underlying: str, left: Strategy, right: Strategy) -> str:
    """A shape a decision can generalise over.

    Deliberately coarse: the underlying, what the combination is, and whether the
    expirations match. Including strikes or dates would make every pair its own
    pattern and every pair its own question, which defeats the point.
    """
    same_expiry = "same-expiry" if set(left.expirations) == set(right.expirations) else "split-expiry"
    return f"{underlying}|{candidate_kind.value}|{same_expiry}"


def _describe(kind: PairKind, left: Strategy, right: Strategy, gap: timedelta) -> str:
    minutes = int(gap.total_seconds() // 60)
    when = "in the same minute" if minutes < 1 else f"{minutes} minutes apart"
    article = {
        PairKind.STRANGLE: "a strangle",
        PairKind.STRADDLE: "a straddle",
        PairKind.VERTICAL: "a vertical spread",
        PairKind.DIAGONAL: "a diagonal",
        PairKind.CALENDAR: "a calendar",
        PairKind.RATIO: "a ratio spread",
        PairKind.OTHER: "one position",
    }[kind]
    return (
        f"Opened {when} on {left.underlying}, and together the legs make {article}. "
        f"Counted apart, the short side reads as a winner and the long side as a loser, "
        f"which is bookkeeping rather than trading."
    )


def _kind_and_confidence(
    left: Strategy, right: Strategy, gap: timedelta
) -> tuple[PairKind, Confidence] | None:
    """Classify the relationship, or return None when there is no case to make."""
    left_legs, right_legs = _option_legs(left), _option_legs(right)
    if not left_legs or not right_legs:
        return None
    # Only ever pair simple pieces. Merging two already-multi-leg structures is
    # a different and much less certain claim.
    if len(left_legs) > 2 or len(right_legs) > 2:
        return None
    if not (_single_expiry(left_legs) and _single_expiry(right_legs)):
        return None

    same_expiry = left_legs[0].expiration == right_legs[0].expiration
    types = {leg.option_type for leg in left_legs} | {leg.option_type for leg in right_legs}
    directions = {leg.direction for leg in left_legs} | {leg.direction for leg in right_legs}
    same_size = _quantity(left_legs) == _quantity(right_legs)
    quick = gap <= CERTAIN_WINDOW

    if same_expiry and len(directions) == 1 and types == {OptionType.CALL, OptionType.PUT}:
        # Both sides short (or both long) at one expiry: a strangle or straddle.
        strikes = {leg.strike for leg in left_legs} | {leg.strike for leg in right_legs}
        kind = PairKind.STRADDLE if len(strikes) == 1 else PairKind.STRANGLE
        return kind, (Confidence.CERTAIN if quick and same_size else Confidence.LIKELY)

    if same_expiry and len(types) == 1 and len(directions) == 2:
        # One long, one short, same type and expiry: a vertical.
        return PairKind.VERTICAL, (Confidence.CERTAIN if quick and same_size else Confidence.LIKELY)

    if not same_expiry and len(types) == 1 and len(directions) == 2:
        # A long-dated leg with a short-dated one against it. Usually one trade
        # -- a diagonal, or a poor man's covered call -- but genuinely not
        # always, so never certain.
        strikes = {leg.strike for leg in left_legs} | {leg.strike for leg in right_legs}
        kind = PairKind.CALENDAR if len(strikes) == 1 else PairKind.DIAGONAL
        return kind, Confidence.LIKELY

    if same_expiry and len(directions) == 1 and len(types) == 1 and not same_size:
        return PairKind.RATIO, Confidence.LIKELY

    return None


def find_candidates(strategies: list[Strategy], rules: PairingRules | None = None) -> list[PairCandidate]:
    """Every pair of trades that plausibly belong together.

    Pairs the user has already called separate are dropped; pairs already merged
    are not re-proposed, because the merged trade no longer looks like either
    half.
    """
    rules = rules or PairingRules(decisions={})
    # Keyed by account as well as underlying. Merging says two trades are one
    # position, and a position lives in one account: two short /ZB puts sold the
    # same minute in two accounts are two trades, however alike they look. Every
    # figure outside this file adds the accounts together, which is how they are
    # traded; joining trades is where the boundary is real.
    by_underlying: dict[tuple[str, str], list[Strategy]] = defaultdict(list)
    for strategy in strategies:
        if not strategy.manual_group:
            by_underlying[(strategy.account_number, strategy.underlying)].append(strategy)

    out: list[PairCandidate] = []
    for (_account, underlying), members in by_underlying.items():
        members.sort(key=lambda s: (s.opened_at, s.id))
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                gap = right.opened_at - left.opened_at
                if gap > LIKELY_WINDOW:
                    break
                verdict = _kind_and_confidence(left, right, gap)
                if verdict is None:
                    continue
                kind, confidence = verdict
                pattern = pattern_key(kind, underlying, left, right)
                if rules.verdict(pattern) == "separate":
                    continue

                merged_type, _ = classify(_option_legs(left) + _option_legs(right))
                out.append(
                    PairCandidate(
                        left_id=left.id,
                        right_id=right.id,
                        underlying=underlying,
                        kind=kind,
                        confidence=confidence,
                        gap_seconds=int(gap.total_seconds()),
                        reason=_describe(kind, left, right, gap),
                        pattern=pattern,
                        combined_pnl=left.realized_pnl + right.realized_pnl,
                        merged_type=merged_type,
                    )
                )

    # Biggest money first: the point of the queue is to fix the trades that move
    # the numbers, not to work through hundreds in order.
    out.sort(key=lambda c: (c.confidence is not Confidence.CERTAIN, -abs(c.combined_pnl)))
    return out


def apply_pairings(candidates: list[PairCandidate], rules: PairingRules | None = None) -> dict[str, str]:
    """Which trades to merge, as a strategy-id to group-id map.

    Certain pairs are merged on their own. Likely ones are merged only where the
    user has said that shape is one trade. Merges chain: if A pairs with B and B
    with C, all three end up in one group, which is what a three-legged position
    legged in over a few minutes actually is.
    """
    rules = rules or PairingRules(decisions={})
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # Lowest id wins so the group id is stable across rebuilds.
            low, high = sorted((ra, rb))
            parent[high] = low

    for candidate in candidates:
        decided = rules.verdict(candidate.pattern)
        if decided == "separate":
            continue
        if candidate.confidence is Confidence.CERTAIN or decided == "merge":
            union(candidate.left_id, candidate.right_id)

    groups: dict[str, str] = {}
    for node in list(parent):
        root = find(node)
        if root != node or any(find(other) == root for other in parent if other != node):
            groups[node] = root
    return groups
