"""Domain model for Tasty Desk.

This module is the contract every other core module builds against. Read the
sign conventions before touching any money in this codebase.

Sign conventions
----------------
Everything monetary is expressed as **account cash flow**, matching the sign
tastytrade already puts on ``Transaction.net_value``:

    positive  = cash came into the account   (a credit)
    negative  = cash left the account        (a debit)

So for a short strangle opened for $3.00 on one contract::

    net_credit = +300      (you received $300)

and if it now costs $450 to buy back::

    cost_to_close = -450   (you would pay $450)
    open_pnl      = net_credit + cost_to_close = -150

``open_pnl`` is therefore always "what this trade is worth to me right now",
positive when winning. Fees and commissions are already baked into
``net_value`` by tastytrade and carry a negative sign, so summing
``net_value`` across a trade's transactions yields realized P&L net of costs
without any extra bookkeeping.

Per-leg prices (``Leg.open_price``, ``Leg.mark``) are the opposite: they are
*quoted prices*, always positive, per unit of the contract. Direction lives in
``Leg.direction``, never in the sign of the price.

The cardinal rule
-----------------
Risk is a property of a :class:`Strategy`, never of a :class:`Leg`. The short
put of a put credit spread routinely shows a far worse percentage move than
the spread it belongs to, because the long put gained at the same moment.
Anything that surfaces danger reads :class:`StrategyRisk`. Leg numbers are
display detail only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "Direction",
    "OptionType",
    "RiskProfile",
    "StrategyType",
    "DangerLevel",
    "RiskReason",
    "Leg",
    "Strategy",
    "StrategyPnL",
    "StrategyRisk",
    "UnderlyingQuote",
    "PortfolioSummary",
    "ZERO",
]

ZERO = Decimal("0")


class Direction(StrEnum):
    LONG = "Long"
    SHORT = "Short"


class OptionType(StrEnum):
    CALL = "C"
    PUT = "P"


class RiskProfile(StrEnum):
    DEFINED = "Defined"
    UNDEFINED = "Undefined"


class StrategyType(StrEnum):
    SHORT_STRANGLE = "Short Strangle"
    SHORT_STRADDLE = "Short Straddle"
    LONG_STRANGLE = "Long Strangle"
    LONG_STRADDLE = "Long Straddle"
    PUT_CREDIT_SPREAD = "Put Credit Spread"
    CALL_CREDIT_SPREAD = "Call Credit Spread"
    PUT_DEBIT_SPREAD = "Put Debit Spread"
    CALL_DEBIT_SPREAD = "Call Debit Spread"
    IRON_CONDOR = "Iron Condor"
    IRON_FLY = "Iron Fly"
    JADE_LIZARD = "Jade Lizard"
    NAKED_PUT = "Naked Put"
    NAKED_CALL = "Naked Call"
    COVERED_CALL = "Covered Call"
    LONG_CALL = "Long Call"
    LONG_PUT = "Long Put"
    CALENDAR = "Calendar"
    DIAGONAL = "Diagonal"
    RATIO_SPREAD = "Ratio Spread"
    EQUITY = "Equity"
    FUTURE = "Future"
    CUSTOM = "Custom"


# Strategies whose profit is capped at the credit taken in. For these,
# max_profit == net_credit and "% of max profit" == "% of credit".
CREDIT_STRATEGIES = frozenset(
    {
        StrategyType.SHORT_STRANGLE,
        StrategyType.SHORT_STRADDLE,
        StrategyType.PUT_CREDIT_SPREAD,
        StrategyType.CALL_CREDIT_SPREAD,
        StrategyType.IRON_CONDOR,
        StrategyType.IRON_FLY,
        StrategyType.JADE_LIZARD,
        StrategyType.NAKED_PUT,
        StrategyType.NAKED_CALL,
        StrategyType.COVERED_CALL,
    }
)


class DangerLevel(StrEnum):
    """How much attention an open strategy needs, worst wins."""

    OK = "OK"
    WATCH = "Watch"
    TESTED = "Tested"
    DANGER = "Danger"
    CRITICAL = "Critical"

    @property
    def rank(self) -> int:
        return _DANGER_RANK[self]


_DANGER_RANK = {
    DangerLevel.OK: 0,
    DangerLevel.WATCH: 1,
    DangerLevel.TESTED: 2,
    DangerLevel.DANGER: 3,
    DangerLevel.CRITICAL: 4,
}


@dataclass(frozen=True, slots=True)
class RiskReason:
    """One human-readable reason a strategy carries the danger level it does."""

    code: str
    level: DangerLevel
    message: str


@dataclass(slots=True)
class Leg:
    """One position inside a strategy.

    ``open_price`` and ``mark`` are positive quoted prices per unit. The
    account-level value of the leg is derived, never stored with a sign here.
    """

    symbol: str
    instrument_type: str
    underlying: str
    direction: Direction
    quantity: Decimal
    multiplier: Decimal = Decimal(100)

    option_type: OptionType | None = None
    strike: Decimal | None = None
    expiration: date | None = None

    open_price: Decimal = ZERO

    # Live data, filled in by the marks service. None means "not quoted yet".
    mark: Decimal | None = None
    # The broker's own close price for this contract: the prior session's
    # close for a position held overnight, and the fill price for one opened
    # today. It is the basis tastytrade measures a day's move from, which is
    # why it is taken from the broker rather than reconstructed here.
    prior_close: Decimal | None = None
    # When this leg was opened. Legs of one strategy do not share a date: a
    # diagonal's weekly is sold long after the LEAP it sits against, and a
    # rolled strangle's new month is days or months younger than the trade.
    opened_at: datetime | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    iv: Decimal | None = None

    @property
    def is_future(self) -> bool:
        """An outright futures contract, not an option on one."""
        return "future" in self.instrument_type.strip().lower() and not self.is_option

    @property
    def is_option(self) -> bool:
        return self.option_type is not None

    @property
    def is_short(self) -> bool:
        return self.direction is Direction.SHORT

    @property
    def signed_quantity(self) -> Decimal:
        """Quantity with direction folded in: negative when short."""
        return -self.quantity if self.is_short else self.quantity

    @property
    def notional_multiplier(self) -> Decimal:
        """Contracts x multiplier, signed by direction."""
        return self.signed_quantity * self.multiplier

    @property
    def open_cash_flow(self) -> Decimal:
        """Cash this leg produced at open. Positive when sold (credit).

        An outright futures contract produced none: the broker books the fill
        at a value of zero and settles the difference every evening instead.
        :attr:`close_cash_flow` has always known that; this end of the trade
        did not, which made a long /ZB leg look as though $106,530 had left the
        account. It showed up twice — as a leg P&L of -$108,390 when the
        contract was down $1,859, and as a premium at risk of $220,700 on a
        trade whose options took in $8,294, which every "% of credit" on that
        position was then divided by.
        """
        if self.is_future:
            return ZERO
        return -self.open_price * self.notional_multiplier

    @property
    def close_cash_flow(self) -> Decimal | None:
        """Cash closing this leg would produce now. Negative when buying back.

        An outright futures contract is the exception, and not as a special
        case bolted on: buying one moves no cash at all. The broker books the
        fill at a value of zero and settles the difference every evening
        instead, so the cash a future produces on the way out is the move since
        entry, not the whole notional. Treating it like an option -- where the
        premium really did leave the account -- priced two long /ZB contracts
        at a hundred thousand dollars of profit.
        """
        if self.mark is None:
            return None
        if self.is_future:
            return (self.mark - self.open_price) * self.notional_multiplier
        return self.mark * self.notional_multiplier

    @property
    def position_delta(self) -> Decimal | None:
        """Share-equivalent delta of the whole leg, signed by direction.

        A share's delta is 1 by definition and no quote is needed to know it.
        Waiting for the Greeks feed to supply one meant a single delivered
        equity position — the shares left behind by an assigned put — turned the
        entire portfolio's net delta into "unknown", because one None
        propagates all the way up.
        """
        if not self.is_option:
            return self.notional_multiplier
        if self.delta is None:
            return None
        return self.delta * self.notional_multiplier

    def dte(self, today: date) -> int | None:
        if self.expiration is None:
            return None
        return (self.expiration - today).days


@dataclass(slots=True)
class Strategy:
    """A group of legs that were opened together and are managed as one trade.

    A strategy survives rolls: rolling a strangle out in time extends the same
    strategy rather than starting a new one, so ``realized_pnl`` and
    ``net_credit`` accumulate across the whole chain.
    """

    id: str
    account_number: str
    underlying: str
    strategy_type: StrategyType
    risk_profile: RiskProfile
    legs: list[Leg]

    opened_at: datetime
    closed_at: datetime | None = None

    # Signed account cash flow from every opening/adjusting transaction.
    # Positive = credit taken in. Includes fees.
    net_credit: Decimal = ZERO
    # Signed cash flow from closing transactions. Zero while fully open.
    closing_cash_flow: Decimal = ZERO
    fees: Decimal = ZERO

    order_ids: list[int] = field(default_factory=list)
    roll_count: int = 0

    # Context captured at entry, for the "what setups work" analytics.
    iv_rank_at_entry: Decimal | None = None
    underlying_price_at_entry: Decimal | None = None
    dte_at_entry: int | None = None
    short_delta_at_entry: Decimal | None = None
    buying_power_used: Decimal | None = None

    notes: str | None = None
    manual_group: bool = False
    # True when the position ended by assignment or exercise rather than by a
    # decision. Such a trade captured nothing; it was taken away.
    closed_by_assignment: bool = False
    # True when the trade was closed because its options had expired but no
    # closing transaction was ever found. The recorded cash flows may be an
    # incomplete picture -- an option that finished in the money was exercised
    # into something that is not in them -- so realized_pnl here is a lower
    # bound on what is known, not a settled result. Analytics excludes these
    # from totals rather than letting one guess move a year's figures.
    outcome_unverified: bool = False

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def realized_pnl(self) -> Decimal:
        """Cash actually banked. Meaningful once closed; partial while open."""
        return self.net_credit + self.closing_cash_flow

    @property
    def short_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.is_short and leg.is_option]

    @property
    def long_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if not leg.is_short and leg.is_option]

    @property
    def expirations(self) -> list[date]:
        return sorted({leg.expiration for leg in self.legs if leg.expiration})

    @property
    def is_multi_expiration(self) -> bool:
        return len(self.expirations) > 1

    def dte(self, today: date) -> int | None:
        """Days to the nearest expiration — the one that governs gamma risk."""
        exps = self.expirations
        return (exps[0] - today).days if exps else None

    @property
    def front_entry_dte(self) -> int | None:
        """How many days the nearest expiry had on it when it was put on.

        The 21-day rule is about getting out of the way before gamma bites, so
        it can only apply to an expiry that was ever outside the line. A call
        sold with eight days on it was never trying to be, and a diagonal's
        weekly is deliberately short-dated: measuring either from the day the
        whole strategy was opened marks it late on a rule it was never running.

        Measured from the legs at that expiry, so a roll into a new month is
        judged from the roll rather than from the original trade. Falls back to
        the strategy's own entry when the legs predate leg-level dates.
        """
        exps = self.expirations
        if not exps:
            return None
        front = exps[0]
        opened = [
            leg.opened_at for leg in self.legs if leg.expiration == front and leg.opened_at
        ]
        if opened:
            return (front - min(opened).date()).days
        if self.dte_at_entry is not None:
            return self.dte_at_entry
        return (front - self.opened_at.date()).days

    @property
    def net_position_delta(self) -> Decimal | None:
        deltas = [leg.position_delta for leg in self.legs]
        if any(d is None for d in deltas):
            return None
        return sum(deltas, ZERO)

    @property
    def net_theta(self) -> Decimal | None:
        if any(leg.theta is None for leg in self.legs if leg.is_option):
            return None
        return sum(
            (
                leg.theta * leg.notional_multiplier
                for leg in self.legs
                if leg.is_option and leg.theta is not None
            ),
            ZERO,
        )


@dataclass(slots=True)
class StrategyPnL:
    """Profit and loss for one strategy, always measured at the strategy level.

    ``pct_of_credit`` is the number a premium seller actually manages against:
    +1.0 means the full credit has been captured, -1.5 means the position is
    down 150% of the credit taken in.
    """

    net_credit: Decimal
    cost_to_close: Decimal | None
    open_pnl: Decimal | None
    pct_of_credit: Decimal | None
    max_profit: Decimal | None
    max_loss: Decimal | None  # None for undefined risk
    pct_of_max_profit: Decimal | None
    pct_of_max_loss: Decimal | None
    realized_pnl: Decimal
    # Realized result against the premium collected. None where there is no
    # premium to measure against, such as an outright futures contract.
    realized_pct_of_credit: Decimal | None = None
    is_credit: bool = False
    quoted_legs: int = 0
    total_legs: int = 0
    # What the trade settles into if the covered shorts are assigned and the
    # cover delivers. A diagonal has no honest max profit -- its legs expire on
    # different days -- but "if I am called away here, what do I walk away
    # with" is exact, and it is the question actually being asked while the
    # underlying climbs through the short strike.
    called_away: Decimal | None = None

    @property
    def fully_quoted(self) -> bool:
        return self.total_legs > 0 and self.quoted_legs == self.total_legs


@dataclass(slots=True)
class StrategyRisk:
    """Strategy-level danger assessment.

    Never built from a single leg's percentage move. See module docstring.
    """

    level: DangerLevel
    score: float
    reasons: list[RiskReason]

    dte: int | None = None
    worst_short_delta: Decimal | None = None
    distance_to_short_pct: Decimal | None = None
    # How many of those expected moves away the short strike is -- what the
    # statistician calls sigma. The scoring is built on it and it is the right
    # way to compare a /ZB strike with a biotech one, but the name went with
    # the word: "0.33 sigma" was a correct answer to a question nobody asked,
    # and the app says the move itself instead.
    short_strike_in_moves: Decimal | None = None
    # The same fact in money: what the market prices this underlying to move
    # between now and expiry. This is the one on screen.
    expected_move: Decimal | None = None
    breached: bool = False
    breached_side: str | None = None
    assignment_risk: bool = False
    pin_risk: bool = False
    pct_of_net_liq: Decimal | None = None


@dataclass(slots=True)
class UnderlyingQuote:
    symbol: str
    last: Decimal | None = None
    mark: Decimal | None = None
    iv: Decimal | None = None
    iv_rank: Decimal | None = None
    iv_percentile: Decimal | None = None
    earnings_date: date | None = None
    ex_dividend_date: date | None = None
    # Beta against SPY, from tastytrade's market metrics. None means unknown,
    # and unknown must never be silently read as 1.0.
    beta: Decimal | None = None


@dataclass(slots=True)
class PortfolioSummary:
    account_number: str
    net_liquidating_value: Decimal
    cash_balance: Decimal
    buying_power_used: Decimal
    buying_power_available: Decimal
    maintenance_requirement: Decimal
    open_strategies: int
    net_delta: Decimal | None = None
    net_theta: Decimal | None = None
    open_pnl: Decimal | None = None
    realized_pnl_ytd: Decimal | None = None
    as_of: datetime | None = None
    # The day, in two parts.
    #
    # ``day_change_open`` is the positions': each contract measured from its
    # own close price. It is what tastytrade prints as "P/L Day" and what the
    # P&L today column adds up to, so it is the headline here too.
    #
    # ``day_change`` is the account's: what it is worth now less what it closed
    # at last session. It is the wider number -- it carries what was closed
    # today, fees, settlement and any cash moved -- and it is reported beside
    # the first rather than instead of it, because the two answer different
    # questions and neither is the other's correction.
    day_change: Decimal | None = None
    day_change_open: Decimal | None = None
    day_change_of: int = 0
