"""His rulebook: the rules for each kind of strategy, and which strategy is which.

The rules come from his own documents. Each kind of strategy he trades — a
strangle, a Dynamic PMCC, a weekly put spread — gets one rule set: the four
lines the app already watches on a plan (profit, loss, time, adjust), the
sizing lines (the loss at the stop and the buying power, as shares of net liq),
and every rule as the document words it, with where it came from.

Which rule set covers which named strategy is read from the name he gave it.
Names are shorthand and often misspelt: "Dmpcc" is the DPMCC, "Stangle" is a
strangle, "weekly PCS" is a put credit spread. A name that fits two rule sets
about as well, or none, is not guessed at. It is listed for him to decide,
and what he decides is kept and wins over the reading.

A plan he wrote himself for a strategy still comes first: the rulebook fills
in the plan only where he has not written one, and its sizing lines are
checked either way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from tastydesk.core.indicators import Verdict
from tastydesk.core.models import Direction, Strategy, StrategyPnL
from tastydesk.core.plans import PlanCheck, TradePlan
from tastydesk.core.plans import sentences as plan_sentences

__all__ = [
    "AUTO_MATCH",
    "SECTIONS",
    "Assignment",
    "Candidate",
    "Rule",
    "Rulebook",
    "StrategyRules",
    "assign",
    "candidates",
    "checks",
    "from_dict",
    "plan_of",
    "sentences",
    "to_dict",
    "verdict",
]

# Where a rule sits in a rule set. The first six follow a trade's life; "avoid"
# is what the rules forbid, "other" anything that fits none of them.
SECTIONS = ("entry", "size", "profit", "loss", "time", "adjust", "avoid", "other")

# How well a name has to fit a rule set before it is taken as that set, and by
# how much it has to beat the next one. Below either, he is asked.
AUTO_MATCH = 0.8
MARGIN = 0.08
# Two words count as the same word from here: "stangle" for "strangle" (0.93),
# "headge" for "hedge" (0.91), "dmpcc" for "dpmcc" (0.8). Words of three
# letters or fewer have to match exactly: "put" is not "pmt".
SAME_WORD = 0.8


# --------------------------------------------------------------------------
# The rulebook
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """One rule, as the document words it."""

    section: str
    text: str


@dataclass(frozen=True, slots=True)
class StrategyRules:
    """The rules for one kind of strategy.

    Every number is optional: a rule the document states only in words stays
    in words, and the app watches only what has a number.
    """

    id: str
    name: str
    # Other names he uses for it, as he writes them: "dmpcc", "pmcc".
    aliases: tuple[str, ...] = ()
    # Where the rules came from: the document and section, and a link to it.
    source: str = ""
    source_url: str = ""
    # The plan lines (see plans.TradePlan): profit as a share of max profit,
    # the loss as a multiple of the credit, days left, a short strike's delta.
    take_profit: Decimal | None = None
    stop_multiple: Decimal | None = None
    dte_exit: int | None = None
    adjust_delta: Decimal | None = None
    # A debit trade's stop: the loss, net of everything sold against it, as a
    # share of what its long side cost (a PMCC's 30% of the LEAP).
    stop_of_debit: Decimal | None = None
    # Size, as a share of net liq: the credit one trade may take in.
    max_credit: Decimal | None = None
    # Size, as shares of net liq: the loss at the stop, and buying power.
    max_loss: Decimal | None = None
    max_bp: Decimal | None = None
    # Days to expiry the trade is opened at, lowest and highest.
    entry_dte: tuple[int, int] | None = None
    rules: tuple[Rule, ...] = ()

    def words(self, *sections: str) -> str:
        return " ".join(r.text for r in self.rules if r.section in sections)


@dataclass(frozen=True, slots=True)
class Rulebook:
    sets: tuple[StrategyRules, ...] = ()
    # The folder or document the rules were read from.
    source: str = ""
    saved_at: datetime | None = None

    def get(self, rules_id: str | None) -> StrategyRules | None:
        return next((s for s in self.sets if s.id == rules_id), None)


def _dec(raw: Mapping[str, Any], key: str, low: Decimal, high: Decimal, what: str) -> Decimal | None:
    value = raw.get(key)
    if value in (None, ""):
        return None
    text = str(value).strip()
    scale = Decimal(1)
    if text.endswith("%"):
        text, scale = text[:-1], Decimal(100)
    try:
        out = Decimal(text) / scale
    except (InvalidOperation, ValueError):
        raise ValueError(f"{key}: {value!r} is not a number") from None
    if out.is_nan() or not (low < out <= high):
        raise ValueError(f"{key}: {what}")
    return out


def _int(raw: Mapping[str, Any], key: str, high: int) -> int | None:
    value = raw.get(key)
    if value in (None, ""):
        return None
    out = int(value)
    if not 0 <= out <= high:
        raise ValueError(f"{key}: days run from 0 to {high}")
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _set_from_dict(raw: Mapping[str, Any]) -> StrategyRules:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Every rule set needs a name")
    rules_id = _slug(str(raw.get("id") or name))
    entry = raw.get("entry_dte")
    entry_dte = None
    if entry not in (None, "", []):
        low, high = (int(entry[0]), int(entry[1]))
        if not 0 <= low <= high <= 1500:
            raise ValueError(f"{name}: entry_dte is [lowest, highest] days, 0 to 1500")
        entry_dte = (low, high)
    rules: list[Rule] = []
    for r in raw.get("rules") or []:
        section = str(r.get("section") or "other").strip().lower()
        if section not in SECTIONS:
            raise ValueError(f"{name}: section {section!r} is not one of {', '.join(SECTIONS)}")
        text = str(r.get("text") or "").strip()
        if text:
            rules.append(Rule(section, text[:1000]))
    try:
        return StrategyRules(
            id=rules_id,
            name=name,
            aliases=tuple(dict.fromkeys(str(a).strip() for a in raw.get("aliases") or () if str(a).strip())),
            source=str(raw.get("source") or "").strip(),
            source_url=str(raw.get("source_url") or "").strip(),
            take_profit=_dec(raw, "take_profit", Decimal(0), Decimal(1), "a share of max profit, 1% to 100%"),
            stop_multiple=_dec(
                raw, "stop_multiple", Decimal(0), Decimal(20), "the loss as 0.1× to 20× the credit"
            ),
            dte_exit=_int(raw, "dte_exit", 400),
            adjust_delta=_dec(
                raw, "adjust_delta", Decimal(0), Decimal("0.99"), "a delta between 0.01 and 0.99"
            ),
            stop_of_debit=_dec(
                raw, "stop_of_debit", Decimal(0), Decimal(1), "a share of the cost, 1% to 100%"
            ),
            max_loss=_dec(raw, "max_loss", Decimal(0), Decimal("0.5"), "a share of net liq, up to 50%"),
            max_bp=_dec(raw, "max_bp", Decimal(0), Decimal(1), "a share of net liq, up to 100%"),
            max_credit=_dec(raw, "max_credit", Decimal(0), Decimal("0.2"), "a share of net liq, up to 20%"),
            entry_dte=entry_dte,
            rules=tuple(rules),
        )
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None


def from_dict(raw: Mapping[str, Any], saved_at: datetime | None = None) -> Rulebook:
    """A rulebook from JSON, every number checked for sense and every id unique."""
    sets = tuple(_set_from_dict(s) for s in raw.get("sets") or [])
    seen: set[str] = set()
    for s in sets:
        if s.id in seen:
            raise ValueError(f"Two rule sets are called {s.id!r}")
        seen.add(s.id)
    saved = raw.get("saved_at")
    when = saved_at or (datetime.fromisoformat(saved) if isinstance(saved, str) and saved else None)
    return Rulebook(sets=sets, source=str(raw.get("source") or "").strip(), saved_at=when)


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def to_dict(book: Rulebook) -> dict[str, Any]:
    return {
        "source": book.source,
        "saved_at": book.saved_at.isoformat() if book.saved_at else None,
        "sets": [
            {
                "id": s.id,
                "name": s.name,
                "aliases": list(s.aliases),
                "source": s.source,
                "source_url": s.source_url,
                "take_profit": _num(s.take_profit),
                "stop_multiple": _num(s.stop_multiple),
                "dte_exit": s.dte_exit,
                "adjust_delta": _num(s.adjust_delta),
                "stop_of_debit": _num(s.stop_of_debit),
                "max_loss": _num(s.max_loss),
                "max_bp": _num(s.max_bp),
                "max_credit": _num(s.max_credit),
                "entry_dte": list(s.entry_dte) if s.entry_dte else None,
                "rules": [{"section": r.section, "text": r.text} for r in s.rules],
            }
            for s in book.sets
        ],
    }


def plan_of(rules: StrategyRules) -> TradePlan:
    """The rule set as a plan: its four lines, and its words beside them."""
    return TradePlan(
        take_profit=rules.take_profit,
        stop_multiple=rules.stop_multiple,
        dte_exit=rules.dte_exit,
        adjust_delta=rules.adjust_delta,
        profit_note=rules.words("profit"),
        loss_note=rules.words("loss"),
        time_note=rules.words("time"),
        adjust_note=rules.words("adjust"),
        notes=rules.words("entry", "size", "avoid", "other"),
    )


def sentences(rules: StrategyRules) -> list[dict[str, str]]:
    """The rule set read back as promises, in a trade's order: entry, the plan's
    four lines, size, then what it forbids and anything else."""

    def said(*parts: str) -> str:
        return " ".join(p for p in parts if p)

    out: list[dict[str, str]] = []
    days = ""
    if rules.entry_dte is not None:
        low, high = rules.entry_dte
        days = f"I open it with {low if low == high else f'{low}–{high}'} days to expiry."
    if entry := said(days, rules.words("entry")):
        out.append({"key": "entry", "text": entry})

    lines = [s for s in plan_sentences(plan_of(rules)) if s["key"] != "notes"]
    if rules.stop_of_debit is not None and rules.stop_multiple is None:
        debit = f"I cut the loss at {_pct(rules.stop_of_debit)} of what the long side cost."
        loss = next((s for s in lines if s["key"] == "loss"), None)
        if loss is not None:
            loss["text"] = said(debit, loss["text"])
        else:
            at = next((i for i, s in enumerate(lines) if s["key"] != "profit"), len(lines))
            lines.insert(at, {"key": "loss", "text": debit})
    out.extend(lines)

    size = said(
        ""
        if rules.max_loss is None
        else f"The loss at my stop stays under {_pct(rules.max_loss)} of net liq.",
        ""
        if rules.max_bp is None
        else f"One trade uses under {_pct(rules.max_bp)} of net liq in buying power.",
        ""
        if rules.max_credit is None
        else f"One trade takes in under {_pct(rules.max_credit)} of net liq in credit.",
        rules.words("size"),
    )
    for key, text in (("size", size), ("avoid", rules.words("avoid")), ("other", rules.words("other"))):
        if text:
            out.append({"key": key, "text": text})
    return out


# --------------------------------------------------------------------------
# Which strategy is which
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    rules_id: str
    name: str
    score: float
    # The words of his name the rule set accounts for: more is more specific.
    explained: int
    term: str  # what it matched: the rule set's name or one of its aliases


@dataclass(frozen=True, slots=True)
class Assignment:
    named_id: str
    rules_id: str | None
    # "you": his decision. "name": read from the name. None: not decided.
    by: str | None
    candidates: tuple[Candidate, ...] = field(default=())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))


def _similar(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) <= 3:
        return 0.0
    if len(a) == len(b):
        # Two letters swapped is one slip of the fingers, not a different word:
        # "dmpcc" for "dpmcc".
        off = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
        if len(off) == 2 and off[1] == off[0] + 1 and a[off[0]] == b[off[1]] and a[off[1]] == b[off[0]]:
            return 0.9
    return SequenceMatcher(None, a, b).ratio()


def _terms(rules: StrategyRules) -> list[tuple[str, list[str]]]:
    """Every way the rule set can be named: its name, id, aliases and their initials."""
    out: list[tuple[str, list[str]]] = []
    for text in (rules.name, rules.id.replace("-", " "), *rules.aliases):
        words = _words(text)
        if not words:
            continue
        out.append((text, words))
        if len(words) >= 2:
            # "put credit spread" is "PCS"; "poor man's covered call" is "PMCC".
            out.append((text, ["".join(w[0] for w in words)]))
    return out


def _fit(name: list[str], term: list[str]) -> tuple[float, int]:
    """How well a name carries every word of a term, and how many of its words that uses."""
    used: set[int] = set()
    scores: list[float] = []
    for word in term:
        best, at = 0.0, -1
        for i, mine in enumerate(name):
            if i in used:
                continue
            s = _similar(word, mine)
            if s > best:
                best, at = s, i
        if best < SAME_WORD:
            return 0.0, 0
        used.add(at)
        scores.append(best)
    share = len(used) / len(name)
    return sum(scores) / len(scores) * (0.75 + 0.25 * share), len(used)


def candidates(name: str, product: str, book: Rulebook) -> list[Candidate]:
    """The rule sets his name for a strategy could mean, best first.

    The product is taken out of the name first: "Bull ZB" on /ZB is "bull".
    """
    root = product.lstrip("/").lower()
    words = [w for w in _words(name) if w != root]
    if not words:
        return []
    found: list[Candidate] = []
    for rules in book.sets:
        best: Candidate | None = None
        for text, term in _terms(rules):
            score, explained = _fit(words, term)
            whole = _similar("".join(words), "".join(term))
            if whole >= 0.85 and whole > score:
                score, explained = whole, len(words)
            if score > 0 and (best is None or (score, explained) > (best.score, best.explained)):
                best = Candidate(rules.id, rules.name, round(score, 3), explained, text)
        if best is not None:
            found.append(best)
    return sorted(found, key=lambda c: (c.score, c.explained), reverse=True)


def _clear_winner(found: Sequence[Candidate]) -> Candidate | None:
    if not found or found[0].score < AUTO_MATCH:
        return None
    best = found[0]
    if len(found) > 1:
        second = found[1]
        # The one that accounts for more of the name is the more specific:
        # "calendarized strangle" over "strangle" for "calnderised strangle".
        if second.score > best.score - MARGIN and second.explained >= best.explained:
            return None
    return best


@dataclass(frozen=True, slots=True)
class Named:
    """What matching needs to know about a named strategy."""

    id: str
    name: str
    product: str


def assign(named: Iterable[Named], book: Rulebook, decided: Mapping[str, str]) -> list[Assignment]:
    """Each named strategy's rule set: his decision, else a clear reading of the name.

    ``decided`` maps a strategy to a rule set's id, or to "" for "no rules for
    this one". A decision naming a rule set the book no longer has is read
    again from the name.
    """
    out: list[Assignment] = []
    for n in named:
        found = tuple(candidates(n.name, n.product, book)[:3])
        if n.id in decided and (decided[n.id] == "" or book.get(decided[n.id]) is not None):
            out.append(Assignment(n.id, decided[n.id] or None, "you", found))
            continue
        winner = _clear_winner(found)
        if winner is not None:
            out.append(Assignment(n.id, winner.rules_id, "name", found))
        else:
            out.append(Assignment(n.id, None, None, found))
    return out


# --------------------------------------------------------------------------
# The rules the plan lines do not cover
# --------------------------------------------------------------------------


def _pct(value: Decimal) -> str:
    text = f"{value * 100:.1f}".removesuffix(".0")
    return f"{text}%"


def _clamp(x: Decimal) -> float:
    return max(0.0, min(1.0, float(x)))


def _long_cost(strategy: Strategy) -> Decimal:
    """What the open long options cost to buy: a PMCC's LEAP."""
    return sum(
        (
            leg.open_price * leg.quantity * leg.multiplier
            for leg in strategy.legs
            if leg.option_type is not None and leg.direction is Direction.LONG
        ),
        Decimal(0),
    )


def checks(
    strategy: Strategy,
    pnl: StrategyPnL,
    rules: StrategyRules,
    net_liq: Decimal | None,
    *,
    stop_multiple: Decimal | None = None,
    whole_trade: tuple[Decimal, date] | None = None,
) -> list[PlanCheck]:
    """The rule set's own lines on this position: a debit trade's stop, and its size.

    ``stop_multiple`` is the stop actually in force (his own plan's, if he
    wrote one), which is what the loss at the stop is measured on.
    ``whole_trade`` is the P&L of the whole trade and the day it began, where
    the app knows it: a PMCC's long call with its own history (a roll's gain
    or loss included) and every call sold against it, closed or open. Without
    it the stop is measured on what is open now.
    """
    out: list[PlanCheck] = []
    credit = pnl.net_credit
    debit = -credit if credit < 0 else Decimal(0)

    if rules.stop_of_debit is not None and debit > 0:
        # The loss net of the calls sold, against what the long side cost: a
        # PMCC's LEAP, not the debit left after the credits came in.
        cost = _long_cost(strategy) or debit
        total = whole_trade[0] if whole_trade is not None else pnl.open_pnl
        down = max(-(total or Decimal(0)), Decimal(0)) / cost
        if total is None:
            reading = None
        elif whole_trade is not None:
            since = f"since {whole_trade[1].day} {whole_trade[1]:%b}"
            reading = (
                f"whole trade down {_pct(down)} {since}" if down > 0 else f"whole trade not losing {since}"
            )
        else:
            reading = f"down {_pct(down)} now" if down > 0 else "not losing now"
        out.append(
            PlanCheck(
                "debit",
                "Cut the loss",
                f"at {_pct(rules.stop_of_debit)} of what the long side cost",
                reading,
                None if total is None else _clamp(down / rules.stop_of_debit),
                total is not None and down >= rules.stop_of_debit,
                rules.words("loss"),
                whose="rules",
            )
        )

    if rules.max_loss is not None and net_liq:
        stop = stop_multiple if stop_multiple is not None else rules.stop_multiple
        loss = None
        if credit > 0 and stop is not None:
            loss = stop * credit
        elif debit > 0 and rules.stop_of_debit is not None:
            loss = rules.stop_of_debit * (_long_cost(strategy) or debit)
        share = None if loss is None else loss / net_liq
        out.append(
            PlanCheck(
                "size",
                "Size",
                f"loss at the stop under {_pct(rules.max_loss)} of net liq",
                None if share is None else f"{_pct(share)} of net liq at the stop",
                None if share is None else _clamp(share / rules.max_loss),
                share is not None and share > rules.max_loss,
                rules.words("size"),
                whose="rules",
            )
        )

    if rules.max_credit is not None and net_liq and credit > 0:
        share = credit / net_liq
        out.append(
            PlanCheck(
                "credit",
                "Credit",
                f"under {_pct(rules.max_credit)} of net liq",
                f"{_pct(share)} of net liq taken in",
                _clamp(share / rules.max_credit),
                share > rules.max_credit,
                rules.words("size"),
                whose="rules",
            )
        )

    # What the trade ties up: its buying power, or what a debit trade cost if
    # that is more — a PMCC whose LEAP covers the short call shows no buying
    # power at all, but the money is spent.
    bp = strategy.buying_power_used
    used = max(bp or Decimal(0), debit)
    if rules.max_bp is not None and net_liq and (bp is not None or debit > 0):
        share = used / net_liq
        out.append(
            PlanCheck(
                "bp",
                "Buying power",
                f"under {_pct(rules.max_bp)} of net liq",
                f"{_pct(share)} of net liq now",
                _clamp(share / rules.max_bp),
                share > rules.max_bp,
                rules.words("size"),
                whose="rules",
            )
        )
    return out


def verdict(current: Verdict, found: Sequence[PlanCheck], rules: StrategyRules) -> Verdict:
    """A debit trade through its stop is a stop-out, whatever else the row says."""
    hit = next((c for c in found if c.key == "debit" and c.hit), None)
    if hit is None or current.rank <= 0:
        return current
    reason = f"{hit.reading}, past your {_pct(rules.stop_of_debit or Decimal(0))} line"
    note = rules.words("loss")
    return Verdict("Stop out", f"{reason} — your rules: {note}" if note else reason, 0, "act")
