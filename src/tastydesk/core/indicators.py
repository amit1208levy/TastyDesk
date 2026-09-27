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

from dataclasses import dataclass, replace
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

    # The line only applies to an expiry that was ever outside it. A call sold
    # with eight days on it, or a diagonal's weekly, was never trying to be
    # outside the line, and telling him to roll it at eight days is telling him
    # to abandon the trade he meant to put on.
    entry_dte = strategy.front_entry_dte
    if risk.dte is not None and risk.dte <= dte_exit and (entry_dte is None or entry_dte > dte_exit):
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

    # Exempt from the line, but eight days from expiry is still eight days from
    # expiry. "Leave it — inside every rule you set" is true and unhelpful.
    if (
        risk.dte is not None
        and risk.dte <= dte_exit
        and entry_dte is not None
        and entry_dte <= dte_exit
    ):
        return Verdict(
            "Watch",
            f"{risk.dte} days left, but you sold it short-dated — your "
            f"{dte_exit}-day line does not apply",
            4,
            "watch",
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
    # Whether a column of these adds up to anything, and in which direction.
    # ``sums`` is down the legs of one position, where every figure is in the
    # same contract; ``book_sums`` is down a page of positions, where it is
    # not. Dollars add up either way. A raw delta adds up across the legs of
    # one underlying and means nothing added across four, and an underlying's
    # price adds up nowhere at all: 104.69 of bond plus 704 of wheat is 808 of
    # nothing. A total the reader has to know not to trust is worse than none.
    sums: bool = False
    book_sums: bool = False
    # Adds up down a page of positions only when they are all one product —
    # a raw delta of /ZB rows is one number, a delta of /ZB plus BBY is two.
    book_sums_one_product: bool = False
    help: str = ""
    """The long form, filled in from the tables at the bottom of this file.

    ``hint`` is one line, written to fit in a margin. It had to be short, so
    the page printed four of them in a strip above the table and still could
    not say what a sigma is. This is the paragraph that strip could never
    hold: what the number is, how it is worked out, and how to read it. It is
    shown on hover, where it costs nothing until it is wanted."""


# --------------------------------------------------------------------------
# What can be shown about a whole position
# --------------------------------------------------------------------------

_STRATEGY_FIELDS: tuple[Field, ...] = (
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
          group="Identity", book_sums=True),
    Field("trades", "Trades", "How many of your trades were merged into this row",
          "number", group="Identity", book_sums=True),

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
          tone="signed", group="Money", default=True, book_sums=True),
    Field("premium", "Premium collected", "Gross option premium, the scale your stop reads",
          "money0", group="Money", book_sums=True),
    Field("open_pnl", "P&L", "What it would realise if closed now", "money0",
          tone="signed", group="Money", default=True, book_sums=True),
    Field("day_change", "P&L today", "What it has done since the last session",
          "money0", tone="signed", group="Money", default=True, book_sums=True),
    Field("pct_of_credit", "% of credit", "P&L as a share of the premium collected",
          "percent", tone="signed", group="Money", default=True),
    # The rule is "manage at 50% of max profit", so this is the number the rule
    # is written against — not % of credit, which diverges from it the moment a
    # position is rolled.
    Field("pct_of_max_profit", "% of max profit", "Progress toward your 50% target",
          "percent", tone="signed", group="Money", default=True),
    Field("pct_of_max_loss", "% of max loss", "How much of the defined risk is used",
          "percent", tone="inverse", group="Money"),
    Field("max_profit", "Max profit", "The most it can make", "money0", group="Money", book_sums=True),
    Field("max_loss", "Max loss", "The most it can lose, where that is defined",
          "money0", group="Money", book_sums=True),
    Field("cost_to_close", "Cost to close", "What buying it back costs right now",
          "money0", group="Money", book_sums=True),
    Field("fees", "Fees", "Commission and clearing paid on it", "money0", group="Money", book_sums=True),

    Field("bp", "Buying power", "Margin this position is holding", "money0",
          group="Risk", book_sums=True),
    Field("bp_pct", "BP % of net liq", "Share of the account it is holding", "percent",
          group="Risk"),
    Field("pnl_per_bp", "P&L per BP", "Return on the margin it is tying up", "percent",
          tone="signed", group="Risk"),
    Field("short_delta", "Short Δ", "Highest delta among the short legs", "delta",
          group="Risk", default=True),
    Field("distance_pct", "Distance to short", "How far the underlying is from the nearest short strike",
          "percent", group="Risk"),
    Field("expected_move", "Expected move", "How far the market prices this to move by expiry",
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
          "money0", tone="signed", group="Greeks", book_sums=True),
    Field("bwd", "BWD (SPY)", "Beta-weighted delta: what it behaves like in SPY shares",
          "delta", tone="signed", group="Greeks", book_sums=True),
    Field("net_delta", "Net Δ", "Sum of leg deltas, in the underlying's own units",
          "delta", tone="signed", group="Greeks", book_sums_one_product=True),
    Field("theta", "Theta", "Dollars a day, at the current mark", "money0",
          tone="signed", group="Greeks", book_sums=True),
    Field("vega", "Vega", "Dollars per one point of implied volatility", "money0",
          tone="inverse", group="Greeks", book_sums=True),
    Field("gamma", "Gamma", "How fast delta itself is moving", "number", group="Greeks", book_sums=True),
    Field("iv_rank", "IV rank", "Where this underlying's volatility sits in its year",
          "percent", group="Greeks"),
    Field("iv_rank_entry", "IV rank at entry", "Where it sat when you opened",
          "percent", group="Greeks"),
)


# --------------------------------------------------------------------------
# What can be shown about a single leg
# --------------------------------------------------------------------------

_LEG_FIELDS: tuple[Field, ...] = (
    Field("leg", "Leg", "Side, size and contract", "text", align="left",
          group="Contract", default=True),
    Field("side", "Side", "Long or short", "text", align="left", group="Contract"),
    Field("quantity", "Qty", "Contracts held", "number", group="Contract", default=True, sums=True),
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
    Field("value", "Value", "What the leg is worth to the account right now", "money0",
          tone="signed", group="Money", sums=True),
    Field("day_change", "P&L today", "What this leg has done since the last session",
          "money0", tone="signed", group="Money", sums=True),
    Field("cost_to_close", "Cost to close", "What buying this leg back costs right now",
          "money0", group="Money", sums=True),
    Field("prior_close", "Last close", "The broker's closing price for it last session",
          "money", group="Money"),
    Field("pnl", "Leg P&L", "This leg alone — detail, never a risk signal", "money",
          tone="signed", group="Money", sums=True),
    Field("extrinsic", "Extrinsic", "The time value left in it", "money", group="Money"),
    Field("intrinsic", "Intrinsic", "How far in the money it is", "money", group="Money"),

    Field("delta", "Delta", "Per contract", "delta", group="Greeks", default=True),
    Field("position_delta", "Position Δ",
          "The leg's delta in units of the underlying: delta times contracts times multiplier",
          "number", tone="signed", group="Greeks", sums=True),
    Field("delta_dollars", "$ delta", "What a one-point move is worth on this leg",
          "money0", tone="signed", group="Greeks", sums=True),
    Field("gamma", "Gamma", "Per contract", "number", group="Greeks", sums=True),
    Field("theta", "Theta", "Dollars a day from this leg", "money0", tone="signed",
          group="Greeks", default=True, sums=True),
    Field("vega", "Vega", "Dollars per volatility point from this leg", "money0",
          tone="inverse", group="Greeks", sums=True),
    Field("iv", "IV", "Implied volatility on this contract", "percent", group="Greeks",
          default=True),
    Field("opened", "Opened", "The day this leg was put on", "date", align="left",
          group="Contract"),
    Field("days_held", "Held", "How long this leg has been on", "days", group="Contract"),
    Field("to_strike", "To strike", "How far the underlying is from this leg's strike",
          "percent", group="Contract"),
    Field("notional", "Notional", "What this leg controls: strike times multiplier times size",
          "money0", group="Money", sums=True),
    Field("moneyness", "Moneyness", "In, at or out of the money", "text", align="left",
          group="Greeks"),
)


# --------------------------------------------------------------------------
# What each number actually means
# --------------------------------------------------------------------------
#
# A column heading has room for a word and the margin above the table had room
# for four short notes, which is how the page ended up explaining "Short Δ" as
# "odds the nearest short strike finishes in the money" and explaining nothing
# at all about the other thirty-nine fields. The notes were in the way of the
# numbers and still incomplete.
#
# So every field gets a paragraph instead, kept here beside the catalogue
# rather than in the page, because the same words have to serve the table, the
# column picker and the leg template. A test asserts none of them is missing.

_STRATEGY_HELP: dict[str, str] = {
    "verdict": (
        "The app reads your own rules against this position and says what to do: stop out, "
        "decide, roll or close, take profit, watch, or leave it. The line beside it is the "
        "reason that verdict came out. None of it is a view on the market — it is your rules, "
        "applied to where the position actually is."
    ),
    "underlying": (
        "What the position is on. Futures read by their root, so /ZB covers every contract "
        "month you hold in it, and everything on this page groups the same way."
    ),
    "price": (
        "The last price of the underlying itself, not of your options. Hold two contract "
        "months and each keeps its own price. Blank means no quote came back, which is not "
        "the same as a price of zero."
    ),
    "strategy": (
        "The name you gave it. If you never named it, this falls back to the shape the legs "
        "make. Your names are what Performance groups by, so naming a trade is what puts it "
        "into your own history."
    ),
    "structure": (
        "The shape the app reads off the legs — strangle, put credit spread, iron condor, "
        "covered call. It comes from the contracts, not from what you called the trade."
    ),
    "risk_profile": (
        "Whether the structure caps the loss. Defined means a long leg sits behind every "
        "short one and the worst case is a known number. Undefined means there is no "
        "structural ceiling and size is the only control you have."
    ),
    "account": "Which tastytrade account holds it.",
    "legs": (
        "How many contracts make up the position. A strangle is two, an iron condor four. "
        "Rolling adds legs until the old ones are closed or expire."
    ),
    "trades": (
        "How many separate trades the app merged into this one row. A position you rolled "
        "three times is one trade continuing, not four — this says how many were folded in."
    ),
    "dte": (
        "Days to the nearest expiry in the position. Your 21-day rule reads this number. "
        "Under 21 days gamma climbs steeply: delta starts moving fast, and a move that was "
        "survivable a week ago is not."
    ),
    "dte_at_entry": (
        "Days left when you opened it. This is the one to slice performance by — a 45-day "
        "entry and a 7-day entry are different trades even in the same underlying."
    ),
    "days_in_trade": (
        "Calendar days since you opened. Read next to P&L it gives what the position has "
        "actually earned per day of risk."
    ),
    "opened": (
        "The day the first leg filled. Rolls do not reset it: the app treats a roll as the "
        "same trade continuing, so this stays the original open."
    ),
    "expiry": (
        "The nearest expiration in the position. Legs dated further out are listed in the "
        "leg detail underneath."
    ),
    "rolls": (
        "How many times you rolled it. Rolls are linked through tastytrade's order chains, "
        "so the P&L above is cumulative across every one of them — not just the legs on now."
    ),
    "credit": (
        "The net cash you took in at open, after fees. Negative means you paid a debit "
        "instead. This is the base that % of credit is measured against."
    ),
    "premium": (
        "The gross option premium in the position — what the short options sold for. Your 2× "
        "stop reads this scale rather than the net credit, which on a tight spread can be "
        "close to nothing and makes percentages explode."
    ),
    "open_pnl": (
        "What you would keep, or pay, closing the whole structure now at the mark. Net of "
        "commission and clearing, taken from the real transaction records rather than "
        "estimated."
    ),
    "day_change": (
        "What the position has made or lost today, measured contract by contract from the "
        "broker's own close price: the previous session's close for anything you held "
        "overnight, and your fill price for anything opened today, which is how tastytrade "
        "measures it — so this figure and the one on their screen agree. It is blank rather "
        "than zero when a leg has no close price or no live mark, because a total missing a "
        "leg is not a smaller move, it is a wrong number."
    ),
    "pct_of_credit": (
        "P&L as a share of the premium you took in. +100% is the whole credit kept, −200% is "
        "your 2× stop. It says nothing about how much of your maximum loss is used: on a wide "
        "spread, −200% of credit can still be a small part of the risk."
    ),
    "pct_of_max_profit": (
        "How much of the best this trade could do you are holding right now — the scale the "
        "50% rule is named after, and the one it reads. Negative when the position is behind, "
        "which is the same question answered from the other side. After a roll the ceiling is "
        "lower than the credit you first collected, because the cash paid to roll cannot come "
        "back, and this measures against what is actually still reachable."
    ),
    "pct_of_max_loss": (
        "How much of your defined risk is in use right now. Only defined-risk structures "
        "have it. Where the loss has no ceiling the field stays blank rather than guessing."
    ),
    "max_profit": (
        "The most the structure can make if everything goes right. On a credit trade that is "
        "the credit received, and nothing more, however far the underlying runs your way."
    ),
    "max_loss": (
        "The most it can lose where the structure defines that: the width between strikes "
        "times the multiplier, less the credit. Undefined-risk positions leave it blank, "
        "because the honest answer is that there is no cap."
    ),
    "cost_to_close": (
        "What buying the whole structure back would cost at the mark. P&L is the credit you "
        "took in less this number."
    ),
    "fees": (
        "Commission and clearing actually charged on this position, read from the "
        "transaction records. Every P&L figure on the page is already net of it."
    ),
    "bp": (
        "The margin this one position is holding. It is the buying power reduction "
        "tastytrade reports, not a formula the app invents."
    ),
    "bp_pct": (
        "What share of your net liquidating value the position is tying up. This is the size "
        "question: a trade can be a good idea and still be too big."
    ),
    "pnl_per_bp": (
        "P&L divided by the buying power it holds. This is how premium selling is actually "
        "ranked — two trades up $300 are not equal if one holds $2,000 and the other $12,000."
    ),
    "short_delta": (
        "The highest delta among your short legs, as a positive number. Read it as the rough "
        "chance that strike finishes in the money: 0.30 is about one in three. The app calls "
        "it tested above 0.30 and danger above 0.45."
    ),
    "distance_pct": (
        "How far the underlying has to move, in percent, to reach your nearest short strike. "
        "Percent on its own is not risk — 3% is a long way in /ZB and nothing in a biotech — "
        "which is why the expected move sits beside it."
    ),
    "expected_move": (
        "How far the market is pricing this underlying to move between now and expiry, in its "
        "own money — the price times its implied volatility times the square root of the time "
        "left. Compare it with the distance to your short strike: a move several times that "
        "distance means the strike is well within reach, and one that falls short of it means "
        "the market does not expect to get there. It is the figure that compares honestly "
        "across products, which a percentage cannot: 3% is a long way in /ZB and nothing in a "
        "biotech, but a move the market prices at twice the distance to your strike means the "
        "same thing in both."
    ),
    "risk_level": (
        "The app's reading of how much attention the position needs, from calm through "
        "tested, danger and critical. It is scored on the whole structure — never on one leg "
        "— from the short delta, the distance to that strike, how much of the risk is used "
        "and the days left."
    ),
    "breached": (
        "Whether the underlying has actually traded through one of your short strikes. Past "
        "the strike is not the same as a loss, but it is where assignment and pin risk stop "
        "being theoretical."
    ),
    "position_on_risk": (
        "Where the trade sits on the line between your 2× credit stop and the most it could "
        "lose. It answers how much room is left, in one picture, without arithmetic."
    ),
    "delta_dollars": (
        "What a one-point move in the underlying is worth to this position, in dollars. Every "
        "leg's delta turned into money through its own contract multiplier, which is what "
        "makes /ZB and XLE addable at all."
    ),
    "bwd": (
        "Beta-weighted delta: this position's direction restated as SPY. The dollar delta is "
        "scaled by the product's beta to SPY and divided by the SPY price, so everything you "
        "hold adds into one number that means something."
    ),
    "net_delta": (
        "The plain sum of leg deltas, in the underlying's own units. Useful inside one "
        "product and meaningless across them: a delta on /ZB and a delta on XLE are not the "
        "same amount of money. Use BWD to compare."
    ),
    "theta": (
        "What the position earns, or pays, per day from time passing, at today's prices. For "
        "a premium seller it should be positive. It is a rate, not a promise, and it changes "
        "as the market does."
    ),
    "vega": (
        "What the position gains or loses per one point of implied volatility. Short premium "
        "is short vega: a jump in volatility hurts it even when the underlying has not moved "
        "at all."
    ),
    "gamma": (
        "How fast delta itself changes as the underlying moves. Short options have negative "
        "gamma and it grows sharply into expiry, which is the whole reason for the 21-day "
        "rule."
    ),
    "iv_rank": (
        "Where this underlying's implied volatility sits inside its own last year, 0 to 100. "
        "High means options are expensive against their own history, which is when selling "
        "premium is paid for the risk."
    ),
    "iv_rank_entry": (
        "The IV rank at the moment you opened. Slicing your results by it answers whether "
        "selling high volatility actually earned you more, or whether it just felt right."
    ),
}

_LEG_HELP: dict[str, str] = {
    "leg": (
        "Side, size and contract in one line — short 2 XLE 64 calls. Leg detail is structure, "
        "never a risk signal on its own."
    ),
    "side": "Long or short. The short legs are the ones you sold, and they carry assignment risk.",
    "quantity": "How many contracts of this leg are held.",
    "right": "Put, call, or the shares and futures themselves.",
    "strike": "The strike price of this contract.",
    "expiry": (
        "When this leg expires. Legs in one position can expire on different days — that is "
        "exactly what a diagonal or a calendar is."
    ),
    "dte": "Days until this particular leg expires, which may not be the position's nearest expiry.",
    "symbol": "The broker's exact symbol for the contract.",
    "multiplier": (
        "What one point of this contract is worth in dollars: 100 for a standard equity "
        "option, 1,000 for /ZB, 5 for /MES. It is what turns a delta into money, and what "
        "makes deltas from different products comparable."
    ),
    "open_price": "What you paid or received per contract when this leg was opened.",
    "mark": (
        "The current mid price per contract. Blank means no quote came back, which is not a "
        "price of zero."
    ),
    "bid": "The best price someone is currently bidding for this contract.",
    "ask": "The best price someone is currently asking for it.",
    "spread": (
        "Ask minus bid — what crossing the market on this leg costs. A wide spread is a real "
        "cost of getting out, and it is why a P&L marked at the mid is the optimistic version."
    ),
    "value": (
        "What this leg is worth to the account right now: negative for anything you are short, "
        "because closing it costs money. An outright futures contract is the exception — nothing "
        "changed hands when you bought it and the difference is settled every evening, so its "
        "value here is the move since entry rather than the price of the contract."
    ),
    "day_change": (
        "What this leg has made or lost today, measured from the broker's own closing price for "
        "it last session, which is the basis the platform uses. A leg opened today is measured "
        "from its fill instead, because it was never part of a close."
    ),
    "cost_to_close": (
        "What buying this leg back would cost right now, at the mark. Negative when closing it "
        "would pay you, which is the case for anything you are long."
    ),
    "prior_close": (
        "The closing price the broker recorded for this contract last session. It is the baseline "
        "P&L today is measured from, shown so the figure can be checked rather than trusted."
    ),
    "pnl": (
        "What this one leg has made or lost. Detail only. A short put down 300% inside a "
        "spread whose long put gained at the same time is not a 300% problem, and the app "
        "never raises an alarm on a leg's percentage."
    ),
    "extrinsic": (
        "The time value left in the contract — the part that decays, and the only part you "
        "are paid for. When a short option has almost none left there is nothing more to "
        "earn from holding it, and assignment gets likelier."
    ),
    "intrinsic": (
        "How far the contract is in the money: below the strike for a put, above it for a "
        "call, times the multiplier."
    ),
    "delta": (
        "The contract's delta, per contract. Roughly the chance it finishes in the money, and "
        "how much it moves per one point of the underlying."
    ),
    "position_delta": (
        "How much of the underlying this leg behaves like: its delta times the number of "
        "contracts times the multiplier, signed so that a short put reads positive. An outright "
        "future counts in full — one /ZB contract is a thousand points of bond — which is why "
        "this column adds up to the position's real delta where the per-contract one cannot."
    ),
    "delta_dollars": (
        "What a one-point move in the underlying is worth on this leg: delta times contracts "
        "times the multiplier."
    ),
    "gamma": "How fast this contract's delta changes as the underlying moves.",
    "theta": "What this leg earns, or pays, per day from time decay.",
    "vega": "What this leg gains or loses per one point of implied volatility.",
    "iv": (
        "The implied volatility the market is pricing into this contract. It is what the "
        "expected move on the position is computed from."
    ),
    "opened": (
        "The day this particular leg was opened, which is not always the day the trade was. "
        "A diagonal's short call is sold months after the long one it sits against, and a roll "
        "replaces one leg while the others stay. When you are deciding which legs belong to one "
        "idea, this is usually the fact that settles it."
    ),
    "days_held": (
        "How many days this leg has been on, counted from its own open rather than the trade's. "
        "Read it beside DTE: a leg held 40 days with 8 left has given up most of its time value "
        "already, and what is left is the part that moves fastest."
    ),
    "to_strike": (
        "How far the underlying has to travel to reach this leg's strike, as a share of where it "
        "is now. Positive means the strike is still out of the money, negative means the "
        "underlying has gone through it. This is distance, not danger: whether that matters "
        "depends on what the rest of the structure is doing, which is measured one level up."
    ),
    "notional": (
        "What this leg controls if it is assigned: the strike times the contract multiplier times "
        "the number of contracts. On a futures option this is the figure that surprises people — "
        "one /ZB put at 106 controls $106,000, not $10,600."
    ),
    "moneyness": (
        "In, at or out of the money right now. A short leg going in the money near expiry is "
        "one of the few genuine leg-level alarms there is — that is assignment and pin risk, "
        "which is physical rather than a percentage."
    ),
}

STRATEGY_FIELDS: tuple[Field, ...] = tuple(
    replace(f, help=_STRATEGY_HELP[f.id]) for f in _STRATEGY_FIELDS
)
LEG_FIELDS: tuple[Field, ...] = tuple(replace(f, help=_LEG_HELP[f.id]) for f in _LEG_FIELDS)



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
        "expected_move": risk.expected_move,
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
    # An outright futures contract is not shares. It had been reading as "long
    # shares" on /ZBZ6 because the only non-option label here was shares, which
    # is wrong twice over: it is a contract, not stock, and one point of it is
    # worth $1,000 rather than $1.
    if leg.option_type is OptionType.CALL:
        right = "call"
    elif leg.option_type is OptionType.PUT:
        right = "put"
    elif leg.is_future:
        right = "futures"
    else:
        right = "shares"
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

    to_strike: Decimal | None = None
    if leg.strike is not None and price is not None and price > ZERO:
        # Signed so that a strike the underlying has gone through reads
        # negative: the sign carries which side of it you are on.
        gap = (leg.strike - price) if leg.option_type is OptionType.CALL else (price - leg.strike)
        to_strike = gap / price

    notional: Decimal | None = None
    if leg.strike is not None:
        notional = abs(leg.strike * leg.notional_multiplier)

    spread = None if leg.bid is None or leg.ask is None else leg.ask - leg.bid
    # What this leg is worth to the account, which for an outright future is
    # not its notional. A /ZB contract marked at 104.69 was reading +$104,690
    # — the price of the bond, not anything the account holds — because a
    # future's cash never changed hands at entry and is settled every evening
    # instead. close_cash_flow already knows that; value asks the same question.
    value = leg.close_cash_flow

    day_change: Decimal | None = None
    if leg.mark is not None and leg.prior_close is not None:
        day_change = (leg.mark - leg.prior_close) * leg.notional_multiplier

    cost_to_close = None if leg.close_cash_flow is None else -leg.close_cash_flow
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
        "opened": None if leg.opened_at is None else leg.opened_at.date(),
        "days_held": (
            None if leg.opened_at is None else max((today - leg.opened_at.date()).days, 0)
        ),
        "to_strike": to_strike,
        "notional": notional,
        "symbol": leg.symbol,
        "multiplier": leg.multiplier,
        "open_price": leg.open_price,
        "mark": leg.mark,
        "bid": leg.bid,
        "ask": leg.ask,
        "spread": spread,
        "value": value,
        "day_change": day_change,
        "cost_to_close": cost_to_close,
        "prior_close": leg.prior_close,
        "pnl": pnl,
        "extrinsic": extrinsic,
        "intrinsic": intrinsic,
        # A future or a share has a delta of exactly one per unit, signed by
        # which side you are on. Left blank it read as "unknown" on the /ZB
        # contracts, and a delta total that skipped them missed the largest
        # exposure in the position.
        "delta": leg.delta
        if leg.is_option
        else (Decimal(1) if leg.direction is Direction.LONG else Decimal(-1)),
        "position_delta": leg.position_delta,
        "delta_dollars": leg_dollar_delta(leg, price),
        "gamma": leg.gamma,
        "theta": None if leg.theta is None else leg.theta * leg.notional_multiplier,
        "vega": None if leg.vega is None else leg.vega * leg.notional_multiplier,
        "iv": leg.iv,
        "moneyness": moneyness,
    }
