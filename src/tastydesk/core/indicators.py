"""Every number the app can show about a position or a leg, in one catalogue.

Why a catalogue rather than columns
-----------------------------------
The Positions table used to decide for the user what mattered: nine fixed
columns, chosen once. But which nine matter depends on what he is doing. Sizing
a new trade, the ones that count are buying power and beta-weighted delta.
Managing an open book, it is days in trade, theta and the distance to the short
strike. Checking a hedge, it is vega. No fixed set is right for all of those.

So the app publishes the whole list, with a label and a format for each entry,
and the user picks which ones he wants and in which order. The same catalogue
serves both levels:

* **strategy** fields describe a whole position — the unit risk is measured on
* **leg** fields describe one contract inside it, and they are the template
  applied wherever a leg is drawn, so a leg always reads the same way

Every value here is either measured or ``None``. Nothing is estimated to fill a
column: a field the data cannot support shows as blank, which is the truth,
rather than as zero, which is a claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tastydesk.core.greeks import leg_dollar_delta
from tastydesk.core.models import (
    ZERO,
    DangerLevel,
    Direction,
    Leg,
    OptionType,
    Strategy,
    StrategyPnL,
    StrategyRisk,
    UnderlyingQuote,
)

__all__ = [
    "Field",
    "STRATEGY_FIELDS",
    "LEG_FIELDS",
    "Verdict",
    "VERDICTS",
    "verdict_for",
    "strategy_values",
    "leg_values",
]


@dataclass(frozen=True, slots=True)
class Verdict:
    """What to do about a position, and why.

    The app already knows the user's rules — take profit at 50% of max profit,
    be out by 21 days, stop at twice the credit — and it already scores how much
    trouble a position is in. Everything it needed to answer "what do I do about
    this one?" was present and buried a click deep, which left a table of
    eleven conditions and no verdicts. This is the verdict.

    ``rank`` sorts the table. It is not severity: it is how soon this needs a
    hand. A trade through its stop comes first, a winner past its target sits
    above one that needs nothing, and "leave it" sorts last — the list reads
    top-down as a to-do list rather than as a scoreboard.
    """

    action: str
    reason: str
    rank: int
    tone: str  # act | take | watch | none


VERDICTS: tuple[tuple[str, str], ...] = (
    ("Stop out", "past the stop you set"),
    ("Decide", "several danger signals at once"),
    ("Roll or close", "inside your 21-day line"),
    ("Take profit", "past your 50% target"),
    ("Leave it", "inside every rule"),
)


def verdict_for(
    strategy: Strategy,
    pnl: StrategyPnL,
    risk: StrategyRisk,
    *,
    profit_target: Decimal,
    dte_exit: int,
    stop_multiple: Decimal,
) -> Verdict:
    """Turn the rules and the risk reading into one instruction.

    Order matters and is the order a trader would use: a position through its
    stop outranks a winner at its target, which outranks a quiet position with
    time left. Each answer carries the reason it fired, because an instruction
    without its reason is something to argue with rather than act on.
    """
    pct_credit = pnl.pct_of_credit
    captured = pnl.pct_of_max_profit

    if pct_credit is not None and pct_credit <= -stop_multiple:
        return Verdict(
            "Stop out",
            f"down {abs(pct_credit):.0%} of the credit, past your {stop_multiple:g}x stop",
            0,
            "act",
        )

    if risk.level in (DangerLevel.CRITICAL, DangerLevel.DANGER):
        worst = next(
            (r.message for r in risk.reasons if r.level in (DangerLevel.CRITICAL, DangerLevel.DANGER)),
            "several signals at once",
        )
        return Verdict("Decide", worst, 1, "act")

    if risk.dte is not None and risk.dte <= dte_exit:
        return Verdict(
            "Roll or close",
            f"{risk.dte} days left, inside your {dte_exit}-day line",
            2,
            "act",
        )

    if captured is not None and captured >= profit_target:
        return Verdict(
            "Take profit",
            f"{captured:.0%} of max profit, past your {profit_target:.0%} target",
            3,
            "take",
        )

    if risk.level is DangerLevel.TESTED:
        return Verdict("Watch", "tested, but no rule has fired yet", 4, "watch")

    return Verdict("Leave it", "inside every rule you set", 5, "none")


@dataclass(frozen=True, slots=True)
class Field:
    """One entry in the catalogue.

    ``format`` tells the page how to print the value, never what it means:
    money, percent, a signed number, a count of days. ``tone`` says whether the
    figure has a direction worth colouring — a P&L does, a strike does not.
    """

    id: str
    label: str
    hint: str
    format: str  # money | money0 | percent | number | delta | days | date | text | level
    align: str = "right"
    tone: str = "none"  # none | signed | inverse
    group: str = "Position"
    default: bool = False
    width: int | None = None


# --------------------------------------------------------------------------
# What can be shown about a whole position
# --------------------------------------------------------------------------

STRATEGY_FIELDS: tuple[Field, ...] = (
    Field("verdict", "Do", "What to do about it, and why", "verdict",
          align="left", group="Identity", default=True, width=210),
    Field("underlying", "Underlying", "The contract this position is in", "text",
          align="left", group="Identity", default=True),
    Field("price", "Price", "What the underlying is trading at right now",
          "number", group="Identity", default=True),
    Field("strategy", "Strategy", "Your name for it, or the shape the legs make",
          "text", align="left", group="Identity", default=True),
    Field("structure", "Structure", "The shape the classifier reads off the legs",
          "text", align="left", group="Identity"),
    Field("risk_profile", "Risk profile", "Whether the loss is capped by the structure",
          "text", align="left", group="Identity"),
    Field("account", "Account", "Which account holds it", "text", align="left",
          group="Identity"),
    Field("legs", "Legs", "How many contracts make up the position", "number",
          group="Identity"),
    Field("trades", "Trades", "How many of your trades were merged into this row",
          "number", group="Identity"),

    Field("dte", "DTE", "Days to the nearest expiry", "days", group="Time", default=True),
    Field("dte_at_entry", "DTE at entry", "Days to expiry when you opened it", "days",
          group="Time"),
    Field("days_in_trade", "Days in trade", "How long you have held it", "days",
          group="Time"),
    Field("opened", "Opened", "The day you put it on", "date", align="left", group="Time"),
    Field("expiry", "Expiry", "The nearest expiration date", "date", align="left",
          group="Time"),
    Field("rolls", "Rolls", "How many times you have rolled it", "number", group="Time"),

    Field("credit", "Credit", "Net credit taken in, or debit paid", "money0",
          tone="signed", group="Money", default=True),
    Field("premium", "Premium collected", "Gross option premium, the scale your stop reads",
          "money0", group="Money"),
    Field("open_pnl", "P&L", "What it would realise if closed now", "money",
          tone="signed", group="Money", default=True),
    Field("day_change", "P&L today", "What it has done since the last session",
          "money", tone="signed", group="Money", default=True),
    Field("pct_of_credit", "% of credit", "P&L as a share of the premium collected",
          "percent", tone="signed", group="Money", default=True),
    # The rule is "manage at 50% of max profit", so this is the number the rule
    # is written against — not % of credit, which diverges from it the moment a
    # position is rolled.
    Field("pct_of_max_profit", "% of max profit", "Progress toward your 50% target",
          "percent", tone="signed", group="Money", default=True),
    Field("pct_of_max_loss", "% of max loss", "How much of the defined risk is used",
          "percent", tone="inverse", group="Money"),
    Field("max_profit", "Max profit", "The most it can make", "money0", group="Money"),
    Field("max_loss", "Max loss", "The most it can lose, where that is defined",
          "money0", group="Money"),
    Field("cost_to_close", "Cost to close", "What buying it back costs right now",
          "money0", group="Money"),
    Field("fees", "Fees", "Commission and clearing paid on it", "money", group="Money"),

    Field("bp", "Buying power", "Margin this position is holding", "money0",
          group="Risk"),
    Field("bp_pct", "BP % of net liq", "Share of the account it is holding", "percent",
          group="Risk"),
    Field("pnl_per_bp", "P&L per BP", "Return on the margin it is tying up", "percent",
          tone="signed", group="Risk"),
    Field("short_delta", "Short Δ", "Highest delta among the short legs", "delta",
          group="Risk", default=True),
    Field("distance_pct", "Distance to short", "How far the underlying is from the nearest short strike",
          "percent", group="Risk"),
    Field("distance_sigma", "Distance in σ", "That distance in standard deviations",
          "number", group="Risk"),
    Field("risk_level", "Risk", "The app's reading of how much attention it needs",
          "level", align="left", group="Risk", default=True),
    Field("breached", "Breached", "Whether the underlying has gone through a short strike",
          "text", align="left", group="Risk"),
    # Off by default: it can only be drawn for a position with a defined max
    # loss, and on this book that is a minority — a column that is blank on six
    # rows out of eleven reads as broken data rather than as missing data.
    Field("position_on_risk", "Position on risk", "Where it sits between your stop and its max loss",
          "scale", align="left", group="Risk", width=170),

    Field("delta_dollars", "$ delta", "What a one-point move in the underlying is worth",
          "money0", tone="signed", group="Greeks"),
    Field("bwd", "BWD (SPY)", "Beta-weighted delta: what it behaves like in SPY shares",
          "delta", tone="signed", group="Greeks"),
    Field("net_delta", "Net Δ", "Sum of leg deltas, in the underlying's own units",
          "delta", tone="signed", group="Greeks"),
    Field("theta", "Theta", "Dollars a day, at the current mark", "money0",
          tone="signed", group="Greeks"),
    Field("vega", "Vega", "Dollars per one point of implied volatility", "money0",
          tone="inverse", group="Greeks"),
    Field("gamma", "Gamma", "How fast delta itself is moving", "number", group="Greeks"),
    Field("iv_rank", "IV rank", "Where this underlying's volatility sits in its year",
          "percent", group="Greeks"),
    Field("iv_rank_entry", "IV rank at entry", "Where it sat when you opened",
          "percent", group="Greeks"),
)


# --------------------------------------------------------------------------
# What can be shown about a single leg
# --------------------------------------------------------------------------

LEG_FIELDS: tuple[Field, ...] = (
    Field("leg", "Leg", "Side, size and contract", "text", align="left",
          group="Contract", default=True),
    Field("side", "Side", "Long or short", "text", align="left", group="Contract"),
    Field("quantity", "Qty", "Contracts held", "number", group="Contract", default=True),
    Field("right", "Type", "Put, call or shares", "text", align="left", group="Contract"),
    Field("strike", "Strike", "The strike price", "number", group="Contract"),
    Field("expiry", "Expiry", "When this leg expires", "date", align="left",
          group="Contract", default=True),
    Field("dte", "DTE", "Days until this leg expires", "days", group="Contract",
          default=True),
    Field("symbol", "Symbol", "The broker's symbol for the contract", "text",
          align="left", group="Contract"),
    Field("multiplier", "Multiplier", "What one point of this contract is worth",
          "number", group="Contract"),

    Field("open_price", "Open", "What you paid or received per contract", "money",
          group="Money", default=True),
    Field("mark", "Mark", "What it is worth per contract now", "money", group="Money",
          default=True),
    Field("bid", "Bid", "Best bid", "money", group="Money"),
    Field("ask", "Ask", "Best ask", "money", group="Money"),
    Field("spread", "Spread", "Ask minus bid — what crossing it costs", "money",
          group="Money"),
    Field("value", "Value", "Mark times contracts times multiplier", "money0",
          tone="signed", group="Money"),
    Field("pnl", "Leg P&L", "This leg alone — detail, never a risk signal", "money",
          tone="signed", group="Money"),
    Field("extrinsic", "Extrinsic", "The time value left in it", "money", group="Money"),
    Field("intrinsic", "Intrinsic", "How far in the money it is", "money", group="Money"),

    Field("delta", "Delta", "Per contract", "delta", group="Greeks", default=True),
    Field("delta_dollars", "$ delta", "What a one-point move is worth on this leg",
          "money0", tone="signed", group="Greeks"),
    Field("gamma", "Gamma", "Per contract", "number", group="Greeks"),
    Field("theta", "Theta", "Dollars a day from this leg", "money0", tone="signed",
          group="Greeks", default=True),
    Field("vega", "Vega", "Dollars per volatility point from this leg", "money0",
          tone="inverse", group="Greeks"),
    Field("iv", "IV", "Implied volatility on this contract", "percent", group="Greeks",
          default=True),
    Field("moneyness", "Moneyness", "In, at or out of the money", "text", align="left",
          group="Greeks"),
)


def _days_between(start: datetime, end: datetime | None = None) -> int:
    return max(((end or datetime.now(UTC)) - start).days, 0)


def strategy_values(
    strategy: Strategy,
    pnl: StrategyPnL,
    risk: StrategyRisk,
    *,
    today: date,
    quote: UnderlyingQuote | None,
    price: Decimal | None,
    net_liq: Decimal | None,
    name: str | None = None,
    parts: int = 1,
    premium: Decimal | None = None,
    beta: Decimal | None = None,
    reference_price: Decimal | None = None,
    verdict: Verdict | None = None,
    day_change: Decimal | None = None,
) -> dict[str, Any]:
    """Every strategy-level field, measured or None. Never estimated."""
    legs = strategy.legs
    dollar_deltas = [leg_dollar_delta(leg, price) for leg in legs]
    delta_dollars = None if any(d is None for d in dollar_deltas) else sum(dollar_deltas, ZERO)

    bwd = None
    if delta_dollars is not None and beta is not None and reference_price:
        bwd = delta_dollars * beta / reference_price

    vega = None
    vegas = [leg.vega for leg in legs if leg.is_option]
    if vegas and all(v is not None for v in vegas):
        vega = sum(
            (leg.vega * leg.notional_multiplier for leg in legs if leg.is_option and leg.vega),
            ZERO,
        )

    gamma = None
    gammas = [leg.gamma for leg in legs if leg.is_option]
    if gammas and all(g is not None for g in gammas):
        gamma = sum(
            (leg.gamma * leg.notional_multiplier for leg in legs if leg.is_option and leg.gamma),
            ZERO,
        )

    bp = strategy.buying_power_used
    expirations = strategy.expirations

    return {
        "verdict": None
        if verdict is None
        else {
            "action": verdict.action,
            "reason": verdict.reason,
            "rank": verdict.rank,
            "tone": verdict.tone,
        },
        "underlying": strategy.underlying,
        "price": price,
        "day_change": day_change,
        "strategy": name or strategy.strategy_type.value,
        "structure": strategy.strategy_type.value,
        "risk_profile": strategy.risk_profile.value,
        "account": strategy.account_number,
        "legs": len(legs),
        "trades": parts,
        "dte": risk.dte,
        "dte_at_entry": (
            strategy.dte_at_entry
            if strategy.dte_at_entry is not None
            else ((expirations[0] - strategy.opened_at.date()).days if expirations else None)
        ),
        "days_in_trade": _days_between(strategy.opened_at, strategy.closed_at),
        "opened": strategy.opened_at.date(),
        "expiry": expirations[0] if expirations else None,
        "rolls": strategy.roll_count,
        "credit": strategy.net_credit,
        "premium": premium,
        "open_pnl": pnl.open_pnl,
        "pct_of_credit": pnl.pct_of_credit,
        "pct_of_max_profit": pnl.pct_of_max_profit,
        "pct_of_max_loss": pnl.pct_of_max_loss,
        "max_profit": pnl.max_profit,
        "max_loss": pnl.max_loss,
        "cost_to_close": pnl.cost_to_close,
        "fees": strategy.fees,
        "bp": bp,
        "bp_pct": (bp / net_liq) if bp is not None and net_liq else None,
        "pnl_per_bp": (
            pnl.open_pnl / bp if pnl.open_pnl is not None and bp is not None and bp > ZERO else None
        ),
        "short_delta": risk.worst_short_delta,
        "distance_pct": risk.distance_to_short_pct,
        "distance_sigma": risk.distance_to_short_sigma,
        "risk_level": risk.level.value,
        "breached": (
            "no" if not risk.breached else f"{risk.breached_side or 'yes'} side"
        ),
        "position_on_risk": None,  # drawn, not printed; the page owns this one
        "delta_dollars": delta_dollars,
        "bwd": bwd,
        "net_delta": strategy.net_position_delta,
        "theta": strategy.net_theta,
        "vega": vega,
        "gamma": gamma,
        "iv_rank": quote.iv_rank if quote else None,
        "iv_rank_entry": strategy.iv_rank_at_entry,
    }


def leg_values(leg: Leg, *, today: date, price: Decimal | None) -> dict[str, Any]:
    """Every leg-level field. This is the template a leg is drawn with."""
    right = (
        "shares"
        if leg.option_type is None
        else ("call" if leg.option_type is OptionType.CALL else "put")
    )
    side = "short" if leg.direction is Direction.SHORT else "long"

    intrinsic: Decimal | None = None
    extrinsic: Decimal | None = None
    moneyness: str | None = None
    if leg.option_type is not None and leg.strike is not None and price is not None:
        if leg.option_type is OptionType.CALL:
            intrinsic = max(price - leg.strike, ZERO)
        else:
            intrinsic = max(leg.strike - price, ZERO)
        if leg.mark is not None:
            extrinsic = leg.mark - intrinsic
        moneyness = "in the money" if intrinsic > ZERO else "out of the money"

    spread = None if leg.bid is None or leg.ask is None else leg.ask - leg.bid
    value = None if leg.mark is None else leg.mark * leg.notional_multiplier
    pnl = None if leg.close_cash_flow is None else leg.open_cash_flow + leg.close_cash_flow

    return {
        "leg": f"{side} {leg.quantity:g} "
        + (right if leg.option_type is None else f"{leg.strike:g} {right}"),
        "side": side,
        "quantity": leg.quantity,
        "right": right,
        "strike": leg.strike,
        "expiry": leg.expiration,
        "dte": leg.dte(today),
        "symbol": leg.symbol,
        "multiplier": leg.multiplier,
        "open_price": leg.open_price,
        "mark": leg.mark,
        "bid": leg.bid,
        "ask": leg.ask,
        "spread": spread,
        "value": value,
        "pnl": pnl,
        "extrinsic": extrinsic,
        "intrinsic": intrinsic,
        "delta": leg.delta,
        "delta_dollars": leg_dollar_delta(leg, price),
        "gamma": leg.gamma,
        "theta": None if leg.theta is None else leg.theta * leg.notional_multiplier,
        "vega": None if leg.vega is None else leg.vega * leg.notional_multiplier,
        "iv": leg.iv,
        "moneyness": moneyness,
    }
