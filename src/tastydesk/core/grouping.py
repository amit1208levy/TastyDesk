"""Rebuilding trades from a flat transaction history.

tastytrade hands back a ledger: one row per fill, per expiration, per
assignment, in time order, with nothing tying the four legs of an iron condor
to each other. A journal needs the opposite — the *trade*, from the moment it
was put on to the moment it came off, with one cumulative P&L. This module is
the bridge, and everything downstream (P&L, risk, the "which setups actually
work" analytics) reads what it produces.

Why the grouping has to be careful
----------------------------------
Three things go wrong in naive reconstructions, and all three lie to the user:

*Splitting one order into several trades.* An iron condor filled as four rows
becomes four "trades", three of which look like naked shorts. The risk engine
would then invent danger that the long wings already paid for. Opening fills
are therefore grouped by ``order_id``: one order is one strategy, full stop.

*Attaching a close to the wrong open.* Sell five puts on Monday across two
orders, buy three back on Friday, and the three have to come off the *oldest*
lots first, with the cash split the same way. Otherwise one strategy shows a
realized gain it never made while another still shows contracts that are gone.
Closes are allocated FIFO by open time and the cash is split proportionally,
with the last slice taking the remainder so the pennies always add back up.

*Letting an assignment discard the shares it delivered.* A short put that gets
assigned is not a trade that disappeared. It is two things: an option that
genuinely expired in the money with the premium kept, and 100 brand new shares
bought at the strike. Record only the first and a naked-put seller's journal
reports a **100% win rate** — every assigned put shows its full credit as a
win, and the entire loss lives in stock the journal never wrote down. Sell a
580 put for $198, get assigned, sell the stock for $49,995: the account is down
$7,807 and the journal says +$198. So the delivery row opens a *second*,
linked strategy carrying the shares at their own cost basis, and the later sale
of that stock closes it. Both halves are honest, and analytics counts the stock
outcome as the losing trade it is.

What ``Strategy.legs`` contains
-------------------------------
While a strategy is **open**, ``legs`` holds only what is still open, at the
quantity still open. This matters more than it looks: a leg that is gone has no
quote, and :func:`tastydesk.core.pnl.cost_to_close` returns ``None`` the moment
any leg is unquoted, so leaving a dead leg in the list would blank out the P&L
of a live position. Close half a strangle and the remaining naked put is what
you see — which is also the honest risk picture.

Once a strategy is **fully closed** the live view would be empty, so ``legs``
becomes the complete record of every leg the trade ever held, at the size it
was traded. ``strategy_type`` is whatever the structure was the last time it
had live legs, so a closed strangle still reads "Short Strangle" rather than
being reclassified from an empty list.

Sign conventions are inherited wholesale from the SDK: ``net_value`` and every
fee field arrive already signed (negative = cash out) and net of fees, so the
accumulation here is addition, never case analysis on buy versus sell.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from tastytrade.account import Transaction

from tastydesk.core.classify import classify
from tastydesk.core.models import (
    ZERO,
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    RollStep,
    Strategy,
    StrategyType,
)
from tastydesk.core.occ import parse_option_symbol

__all__ = ["build_strategies", "match_rolls"]

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")

# Transaction types. Only the first two carry legs; Money Movement never does.
_TRADE = "Trade"
_RECEIVE_DELIVER = "Receive Deliver"
_MONEY_MOVEMENT = "Money Movement"

# Receive Deliver sub-types that settle an option: the contract goes away.
_EXERCISE_SUB_TYPES = frozenset({"Assignment", "Exercise", "Expiration", "Cash Settled Assignment"})
# A position moved between the user's own accounts. The broker writes one row
# per account -- the side it leaves and the side it arrives -- both for zero
# cash, and both labelled "to Open" because each account is booking a new line.
# Ignoring them, which this did, left the position open for ever in the account
# it had left: it sat there until its expiry date, was closed as "expired" with
# no closing transaction to confirm it, and turned up in the unsettled list
# carrying the whole entry credit as a result that never happened.
_TRANSFER_SUB_TYPE = "Transfer"
# The two of those that settle *physically*, i.e. that owe somebody shares. A
# cash-settled index assignment pays the difference and delivers nothing, and an
# expiration out of the money delivers nothing either.
_PHYSICAL_SUB_TYPES = frozenset({"Assignment", "Exercise"})

_OPEN_SUB_TYPES = frozenset({"Buy to Open", "Sell to Open"})
_CLOSE_SUB_TYPES = frozenset({"Buy to Close", "Sell to Close"})

# The share-delivery rows that accompany an assignment or exercise arrive under
# Receive Deliver wearing an ordinary open/close sub-type ("Buy to Open" for the
# 100 shares a short put just bought you). They are real positions with a real
# cost basis and must be treated as such: the shares are NOT a cost of the
# option trade — folding -58,000 into a +198 put would invent a catastrophe that
# did not happen on that trade — but they are also not nothing, which is what
# dropping the row amounts to. They open their own strategy instead.
_DELIVERY_SUB_TYPES = _OPEN_SUB_TYPES | _CLOSE_SUB_TYPES

_BUY_ACTIONS = frozenset({"Buy to Open", "Buy to Close", "Buy"})
_SELL_ACTIONS = frozenset({"Sell to Open", "Sell to Close", "Sell"})

# Instrument types that are shares, not contracts: one unit is one unit.
_SHARE_INSTRUMENTS = frozenset({"Equity", "Cryptocurrency", "Index", "Warrant"})


def _enum_value(value: object) -> str:
    """The wire string behind an SDK enum, or "" when the field was absent."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _fee_total(transaction: Transaction) -> Decimal:
    """Every fee component on one row, already signed by the SDK (debits negative).

    Addition, never ``-abs(...)``: the SDK hands these over signed (a charge is
    negative, and a rebate — they exist — is positive), so flipping the sign
    here would double-negate every fee. Missing fields are ``None`` rather than
    zero on most rows, hence the guard: an expiration carries no commission at
    all, and index options carry a fee equity options never do. SPX and friends
    bill through ``proprietary_index_option_fees``, which is the whole reason
    that field is in this list; leaving it out understates the cost of every
    index trade the user makes.
    """
    total = ZERO
    for component in (
        transaction.regulatory_fees,
        transaction.clearing_fees,
        transaction.commission,
        transaction.proprietary_index_option_fees,
        transaction.other_charge,
    ):
        if component is not None:
            total += component
    return total


def _fmt(quantity: Decimal) -> str:
    """Render a Decimal without exponent or trailing-zero noise, for notes."""
    normalized = quantity.normalize()
    return f"{normalized:f}"


def _split_cash(total: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """Split ``total`` across ``weights`` so the parts add back up to it exactly.

    Decimal division does not always terminate (three contracts out of seven),
    so every slice but the last is rounded to the cent and the last one takes
    the remainder. A close that spans two strategies must not lose or invent a
    penny between them.
    """
    if not weights:
        return []
    denominator = sum(weights, ZERO)
    if denominator == ZERO:
        return [ZERO] * len(weights)
    parts: list[Decimal] = []
    running = ZERO
    for weight in weights[:-1]:
        part = (total * weight / denominator).quantize(_CENT, rounding=ROUND_HALF_UP)
        parts.append(part)
        running += part
    parts.append(total - running)
    return parts


def _is_leg_row(transaction: Transaction) -> bool:
    """True when this row opens or closes an actual position.

    Under Receive Deliver that means both halves of an assignment: the row that
    settles the option, and the row that hands over the shares. The second one
    used to be discarded, which is how an assigned naked put came to look like a
    pure winner.
    """
    if transaction.transaction_type == _TRADE:
        return True
    if transaction.transaction_type != _RECEIVE_DELIVER:
        return False
    return (
        transaction.transaction_sub_type in _EXERCISE_SUB_TYPES
        or transaction.transaction_sub_type in _DELIVERY_SUB_TYPES
        or (transaction.transaction_sub_type == _TRANSFER_SUB_TYPE and bool(transaction.symbol))
    )


@dataclass(slots=True)
class _Position:
    """One contract line inside one strategy, plus how much of it is still open.

    ``leg.quantity`` is the *remaining* open size and shrinks as closes land;
    ``traded_quantity`` never shrinks, because the journal has to remember that
    five contracts were sold even after all five are gone.
    """

    leg: Leg
    traded_quantity: Decimal
    opened_at: datetime
    sequence: int
    owner: _Build

    @property
    def remaining(self) -> Decimal:
        return self.leg.quantity


@dataclass(slots=True)
class _PendingDelivery:
    """An assignment or exercise that has settled an option and now owes shares.

    The two rows are separate ledger entries with no field tying them together,
    so the option side is parked here for the moment the share row shows up.
    Matching is by underlying and settlement date, which is as tight as the data
    allows and tight enough: two assignments of the same underlying on the same
    day are matched oldest-first, and the only thing at stake in a mismatch is
    the wording of a note, never a number.
    """

    build: _Build
    leg: Leg
    contracts: Decimal
    sub_type: str
    on: date


@dataclass(slots=True)
class _Build:
    """A strategy under construction, mutated as later transactions land on it."""

    id: str
    account_number: str
    underlying: str
    opened_at: datetime
    positions: list[_Position] = field(default_factory=list)
    net_credit: Decimal = ZERO
    closing_cash_flow: Decimal = ZERO
    fees: Decimal = ZERO
    order_ids: list[int] = field(default_factory=list)
    closed_at: datetime | None = None
    notes: list[str] = field(default_factory=list)
    # order id -> the legs that order closed, as they read on the page. The
    # ledger is the only place that knows this: by the time the strategies are
    # merged, a leg that was bought back is simply gone.
    closed_by_order: dict[int, list[str]] = field(default_factory=dict)
    # The last structure this trade was seen to be while it had live legs.
    strategy_type: StrategyType = StrategyType.CUSTOM
    risk_profile: RiskProfile = RiskProfile.UNDEFINED
    closed_by_assignment: bool = False

    @property
    def live_legs(self) -> list[Leg]:
        return [p.leg for p in self.positions if p.remaining > ZERO]

    def traded_legs(self) -> list[Leg]:
        """Every leg ever held, restored to the size it was actually traded at."""
        return [replace(p.leg, quantity=p.traded_quantity) for p in self.positions]

    def note(self, text: str) -> None:
        self.notes.append(text)

    def touch_order(self, order_id: int | None) -> None:
        if order_id is not None and order_id not in self.order_ids:
            self.order_ids.append(order_id)


class _Reconstructor:
    """One pass over one account's history. Deliberately not reusable."""

    def __init__(self, account_number: str) -> None:
        self.account_number = account_number
        self.builds: list[_Build] = []
        self.by_id: dict[str, _Build] = {}
        # Open lots per option/share symbol, appended in open order so the FIFO
        # walk below is already chronological.
        self.lots: dict[str, list[_Position]] = {}
        # Assignments/exercises waiting for their share-delivery row, keyed by
        # underlying. Consumed as the deliveries land, purely to link the two
        # strategies together in the notes.
        self.pending_deliveries: dict[str, list[_PendingDelivery]] = {}
        self.sequence = 0

    # ---------------------------------------------------------------- driving

    def run(self, transactions: Sequence[Transaction]) -> list[_Build]:
        rows = self._for_this_account(transactions)
        leg_rows = [t for t in rows if _is_leg_row(t)]
        cash_rows = [t for t in rows if t.transaction_type == _MONEY_MOVEMENT]
        ignored = len(rows) - len(leg_rows) - len(cash_rows)
        if ignored:
            # Corporate actions (symbol changes, splits) and administrative rows
            # land here. Share deliveries no longer do — see _is_leg_row.
            logger.debug("grouping: %d row(s) carry neither a leg nor position cash", ignored)

        for batch in self._batches(leg_rows):
            self._apply_batch(batch)
        self._attach_position_cash(cash_rows)
        return self.builds

    def _for_this_account(self, transactions: Sequence[Transaction]) -> list[Transaction]:
        kept: list[Transaction] = []
        stray = 0
        for transaction in transactions:
            if transaction.account_number != self.account_number:
                stray += 1
                continue
            kept.append(transaction)
        if stray:
            logger.warning(
                "grouping: ignoring %d transaction(s) belonging to another account than %s",
                stray,
                self.account_number,
            )
        return kept

    def _batches(self, leg_rows: list[Transaction]) -> list[list[Transaction]]:
        """One batch per order, so a multi-leg fill is handled as a single event.

        Rows without an order id (expirations, assignments) are their own batch;
        they only ever close, and each one names the contract it closes.
        """
        batches: dict[tuple[str, int], list[Transaction]] = {}
        for transaction in sorted(leg_rows, key=lambda t: (t.executed_at, t.id)):
            order_id = transaction.order_id
            key = ("order", order_id) if order_id is not None else ("row", transaction.id)
            batches.setdefault(key, []).append(transaction)
        return sorted(
            batches.values(),
            key=lambda batch: (min(r.executed_at for r in batch), min(r.id for r in batch)),
        )

    def _apply_batch(self, batch: list[Transaction]) -> None:
        # Intent is decided against the lot state *before* this batch, so a roll
        # order's closing legs are seen as closes even though the same order
        # opens new ones a microsecond later.
        closers = [row for row in batch if self._closes(row)]
        openers = [row for row in batch if row not in closers]

        self._check_leg_count(batch)
        # Which builds this order closed into, and when their last closing row
        # landed. Collected rather than settled row by row: see _settle_closes.
        touched: dict[str, tuple[_Build, datetime]] = {}
        for row in closers:
            self._apply_close(row, touched)
        if openers:
            self._apply_open(openers)
        self._settle_closes(touched)

    def _settle_closes(self, touched: dict[str, tuple[_Build, datetime]]) -> None:
        """Re-name and close out the builds this order touched, once per order.

        Deliberately not done per row. Closing a strangle with a single two-leg
        order arrives as two rows, and after the first one the position really
        is a lone short call — but only for the microsecond between two fills of
        the same order, which is not a state the trade was ever managed in.
        Naming it there would leave a closed strangle recorded as "Naked Call".
        Settling once per order means the only names we record are the ones the
        position actually rested in.
        """
        for build, last_close in touched.values():
            if all(p.remaining <= ZERO for p in build.positions):
                # Rule 6: closed when every leg is flat, stamped with the final
                # closing transaction of the order that flattened it.
                build.closed_at = last_close
            else:
                self._reclassify(build)

    def _check_leg_count(self, batch: list[Transaction]) -> None:
        """Warn when the broker's own leg count disagrees with what we grouped.

        A mismatch means either a partial fill we have not seen the rest of, or
        a grouping bug. Either way the user's iron condor may be about to be
        displayed as something with a different risk profile, so say so.
        """
        expected = {row.leg_count for row in batch if row.leg_count is not None}
        if not expected:
            return
        symbols = {row.symbol for row in batch if row.symbol}
        if len(expected) > 1 or max(expected) != len(symbols):
            logger.warning(
                "grouping: order %s reports leg-count %s but %d distinct symbol(s) were grouped",
                batch[0].order_id,
                sorted(expected),
                len(symbols),
            )

    # ---------------------------------------------------------------- opening

    def _apply_open(self, openers: list[Transaction]) -> None:
        by_underlying: dict[str, list[Transaction]] = {}
        for row in openers:
            by_underlying.setdefault(_underlying_of(row), []).append(row)
        if len(by_underlying) > 1:
            # One order across two underlyings is not a structure; splitting it
            # keeps each side classifiable instead of yielding one CUSTOM blob.
            logger.warning(
                "grouping: order %s opens %d underlyings; splitting into one strategy each",
                openers[0].order_id,
                len(by_underlying),
            )
        for underlying, rows in by_underlying.items():
            self._open_one(underlying, rows)

    def _open_one(self, underlying: str, rows: list[Transaction]) -> None:
        first = rows[0]
        # Deterministic by construction: account, underlying and the id of the
        # order that opened it. Rebuilding the same history always yields the
        # same ids, which is what lets a resync update rows instead of
        # duplicating every trade the user has ever made.
        tag = str(first.order_id) if first.order_id is not None else f"t{first.id}"
        strategy_id = f"{self.account_number}:{underlying}:{tag}"

        build = self.by_id.get(strategy_id)
        if build is None:
            build = _Build(
                id=strategy_id,
                account_number=self.account_number,
                underlying=underlying,
                opened_at=min(row.executed_at for row in rows),
            )
            self.by_id[strategy_id] = build
            self.builds.append(build)

        for row in rows:
            self._add_leg(build, row)
            build.net_credit += row.net_value
            build.fees += _fee_total(row)
            build.touch_order(row.order_id)
            if row.transaction_sub_type == _TRANSFER_SUB_TYPE:
                build.note(
                    f"Transferred in from another of your accounts on "
                    f"{row.executed_at:%b %-d, %Y}, at no cost here. What it cost "
                    "to put on is recorded in the account it came from."
                )
            elif row.transaction_type == _RECEIVE_DELIVER:
                # Shares handed over by an assignment or exercise. They open here
                # at the strike, as their own position; the note says where they
                # came from so the UI can show the pair as one event.
                self._link_delivery(build, row)
        self._reclassify(build)

    def _add_leg(self, build: _Build, row: Transaction) -> None:
        quantity = abs(row.quantity) if row.quantity is not None else ZERO
        if quantity == ZERO:
            logger.warning("grouping: opening transaction %s has no quantity; skipped", row.id)
            return
        direction = _direction_of(row)
        symbol = row.symbol or ""
        multiplier = _multiplier_of(row)
        price = _open_price(row, quantity, multiplier)

        existing = next(
            (p for p in build.positions if p.leg.symbol == symbol and p.leg.direction is direction),
            None,
        )
        if existing is not None:
            # A partially filled leg arrives as several rows on one order. Average
            # the price by size so the recorded entry matches the cash actually paid.
            total = existing.traded_quantity + quantity
            blended = (existing.leg.open_price * existing.traded_quantity + price * quantity) / total
            existing.leg.open_price = blended
            existing.leg.quantity += quantity
            # Adding to a position does not restart its clock: the 21-day rule
            # reads from when the expiry was first put on.
            if existing.leg.opened_at is None or row.executed_at < existing.leg.opened_at:
                existing.leg.opened_at = row.executed_at
            existing.traded_quantity = total
            return

        parsed = parse_option_symbol(symbol)
        leg = Leg(
            symbol=symbol,
            instrument_type=_enum_value(row.instrument_type) or "Equity Option",
            underlying=_underlying_of(row),
            direction=direction,
            quantity=quantity,
            multiplier=multiplier,
            option_type=(OptionType.CALL if parsed.is_call else OptionType.PUT) if parsed else None,
            strike=parsed.strike if parsed else None,
            expiration=parsed.expiration if parsed else None,
            open_price=price,
            opened_at=row.executed_at,
        )
        self.sequence += 1
        position = _Position(
            leg=leg,
            traded_quantity=quantity,
            opened_at=row.executed_at,
            sequence=self.sequence,
            owner=build,
        )
        build.positions.append(position)
        self.lots.setdefault(symbol, []).append(position)

    # ---------------------------------------------------------------- closing

    def _closes(self, row: Transaction) -> bool:
        sub_type = row.transaction_sub_type
        # A transfer is a close in the account the position leaves and an open
        # in the one it arrives at, and the only way to tell which is which is
        # to look: the leaving side offsets something already held. The row's
        # own label says "to Open" on both sides, because each account is
        # booking its own new line, so it cannot be trusted here.
        if sub_type == _TRANSFER_SUB_TYPE:
            wanted = _closing_direction(row)
            if wanted is None:
                return False
            lots = self.lots.get(row.symbol or "", [])
            return any(p.remaining > ZERO and p.leg.direction is wanted for p in lots)
        if sub_type in _OPEN_SUB_TYPES:
            return False
        if sub_type in _CLOSE_SUB_TYPES or sub_type in _EXERCISE_SUB_TYPES:
            return True
        action = _enum_value(row.action)
        if action in _OPEN_SUB_TYPES:
            return False
        if action in _CLOSE_SUB_TYPES:
            return True
        # A bare Buy/Sell (equities often report these) only closes if there is
        # something of the opposite direction open to close.
        wanted = _closing_direction(row)
        if wanted is None:
            return False
        lots = self.lots.get(row.symbol or "", [])
        return any(p.remaining > ZERO and p.leg.direction is wanted for p in lots)

    def _apply_close(self, row: Transaction, touched: dict[str, tuple[_Build, datetime]]) -> None:
        symbol = row.symbol or ""
        wanted = _closing_direction(row)
        candidates = [
            p
            for p in self.lots.get(symbol, [])
            if p.remaining > ZERO and (wanted is None or p.leg.direction is wanted)
        ]
        candidates.sort(key=lambda p: (p.opened_at, p.sequence))
        available = sum((p.remaining for p in candidates), ZERO)
        if available == ZERO:
            # The open happened before the window of history we were given. Say
            # so rather than inventing a strategy to hang the cash on.
            logger.warning(
                "grouping: closing transaction %s on %s matches no open position; cash not attributed",
                row.id,
                symbol or "<no symbol>",
            )
            return

        # Expirations frequently omit the quantity: everything open expires.
        stated = abs(row.quantity) if row.quantity is not None else available
        wanted_quantity = min(stated, available)
        if wanted_quantity < stated:
            logger.warning(
                "grouping: closing transaction %s wants %s of %s but only %s is open",
                row.id,
                _fmt(stated),
                symbol,
                _fmt(available),
            )

        taken: list[tuple[_Position, Decimal]] = []
        outstanding = wanted_quantity
        for position in candidates:
            if outstanding <= ZERO:
                break
            take = min(outstanding, position.remaining)
            taken.append((position, take))
            outstanding -= take

        weights = [take for _position, take in taken]
        # Only the matched fraction of the row's cash is attributed; the rest
        # belongs to an open we never saw and was warned about above.
        matched_share = wanted_quantity / stated if stated else ZERO
        cash_parts = _split_cash(_scale(row.net_value, matched_share), weights)
        fee_parts = _split_cash(_scale(_fee_total(row), matched_share), weights)

        for (position, take), cash, fee in zip(taken, cash_parts, fee_parts, strict=True):
            build = position.owner
            position.leg.quantity -= take
            build.closing_cash_flow += cash
            build.fees += fee
            build.touch_order(row.order_id)
            if row.order_id is not None:
                build.closed_by_order.setdefault(row.order_id, []).append(
                    _leg_line(replace(position.leg, quantity=take))
                )
            if row.transaction_sub_type == _TRANSFER_SUB_TYPE:
                # The cash says zero because none moved. Without a word here,
                # this side reads as a trade that gave back nothing and the
                # other side as one that cost nothing, and neither is true on
                # its own -- only the pair is.
                build.note(
                    f"{_fmt(take)} moved to another of your accounts on "
                    f"{row.executed_at:%b %-d, %Y}. No cash changed hands here; "
                    "the rest of this position's life is recorded in that account."
                )
            elif row.transaction_sub_type in _EXERCISE_SUB_TYPES:
                build.note(_exercise_note(row, position.leg, take))
                self._remember_delivery(row, build, position.leg, take)
            previous = touched.get(build.id)
            when = max(previous[1], row.executed_at) if previous else row.executed_at
            touched[build.id] = (build, when)

        if (
            taken
            and row.transaction_type == _RECEIVE_DELIVER
            and row.transaction_sub_type in _DELIVERY_SUB_TYPES
        ):
            # Shares delivered *away* — a covered call called away, or a long put
            # exercised — close stock the journal already holds. That cash is a
            # closing cash flow of the strategy holding the shares, which is what
            # the loop above just recorded; all that is left is the paper trail.
            self._link_delivery(taken[0][0].owner, row)

    # ------------------------------------------------------------- deliveries

    def _remember_delivery(
        self,
        row: Transaction,
        build: _Build,
        leg: Leg,
        contracts: Decimal,
    ) -> None:
        """Park a physically settled option so its share row can find it."""
        if row.transaction_sub_type not in _PHYSICAL_SUB_TYPES or leg.option_type is None:
            return
        # The trade did not capture its credit; the position was taken away.
        # analytics.max_profit_at_close reads this so the assigned put stops
        # counting as a textbook full-credit capture.
        build.closed_by_assignment = True
        self.pending_deliveries.setdefault(_underlying_of(row), []).append(
            _PendingDelivery(
                build=build,
                leg=leg,
                contracts=contracts,
                sub_type=row.transaction_sub_type,
                on=row.transaction_date,
            )
        )

    def _take_delivery(self, row: Transaction) -> _PendingDelivery | None:
        """The assignment this share row settles, if we saw it. Oldest first."""
        queue = self.pending_deliveries.get(_underlying_of(row), [])
        for index, pending in enumerate(queue):
            if pending.on == row.transaction_date:
                return queue.pop(index)
        return None

    def _link_delivery(self, build: _Build, row: Transaction) -> None:
        """Cross-reference the option trade and the stock position it created.

        Nothing about the money depends on this; the cash is already where it
        belongs. What it buys is the answer to "why do I suddenly own 100 SPY?",
        months later, from either end of the pair. Both ids are deterministic —
        they are built from the account, underlying and the id of the order or
        row that opened each side — so the link survives a full resync.
        """
        when = row.transaction_date.isoformat()
        size = _fmt(abs(row.quantity)) if row.quantity is not None else "?"
        symbol = (row.symbol or "").strip()
        pending = self._take_delivery(row)
        if pending is None:
            # A delivery whose option was opened before our window of history.
            # Still a real position; we just cannot name what created it.
            build.note(f"{when} {row.transaction_sub_type.lower()} by delivery: {size}x {symbol}")
            return

        build.note(
            f"{when} {size}x {symbol} delivered by {pending.sub_type.lower()} of "
            f"{pending.leg.symbol.strip()} (strategy {pending.build.id})"
        )
        if pending.build is not build:
            # A covered call called away settles into the very strategy that held
            # the shares, and noting it twice there would just be noise.
            pending.build.note(f"{when} delivered {size}x {symbol} into strategy {build.id}")

    # ------------------------------------------------------------ bookkeeping

    def _reclassify(self, build: _Build) -> None:
        """Re-name the structure from what is still open.

        Closing the call side of a strangle really does leave a naked put, and
        the risk engine should see a naked put. When nothing is left open we
        keep the last name instead of reclassifying an empty list into CUSTOM.
        """
        live = build.live_legs
        if not live:
            return
        build.strategy_type, build.risk_profile = classify(live)

    def _attach_position_cash(self, cash_rows: list[Transaction]) -> None:
        """Fold dividends and symbol-tied fees into the trade that earned them.

        These rows carry no leg, so they never move a quantity, but a dividend
        paid on the shares inside a covered call is part of that trade's cash
        and dropping it would understate the result. Anything we cannot pin to
        exactly one strategy that was open at the time is account-level cash and
        is reported rather than guessed at.
        """
        for row in cash_rows:
            underlying = row.underlying_symbol
            if not underlying:
                logger.debug(
                    "grouping: %s row %s has no underlying; left at account level",
                    row.transaction_sub_type,
                    row.id,
                )
                continue
            candidates = [
                b
                for b in self.builds
                if b.underlying == underlying.strip().upper()
                and b.opened_at <= row.executed_at
                and (b.closed_at is None or row.executed_at <= b.closed_at)
            ]
            if len(candidates) != 1:
                logger.warning(
                    "grouping: %s of %s on %s matches %d open strategies; left at account level",
                    row.transaction_sub_type,
                    row.net_value,
                    underlying,
                    len(candidates),
                )
                continue
            build = candidates[0]
            build.net_credit += row.net_value
            fee = _fee_total(row)
            if fee == ZERO and row.transaction_sub_type == "Fee":
                # A fee row keeps the whole charge in net_value; the itemised
                # fee fields are empty on it.
                fee = row.net_value
            build.fees += fee
            build.note(f"{row.transaction_date.isoformat()} {row.transaction_sub_type}: {row.net_value}")


def _scale(amount: Decimal, share: Decimal) -> Decimal:
    if share >= Decimal(1):
        return amount
    return (amount * share).quantize(_CENT, rounding=ROUND_HALF_UP)


def _underlying_of(row: Transaction) -> str:
    if row.underlying_symbol:
        return row.underlying_symbol.strip().upper()
    parsed = parse_option_symbol(row.symbol)
    if parsed:
        return parsed.root.strip().upper()
    return (row.symbol or "").strip().upper()


# Multipliers a derived figure is allowed to snap to. Futures options are the
# reason this exists at all: /ES is 50, /MES is 5, /CL is 1000, and an equity
# option's 100 is only the most common case, not the rule. A strangle on /ES
# priced as though it were 100 would overstate the notional twofold, which is
# exactly the kind of quietly wrong number this application is meant to avoid.
_KNOWN_MULTIPLIERS = (
    Decimal(1),
    Decimal(5),
    Decimal(10),
    Decimal(20),
    Decimal(50),
    Decimal(100),
    Decimal(250),
    Decimal(500),
    Decimal(1000),
)

# How far a derived multiplier may sit from a known one and still be trusted.
# Fills are reported to the cent, so a real multiplier lands within a whisker;
# anything looser is a sign the arithmetic did not mean what we assumed.
_MULTIPLIER_TOLERANCE = Decimal("0.02")


def _derived_multiplier(row: Transaction) -> Decimal | None:
    """Back the multiplier out of the fill: |value| = price x quantity x multiplier.

    tastytrade does not carry the contract multiplier on a transaction, but it
    carries both sides of the identity that defines it, so for any ordinary fill
    the number is recoverable exactly rather than assumed.

    Returns None when the row cannot speak to it — an expiration at zero, an
    assignment with no price, a quantity of zero.
    """
    price = row.price
    quantity = row.quantity
    if price is None or quantity is None:
        return None
    if abs(price) == ZERO or abs(quantity) == ZERO:
        return None

    try:
        derived = abs(row.value) / (abs(price) * abs(quantity))
    except (InvalidOperation, ZeroDivisionError):
        return None

    for candidate in _KNOWN_MULTIPLIERS:
        if abs(derived - candidate) <= _MULTIPLIER_TOLERANCE:
            return candidate
    return None


def _multiplier_of(row: Transaction) -> Decimal:
    """Units per contract.

    Derived from the fill where the arithmetic allows, because a futures option's
    multiplier is contract-specific and guessing 100 would misstate every /ES or
    /MES position. Falls back to the conventional values only when the row cannot
    settle the question itself.
    """
    derived = _derived_multiplier(row)
    if derived is not None:
        return derived

    if parse_option_symbol(row.symbol) and _enum_value(row.instrument_type) != "Equity":
        return Decimal(100)
    if _enum_value(row.instrument_type) in _SHARE_INSTRUMENTS:
        return Decimal(1)
    return Decimal(100) if parse_option_symbol(row.symbol) else Decimal(1)


def _open_price(row: Transaction, quantity: Decimal, multiplier: Decimal) -> Decimal:
    """The quoted price per unit at entry, always positive.

    ``price`` is what the SDK reports per contract; when it is missing we back
    it out of the gross value, which is signed, hence the abs(). Direction lives
    in :attr:`Leg.direction` and must never leak into a price.
    """
    if row.price is not None:
        return abs(row.price)
    units = quantity * multiplier
    if units == ZERO:
        return ZERO
    return abs(row.value) / units


def _direction_of(row: Transaction) -> Direction:
    action = _enum_value(row.action) or row.transaction_sub_type
    return Direction.SHORT if action in _SELL_ACTIONS else Direction.LONG


def _closing_direction(row: Transaction) -> Direction | None:
    """Which side this row buys back, or None when the row does not say.

    Expirations and assignments carry no action: whatever is open on that
    contract is what goes away, and there is only ever one net side open.
    """
    action = _enum_value(row.action) or row.transaction_sub_type
    if action in _BUY_ACTIONS:
        return Direction.SHORT
    if action in _SELL_ACTIONS:
        return Direction.LONG
    return None


def _exercise_note(row: Transaction, leg: Leg, contracts: Decimal) -> str:
    """Say in words what an expiration or assignment did to the position.

    An assigned short put is not a trade that evaporated: it is a closed option
    and a new block of long stock, and the journal has to be able to answer
    "where did it go?" months later.
    """
    when = row.transaction_date.isoformat()
    label = f"{leg.direction.value.lower()} {_fmt(contracts)}x {leg.symbol.strip()}"
    sub_type = row.transaction_sub_type
    if sub_type == "Expiration":
        return f"{when} expired: {label}"
    if sub_type == "Cash Settled Assignment":
        return f"{when} cash settled: {label}"
    if leg.option_type is None:
        return f"{when} {sub_type.lower()}: {label}"
    # Calls deliver stock in the direction of the option; puts deliver it the
    # other way. Short put assigned -> long shares; short call assigned -> short.
    sign = Decimal(1) if leg.option_type is OptionType.CALL else Decimal(-1)
    shares = (-contracts if leg.is_short else contracts) * leg.multiplier * sign
    signed = f"{'+' if shares > ZERO else ''}{_fmt(shares)}"
    strike = f" at {_fmt(leg.strike)}" if leg.strike is not None else ""
    return f"{when} {sub_type.lower()}: {label} -> {signed} shares of {leg.underlying}{strike}"


def _finish(build: _Build) -> Strategy:
    """Freeze a build into the domain object the rest of the app consumes."""
    closed = build.closed_at is not None
    legs = build.traded_legs() if closed else [replace(p.leg) for p in build.positions if p.remaining > ZERO]
    expirations = sorted({leg.expiration for leg in legs if leg.expiration})
    dte_at_entry = (expirations[0] - build.opened_at.date()).days if expirations else None

    strategy_type, risk_profile = build.strategy_type, build.risk_profile
    if closed:
        # A finished trade is named by what it was traded as, not by whichever
        # side happened to be unwound last. Close the call of a strangle in
        # January and the put in March and the live name really did pass through
        # "Naked Put", but the trade in the journal is a short strangle. The
        # traded legs only get the last word when they still form a structure we
        # recognise; a rolled chain nets out to something unnameable, and there
        # the last live name is the better answer.
        traded_type, traded_risk = classify(legs)
        if traded_type is not StrategyType.CUSTOM:
            strategy_type, risk_profile = traded_type, traded_risk

    return Strategy(
        id=build.id,
        account_number=build.account_number,
        underlying=build.underlying,
        strategy_type=strategy_type,
        risk_profile=risk_profile,
        legs=legs,
        opened_at=build.opened_at,
        closed_at=build.closed_at,
        net_credit=build.net_credit,
        closing_cash_flow=build.closing_cash_flow,
        fees=build.fees,
        order_ids=list(build.order_ids),
        roll_count=0,
        dte_at_entry=dte_at_entry,
        notes="\n".join(build.notes) if build.notes else None,
        closed_by_assignment=build.closed_by_assignment,
        closed_by_order=dict(build.closed_by_order),
    )


def build_strategies(
    transactions: Sequence[Transaction],
    account_number: str,
    manual_overrides: Mapping[str, str] | None = None,
) -> list[Strategy]:
    """Rebuild every trade in ``transactions`` for one account.

    ``manual_overrides`` maps a strategy id to a group id and is applied last,
    after all the heuristics have had their say: strategies sharing a group id
    are merged into one trade carrying that id, for the cases where the broker's
    order ids do not match how the user actually thinks about the position.
    """
    reconstructor = _Reconstructor(account_number)
    builds = reconstructor.run(transactions)
    strategies = [_finish(build) for build in builds]
    strategies.sort(key=lambda s: (s.opened_at, s.id))
    if manual_overrides:
        strategies = _apply_overrides(strategies, manual_overrides)
    return strategies


def _apply_overrides(strategies: list[Strategy], overrides: Mapping[str, str]) -> list[Strategy]:
    groups: dict[str, list[Strategy]] = {}
    survivors: list[Strategy] = []
    for strategy in strategies:
        group = overrides.get(strategy.id)
        if group is None:
            survivors.append(strategy)
            continue
        groups.setdefault(group, []).append(strategy)

    for group_id, members in groups.items():
        members.sort(key=lambda s: (s.opened_at, s.id))
        merged = members[0]
        for other in members[1:]:
            merged = _merge(merged, other, is_roll=False)
        merged.id = group_id
        # The user said these belong together; match_rolls must not second-guess it.
        merged.manual_group = True
        survivors.append(merged)

    survivors.sort(key=lambda s: (s.opened_at, s.id))
    return survivors


def _without(these: list[str], those: list[str]) -> list[str]:
    """``these`` minus ``those``, counting duplicates rather than sets.

    Two identical short calls are two legs, and a roll that closed one of them
    closed one of them.
    """
    left = list(those)
    out: list[str] = []
    for line in these:
        if line in left:
            left.remove(line)
        else:
            out.append(line)
    return out


def _merge(into: Strategy, other: Strategy, *, is_roll: bool) -> Strategy:
    """Fold ``other`` into ``into`` so the pair reads as one cumulative trade."""
    # What the roll changed, read as the difference between the legs before it
    # and the legs after. Taking "closed" to be the parent's whole leg list
    # made every later roll claim to have closed everything that came before
    # it; the difference names only what actually went out at that step.
    before = [_leg_line(leg) for leg in into.legs]
    into.net_credit += other.net_credit
    into.closing_cash_flow += other.closing_cash_flow
    into.fees += other.fees
    for order_id in other.order_ids:
        if order_id not in into.order_ids:
            into.order_ids.append(order_id)
    into.roll_count += other.roll_count + (1 if is_roll else 0)
    into.opened_at = min(into.opened_at, other.opened_at)

    both_closed = into.closed_at is not None and other.closed_at is not None
    if both_closed:
        # Two closed records join into the full leg history of the trade.
        into.legs = [*into.legs, *other.legs]
        into.closed_at = max(into.closed_at, other.closed_at)  # type: ignore[type-var]
    else:
        # The merged trade is still open, so only legs from the side that is
        # still open may go in the list. This is the normal shape of a roll:
        # the old strangle was closed by the very order that opened the new one,
        # so ``into`` is the closed side and every one of its legs is dead. They
        # would do two kinds of damage if they stayed. They cannot be quoted, and
        # pnl.cost_to_close returns None the moment one leg is unquoted, so a
        # perfectly live position would show no P&L at all; and classify() would
        # see four strikes across two expirations and give up on naming a trade
        # that is plainly a short strangle. The dead legs survive as a note,
        # which is where the history belongs once the position has moved on.
        live: list[Leg] = []
        retired: list[Leg] = []
        for source in (into, other):
            (retired if source.closed_at is not None else live).extend(source.legs)
        into.legs = live
        gone = ", ".join(
            f"{leg.direction.value.lower()} {_fmt(leg.quantity)}x {leg.symbol.strip()}" for leg in retired
        )
        if gone:
            _append_note(into, f"closed leg(s) rolled out of: {gone}")
        into.closed_at = None

    if other.notes:
        _append_note(into, other.notes)
    # A roll of a roll keeps the earlier steps: the chain is the history.
    into.rolls = [*into.rolls, *other.rolls]
    for order, lines in other.closed_by_order.items():
        into.closed_by_order.setdefault(order, []).extend(lines)
    if is_roll:
        after = [_leg_line(leg) for leg in into.legs]
        order_id = other.order_ids[0] if other.order_ids else None
        # What the roll order closed, from the ledger. The difference between
        # the legs before and after is the fallback: while both sides of a roll
        # are open the leg lists overlap, and the difference then reports an
        # empty roll.
        closed = list(into.closed_by_order.get(order_id, [])) if order_id is not None else []
        into.rolls.append(
            RollStep(
                at=other.opened_at,
                absorbed_id=other.id,
                closed=closed or _without(before, after),
                opened=[_leg_line(leg) for leg in other.legs] or _without(after, before),
                credit=other.net_credit,
                order_id=order_id,
            )
        )
        into.rolls.sort(key=lambda step: step.at)
        _append_note(into, f"rolled: absorbed {other.id} (roll #{into.roll_count})")

    if into.closed_at is None:
        into.strategy_type, into.risk_profile = classify(into.legs)
    return into


def _append_note(strategy: Strategy, text: str) -> None:
    strategy.notes = f"{strategy.notes}\n{text}" if strategy.notes else text


def match_rolls(
    strategies: list[Strategy],
    separated: frozenset[tuple[str, str]] = frozenset(),
) -> list[Strategy]:
    """Merge rolls into the strategy they rolled out of.

    A roll is one order that closes legs of an open strategy and opens new legs
    in the same underlying — further out in time, at different strikes, or both.
    :func:`build_strategies` cannot see that while it walks the ledger, because
    at the moment of the roll the new legs are simply an opening order. Here the
    finished strategies are matched up: the new strategy's *opening* order id
    also appears in the older strategy's order list, which it only can if that
    order closed something of the older strategy's.

    Merging is the whole point. A strangle rolled four times is one trade with
    one cumulative credit and one P&L; reported as five trades it would show
    four tidy winners and one loser, which is exactly the illusion that makes
    rolling look better than it is. The user wants to know whether rolling
    actually helps, and only the merged number can answer that.

    ``opened_at`` stays at the original entry, so time-in-trade counts from when
    the risk was first taken on.

    ``separated`` holds the pairs the user has said are not one trade, as
    (parent id, absorbed id). The app reads a roll off the order ids and is
    usually right, but "usually" is not a thing to be stuck with: a pair named
    here is left as two trades, and stays that way through every rebuild.
    """
    working = [_copy(strategy) for strategy in strategies]
    by_id = {strategy.id: strategy for strategy in working}

    # Which strategies each order touched, from the pre-merge record.
    touched_by: dict[int, list[str]] = {}
    for strategy in working:
        for order_id in strategy.order_ids:
            touched_by.setdefault(order_id, []).append(strategy.id)

    absorbed: dict[str, str] = {}
    for candidate in sorted(working, key=lambda s: (s.opened_at, s.id)):
        if candidate.manual_group or not candidate.order_ids:
            continue
        opening_order = candidate.order_ids[0]
        parents = [
            by_id[other_id]
            for other_id in touched_by.get(opening_order, [])
            if other_id != candidate.id
            and other_id not in absorbed
            and by_id[other_id].order_ids[:1] != [opening_order]
        ]
        # A manually grouped strategy can still absorb a roll. Being told
        # "these trades are one position" is not being told "this position can
        # never be rolled again": when one order buys back a leg of it and
        # sells a replacement, the replacement belongs to it. Refusing left the
        # group a leg short -- still called a strangle, still being told to
        # take profit on a call that had been bought back -- while the new leg
        # sat beside it as an unrelated naked call. What is still protected is
        # the candidate: a strategy the user put in a group of its own is never
        # swallowed into another.
        parents = [
            parent
            for parent in parents
            if parent.underlying == candidate.underlying
            and parent.account_number == candidate.account_number
            and parent.opened_at <= candidate.opened_at
        ]
        if not parents:
            continue
        if len(parents) > 1:
            logger.warning(
                "grouping: order %s closed legs of %d strategies; rolling into the oldest",
                opening_order,
                len(parents),
            )
        parent = min(parents, key=lambda s: (s.opened_at, s.id))
        if (parent.id, candidate.id) in separated:
            continue
        _merge(parent, candidate, is_roll=True)
        absorbed[candidate.id] = parent.id
        # Later rolls in the chain look the absorbed strategy up and land on the
        # survivor, so a strangle rolled four times ends as one trade.
        for order_id in candidate.order_ids:
            holders = touched_by.setdefault(order_id, [])
            if parent.id not in holders:
                holders.append(parent.id)

    return [strategy for strategy in working if strategy.id not in absorbed]


def _copy(strategy: Strategy) -> Strategy:
    """A private copy, so merging never mutates the caller's objects."""
    return replace(
        strategy,
        legs=[replace(leg) for leg in strategy.legs],
        order_ids=list(strategy.order_ids),
    )


# --------------------------------------------------------------- roll candidates

# How long after closing one trade an opening trade can still plausibly be the
# other half of a roll. Rolls are a single decision even when they take two
# orders to execute, and that decision happens in minutes, not days.
_ROLL_WINDOW = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class RollCandidate:
    """Two trades that look like one roll executed as two separate orders.

    ``match_rolls`` can only see a roll when the broker filled it as one order,
    because that is the only case where the closing and opening legs share an
    order id. Plenty of traders close and re-open separately, and left alone
    that reads as a loser followed by an unrelated winner — which flatters the
    win rate and hides what rolling actually costs.

    Guessing is not the answer either: closing one trade and opening another the
    same afternoon is ordinary behaviour. So these are proposed, never applied,
    and the user's decision is stored in ``manual_overrides``.
    """

    closed_id: str
    opened_id: str
    underlying: str
    confidence: str  # "high" | "likely" | "possible"
    reason: str
    gap_minutes: int
    # What each side actually was, so the decision can be made from the card
    # rather than from the two ids and a sentence.
    closed_legs: tuple[str, ...] = ()
    opened_legs: tuple[str, ...] = ()
    closed_at: datetime | None = None
    opened_at: datetime | None = None
    closed_pnl: Decimal = ZERO
    opened_credit: Decimal = ZERO
    closed_credit: Decimal = ZERO
    days_held: int | None = None


def _structure_family(strategy: Strategy) -> str:
    """Group strategy types that a roll would move between."""
    t = strategy.strategy_type
    if t in (StrategyType.SHORT_STRANGLE, StrategyType.SHORT_STRADDLE):
        return "short premium, two sides"
    if t in (StrategyType.PUT_CREDIT_SPREAD, StrategyType.CALL_CREDIT_SPREAD):
        return "credit spread"
    if t in (StrategyType.IRON_CONDOR, StrategyType.IRON_FLY):
        return "iron"
    if t in (StrategyType.NAKED_PUT, StrategyType.NAKED_CALL):
        return "single short"
    return t.value


def _leg_line(leg: Leg) -> str:
    """"short 2 x 112 call, 20 Nov" — one leg, readable without the symbol."""
    side = "short" if leg.direction is Direction.SHORT else "long"
    size = _fmt(leg.quantity)
    if leg.option_type is None:
        what = "futures" if leg.is_future else "shares"
        return f"{side} {size} {what}"
    kind = "call" if leg.option_type is OptionType.CALL else "put"
    strike = "" if leg.strike is None else f"{leg.strike:g} "
    when = "" if leg.expiration is None else f", {leg.expiration:%-d %b %Y}"
    return f"{side} {size} x {strike}{kind}{when}"


def suggest_roll_links(strategies: Sequence[Strategy]) -> list[RollCandidate]:
    """Propose links for rolls that were executed as two orders.

    Deliberately conservative: a candidate needs the same account and the same
    underlying, a close and an open within hours of each other, and expirations
    that moved outward. Anything weaker would start merging unrelated trades,
    which is worse than leaving them apart.

    The account is part of it because a roll is one position continuing. Every
    other figure in this app treats the accounts as one book, which is how they
    are traded, but you cannot roll a position in one account by opening one in
    another: those are two positions that happen to look alike.
    """
    closed = [s for s in strategies if not s.is_open and s.closed_at and not s.manual_group]
    opened = [s for s in strategies if not s.manual_group]
    out: list[RollCandidate] = []

    for old in closed:
        assert old.closed_at is not None
        old_exps = old.expirations
        for new in opened:
            if new.id == old.id or new.underlying != old.underlying:
                continue
            if new.account_number != old.account_number:
                continue
            gap = new.opened_at - old.closed_at
            if gap < timedelta(0) or gap > _ROLL_WINDOW:
                continue

            new_exps = new.expirations
            # A roll moves the position outward in time, or at minimum to a
            # different expiration. Same expiration and same strikes is not a
            # roll, it is the same trade re-entered.
            if old_exps and new_exps and new_exps[0] <= old_exps[0]:
                continue

            same_family = _structure_family(old) == _structure_family(new)
            same_size = sum(leg.quantity for leg in old.legs) == sum(leg.quantity for leg in new.legs)
            minutes = int(gap.total_seconds() // 60)

            if same_family and same_size:
                confidence = "high"
            elif same_family or same_size:
                confidence = "likely"
            else:
                confidence = "possible"

            when = "moments later" if minutes < 5 else f"{minutes} minutes later"
            moved = ""
            if old_exps and new_exps:
                moved = f", expiry {old_exps[0].isoformat()} -> {new_exps[0].isoformat()}"
            shape = (
                f"both {_structure_family(old)}"
                if same_family
                else f"{old.strategy_type.value} -> {new.strategy_type.value}"
            )

            out.append(
                RollCandidate(
                    closed_id=old.id,
                    opened_id=new.id,
                    underlying=old.underlying,
                    confidence=confidence,
                    reason=(
                        f"Closed this {old.underlying} position and opened another {when}"
                        f"{moved} ({shape})."
                        + ("" if same_size else " Contract counts differ, so check the size.")
                    ),
                    gap_minutes=minutes,
                    closed_legs=tuple(_leg_line(leg) for leg in old.legs),
                    opened_legs=tuple(_leg_line(leg) for leg in new.legs),
                    closed_at=old.closed_at,
                    opened_at=new.opened_at,
                    closed_pnl=old.realized_pnl,
                    closed_credit=old.net_credit,
                    opened_credit=new.net_credit,
                    days_held=(old.closed_at - old.opened_at).days if old.closed_at else None,
                )
            )

    order = {"high": 0, "likely": 1, "possible": 2}
    out.sort(key=lambda c: (order[c.confidence], c.gap_minutes))
    return out


# ------------------------------------------------------------- stale positions

# Settlement can take a day, so an option is not treated as gone the moment its
# expiration date arrives.
_SETTLEMENT_GRACE = timedelta(days=2)


def close_expired(
    strategies: Sequence[Strategy], today: date, *, grace: timedelta = _SETTLEMENT_GRACE
) -> list[Strategy]:
    """Close positions whose options have expired but that have no closing row.

    Brokers do not always send a transaction when an option expires worthless,
    and a close that fails to match its open leaves the same gap. Either way the
    journal holds a position that no longer exists: one such trade in this
    user's book had been "open" for a year, and because an expired contract has
    no price it also turned the entire portfolio's P&L into "unknown".

    What this does NOT do is invent an outcome. The cash flows stay exactly as
    recorded, so a short option that expired worthless correctly keeps its
    credit. But an option that finished in the money was exercised or assigned
    into something, and that something is not in these figures — so the trade
    carries a note saying so rather than presenting a tidy number that might be
    missing a leg. Deciding which case applies needs the underlying's price at
    expiry, which the transaction record does not carry, so the honest move is
    to say the outcome is unverified and let the reader check.
    """
    cutoff = today - grace
    out: list[Strategy] = []

    for strategy in strategies:
        expirations = strategy.expirations
        if not strategy.is_open or not expirations or expirations[-1] > cutoff:
            out.append(strategy)
            continue

        last_expiry = expirations[-1]
        closed_at = datetime.combine(last_expiry, time(20, 0), tzinfo=UTC)
        note = (
            f"{last_expiry.isoformat()} closed by expiry: no closing transaction was found, "
            "so the recorded cash flows are all there is. If it was exercised or assigned, "
            "that outcome is not in these figures."
        )
        logger.info(
            "grouping: closing %s at its %s expiry; no closing transaction was found",
            strategy.id,
            last_expiry.isoformat(),
        )
        out.append(
            replace(
                strategy,
                closed_at=closed_at,
                outcome_unverified=True,
                notes=f"{strategy.notes}\n{note}" if strategy.notes else note,
            )
        )

    return out
