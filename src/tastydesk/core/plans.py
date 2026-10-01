"""The plan a trade is entered under, and whether it is being kept.

A plan is a promise made before the trade, in plain words: where profit is
taken, where the loss is cut, what happens at the time line, and what is done
when a short strike is pushed on. Each promise can carry one number the app
can watch. The words are his; the number is what lets the app say "your plan
says: now".

A plan belongs to a named strategy, not to one fill, because the strategies he
runs are things he keeps re-opening — a strangle on /ZW closed and sold again
is the same idea under the same promise.

Plans are never edited in place. Every save is a new version with its date,
and a version saved while trades were open is marked so: moving the goalposts
mid-trade is allowed, but it is on the record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from tastydesk.core.analytics import RuleSet
from tastydesk.core.indicators import Verdict, verdict_for
from tastydesk.core.models import Strategy, StrategyPnL, StrategyRisk

__all__ = [
    "PlanCheck",
    "TradePlan",
    "check",
    "from_dict",
    "plan_verdict",
    "rules_for",
    "sentences",
]


@dataclass(frozen=True, slots=True)
class TradePlan:
    # Fraction of max profit: 0.5 is "take it off at 50%".
    take_profit: Decimal | None = None
    # Multiple of the credit: 2 is "cut it when the loss is twice what I took in".
    stop_multiple: Decimal | None = None
    # Days to expiry at which the trade is closed or rolled.
    dte_exit: int | None = None
    # Short-strike delta, as a fraction, at which the position is adjusted.
    adjust_delta: Decimal | None = None
    # What he will do at each line, in his own words.
    profit_note: str = ""
    loss_note: str = ""
    time_note: str = ""
    adjust_note: str = ""
    # Anything else: when he enters, what he will not do.
    notes: str = ""

    @property
    def empty(self) -> bool:
        return self == TradePlan()


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{value!r} is not a number") from None
    return None if out.is_nan() else out


def _text(value: Any) -> str:
    return str(value or "").strip()[:1000]


def from_dict(raw: dict[str, Any]) -> TradePlan:
    """A plan from what the page sends, with every number checked for sense."""
    take = _dec(raw.get("take_profit"))
    stop = _dec(raw.get("stop_multiple"))
    delta = _dec(raw.get("adjust_delta"))
    dte_raw = raw.get("dte_exit")
    dte = None if dte_raw in (None, "") else int(dte_raw)

    if take is not None and not (Decimal(0) < take <= Decimal(1)):
        raise ValueError("Take profit is a share of max profit, between 1% and 100%")
    if stop is not None and not (Decimal(0) < stop <= Decimal(20)):
        raise ValueError("The stop is a multiple of the credit, between 0.1× and 20×")
    if dte is not None and not (0 <= dte <= 400):
        raise ValueError("Days to expiry runs from 0 to 400")
    if delta is not None and not (Decimal(0) < delta < Decimal(1)):
        raise ValueError("Delta is between 0.01 and 0.99")

    return TradePlan(
        take_profit=take,
        stop_multiple=stop,
        dte_exit=dte,
        adjust_delta=delta,
        profit_note=_text(raw.get("profit_note")),
        loss_note=_text(raw.get("loss_note")),
        time_note=_text(raw.get("time_note")),
        adjust_note=_text(raw.get("adjust_note")),
        notes=_text(raw.get("notes")),
    )


def to_dict(plan: TradePlan) -> dict[str, Any]:
    out = asdict(plan)
    for key in ("take_profit", "stop_multiple", "adjust_delta"):
        out[key] = None if out[key] is None else str(out[key])
    return out


def rules_for(plan: TradePlan | None, default: RuleSet) -> RuleSet:
    """The global rules with this plan's numbers in place of theirs."""
    if plan is None:
        return default
    take, stop, dte = plan.take_profit, plan.stop_multiple, plan.dte_exit
    return replace(
        default,
        profit_target_pct=default.profit_target_pct if take is None else take,
        stop_loss_multiple=default.stop_loss_multiple if stop is None else stop,
        dte_exit=default.dte_exit if dte is None else dte,
    )


def _pct(value: Decimal) -> str:
    return f"{value * 100:.0f}%"


def sentences(plan: TradePlan) -> list[dict[str, str]]:
    """The plan read back as the promises it is, one per line."""
    out: list[dict[str, str]] = []

    def add(key: str, line: str | None, note: str) -> None:
        if line is None and not note:
            return
        if note:
            # His words as he wrote them, made to read as a sentence.
            note = note[0].upper() + note[1:]
            if note[-1] not in ".!?":
                note += "."
        text = " ".join(part for part in (line, note) if part)
        out.append({"key": key, "text": text})

    add(
        "profit",
        None if plan.take_profit is None else f"I take profit at {_pct(plan.take_profit)} of max profit.",
        plan.profit_note,
    )
    add(
        "loss",
        None
        if plan.stop_multiple is None
        else f"I cut the loss when it reaches {plan.stop_multiple:g}× the credit I took in.",
        plan.loss_note,
    )
    add(
        "time",
        None if plan.dte_exit is None else f"With {plan.dte_exit} days left, I close or roll.",
        plan.time_note,
    )
    add(
        "adjust",
        None
        if plan.adjust_delta is None
        else f"When a short strike reaches {plan.adjust_delta * 100:.0f} delta, I adjust.",
        plan.adjust_note,
    )
    if plan.notes:
        out.append({"key": "notes", "text": plan.notes})
    return out


@dataclass(frozen=True, slots=True)
class PlanCheck:
    """One promise against where the position is now."""

    key: str
    label: str
    line: str  # the promise, short: "50% of max profit"
    reading: str | None  # where it is: "32% now"
    progress: float | None  # 0 = nowhere near, 1 = the line itself
    hit: bool
    note: str


def check(strategy: Strategy, pnl: StrategyPnL, risk: StrategyRisk, plan: TradePlan) -> list[PlanCheck]:
    """Each promise that has a number, measured on this position now."""
    out: list[PlanCheck] = []

    def clamp(x: Decimal | float) -> float:
        return max(0.0, min(1.0, float(x)))

    if plan.take_profit is not None:
        got = pnl.pct_of_max_profit
        out.append(
            PlanCheck(
                "profit",
                "Take profit",
                f"at {_pct(plan.take_profit)} of max profit",
                None if got is None else f"{_pct(got)} now",
                None if got is None else clamp(got / plan.take_profit),
                got is not None and got >= plan.take_profit,
                plan.profit_note,
            )
        )

    if plan.stop_multiple is not None:
        got = pnl.pct_of_credit
        down = None if got is None else max(-got, Decimal(0))
        out.append(
            PlanCheck(
                "loss",
                "Cut the loss",
                f"at {plan.stop_multiple:g}× the credit",
                None
                if got is None
                else (f"down {down:.2f}× now" if down and down > 0 else "not losing now"),
                None if down is None else clamp(down / plan.stop_multiple),
                got is not None and got <= -plan.stop_multiple,
                plan.loss_note,
            )
        )

    if plan.dte_exit is not None:
        dte = risk.dte
        entry = strategy.front_entry_dte
        # Sold inside the line on purpose: the line was never meant for it.
        exempt = entry is not None and entry <= plan.dte_exit
        out.append(
            PlanCheck(
                "time",
                "Close or roll",
                f"at {plan.dte_exit} days left",
                None if dte is None else f"{dte} days left" + (" — sold inside the line" if exempt else ""),
                None
                if dte is None or exempt or entry is None or entry <= plan.dte_exit
                else clamp((entry - dte) / (entry - plan.dte_exit)),
                dte is not None and not exempt and dte <= plan.dte_exit,
                plan.time_note,
            )
        )

    if plan.adjust_delta is not None:
        worst = risk.worst_short_delta
        out.append(
            PlanCheck(
                "adjust",
                "Adjust",
                f"at {plan.adjust_delta * 100:.0f} delta",
                None if worst is None else f"worst short at {worst * 100:.0f} delta",
                None if worst is None else clamp(worst / plan.adjust_delta),
                worst is not None and worst >= plan.adjust_delta,
                plan.adjust_note,
            )
        )
    return out


_NOTE_FOR = {"Stop out": "loss", "Roll or close": "time", "Take profit": "profit", "Adjust": "adjust"}


def plan_verdict(
    strategy: Strategy,
    pnl: StrategyPnL,
    risk: StrategyRisk,
    plan: TradePlan | None,
    default: RuleSet,
) -> Verdict:
    """The usual verdict, judged on this plan's numbers and quoting its words."""
    rules = rules_for(plan, default)
    verdict = verdict_for(
        strategy,
        pnl,
        risk,
        profit_target=rules.profit_target_pct,
        dte_exit=rules.dte_exit,
        stop_multiple=rules.stop_loss_multiple,
    )
    if plan is None:
        return verdict

    worst = risk.worst_short_delta
    if (
        plan.adjust_delta is not None
        and worst is not None
        and worst >= plan.adjust_delta
        and verdict.rank > 2
    ):
        verdict = Verdict(
            "Adjust",
            f"a short strike is at {worst * 100:.0f} delta, past your {plan.adjust_delta * 100:.0f} line",
            2,
            "act",
        )

    key = _NOTE_FOR.get(verdict.action)
    note = getattr(plan, f"{key}_note", "") if key else ""
    if note:
        verdict = Verdict(verdict.action, f"{verdict.reason} — your plan: {note}", verdict.rank, verdict.tone)
    return verdict


def version_row(plan: TradePlan, saved_at: datetime, while_open: bool) -> dict[str, Any]:
    return {"saved_at": saved_at, "plan": to_dict(plan), "while_open": while_open}


def record(closed: list[Strategy], plan: TradePlan, default: RuleSet) -> dict[str, Any]:
    """How the trades closed under this plan kept its lines.

    The profit and time lines are judged exactly as the Rules page judges the
    global ones, on this plan's numbers. The loss line is judged on where the
    trade closed: closing at a loss past the stop is a broken promise. A trade
    that went past the stop and came back cannot be seen in a closed record,
    so this can only ever undercount the breaks, never invent one.
    """
    from tastydesk.core import analytics
    from tastydesk.core.pnl import premium_at_risk

    rules = rules_for(plan, default)
    done = analytics.closed_strategies(closed)
    scored = analytics.rule_adherence(done, rules)
    out: dict[str, Any] = {"trades": len(done), "lines": []}
    if plan.take_profit is not None:
        r = scored["profit_target"]
        out["lines"].append({"key": "profit", "kept": r.followed, "broken": r.violated})
    if plan.dte_exit is not None:
        r = scored["dte_exit"]
        out["lines"].append({"key": "time", "kept": r.followed, "broken": r.violated})
    if plan.stop_multiple is not None:
        kept = broken = 0
        for s in done:
            credit = premium_at_risk(s)
            if credit <= 0 or s.realized_pnl >= 0:
                continue
            if -s.realized_pnl > credit * plan.stop_multiple * (1 + rules.stop_band):
                broken += 1
            else:
                kept += 1
        out["lines"].append({"key": "loss", "kept": kept, "broken": broken})
    return out
