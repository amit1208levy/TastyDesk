"""The application layer: one object that owns syncing, caching and derived views.

Everything the dashboard shows and everything the MCP server answers comes
through here, so the two can never drift apart. That was an explicit
requirement: there must be no gap between what Claude sees in conversation and
what the dashboard renders, and the way to guarantee it is to give them one
code path rather than two that agree by convention.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from tastydesk.core import (
    analytics,
    confidence,
    greeks,
    grouping,
    indicators,
    pairing,
    playbook,
)
from tastydesk.core import pnl as pnl_mod
from tastydesk.core import risk as risk_mod
from tastydesk.core import scenario as scenario_mod
from tastydesk.core.analytics import PerformanceStats, RuleSet

# The package re-exports classify() the function, which shadows the module of
# the same name, so the function is imported directly rather than reached
# through the package.
from tastydesk.core.classify import classify as classify_legs
from tastydesk.core.client import ClientHealth, TastyClient
from tastydesk.core.db import Database
from tastydesk.core.marks import MarkService
from tastydesk.core.models import (
    ZERO,
    Leg,
    PortfolioSummary,
    RiskReason,
    Strategy,
    StrategyPnL,
    StrategyRisk,
    UnderlyingQuote,
)
from tastydesk.core.occ import product_root

logger = logging.getLogger(__name__)

VIX_SYMBOL = "VIX"

# Bumped when new columns should be folded into a saved list once.
_COLUMN_MIGRATION = "v2-verdict"

# How recently a position must have been opened for today's volatility and
# deltas to be a fair record of its entry. Anything older keeps its unknowns:
# filing today's IV rank as the reason for a trade made in a different market
# would make every slice built on it fiction.
_ENTRY_CONTEXT_GRACE_DAYS = 3

# How far back the first sync reaches. Two years covers enough closed trades for
# the per-bucket statistics to mean anything without making the first run crawl.
INITIAL_HISTORY_DAYS = 730

# Every "how many days to expiry" question is a question about trading days, and
# an option expires on a New York date. Asking UTC instead moves the answer after
# about 8pm Eastern: on expiry evening a 0-DTE position reads as -1 DTE, and both
# the pin-risk and expiry-week alarms switch themselves off on the one evening
# they matter. The 21-DTE flag slides by a day for the same reason.
MARKET_TZ = ZoneInfo("America/New_York")


def market_today() -> date:
    """Today on the exchange's calendar, not the server's."""
    return datetime.now(MARKET_TZ).date()


# Re-fetch a little before the last stored transaction. tastytrade backfills
# fees and corrections for a day or two after the fact, and an upsert keyed on
# transaction id makes the overlap free.
RESYNC_OVERLAP_DAYS = 5


def _readable_pattern(pattern: str) -> str:
    """A pattern id as a phrase: "/MESH7|strangle|same-expiry" -> the English."""
    parts = pattern.split("|")
    underlying = parts[0] if parts else pattern
    kind = parts[1].replace("-", " ") if len(parts) > 1 else ""
    expiry = ""
    if len(parts) > 2:
        expiry = "same expiry" if parts[2] == "same-expiry" else "split expiries"
    tail = ", ".join(bit for bit in (kind, expiry) if bit)
    return f"{underlying} — {tail}" if tail else underlying


def _stats_dict(stats: Any) -> dict[str, object]:
    """PerformanceStats as plain values, for the API layer to encode."""
    from dataclasses import asdict

    return asdict(stats)


def _zero_crossings(curve: list[dict[str, Decimal]]) -> list[Decimal]:
    """Where a drawn curve crosses zero, by interpolation between samples.

    Only for structures whose exact breakevens cannot be solved — a diagonal,
    where the payoff drawn is an assumption rather than arithmetic. Reading the
    crossings off the same samples the chart plots keeps the dot on the line it
    belongs to instead of claiming a precision the structure does not have.
    """
    roots: list[Decimal] = []
    for first, second in zip(curve, curve[1:], strict=False):
        y0, y1 = first["pnl"], second["pnl"]
        if (y0 < ZERO < y1) or (y1 < ZERO < y0):
            x0, x1 = first["price"], second["price"]
            roots.append(x0 + (x1 - x0) * (-y0) / (y1 - y0))
        elif y0 == ZERO:
            roots.append(first["price"])
    return [root.quantize(Decimal("0.01")) for root in roots]


def _leg_lines(strategy: Strategy) -> list[str]:
    return [
        f"{leg.direction.value.lower()} {leg.quantity:g} "
        f"{leg.option_type.value if leg.option_type else 'sh'}"
        f"{f' {leg.strike:g}' if leg.strike else ''}"
        f"{f' {leg.expiration:%d %b %y}' if leg.expiration else ''}"
        for leg in strategy.legs
    ]


def _front_month(members: list[Strategy]) -> str:
    """The contract month expiring first — the one that decides the row's fate."""
    def key(m: Strategy) -> tuple[date, str]:
        exps = m.expirations
        return (exps[0] if exps else date.max, m.underlying)

    return min(members, key=key).underlying


def _member_row(strategy: Strategy, today: date) -> dict[str, object]:
    """One past trade, with everything needed to judge it without opening it.

    The history of a named strategy is the whole point of naming one, so a row
    here carries what a premium seller actually asks of an old trade: what was
    collected, what came back, how long it was held, how close to expiry it was
    let run, and — the one most journals drop — *how it ended*. A trade that was
    assigned and a trade that was bought back at 50% are not the same trade, and
    the difference does not show up anywhere in the P&L column.
    """
    expirations = strategy.expirations
    credit = pnl_mod.premium_at_risk(strategy)
    realized = strategy.realized_pnl

    dte_at_close: int | None = None
    if strategy.closed_at is not None and expirations:
        dte_at_close = (expirations[0] - strategy.closed_at.date()).days

    days_held: int | None = None
    end = strategy.closed_at or datetime.now(UTC)
    days_held = max((end - strategy.opened_at).days, 0)

    max_profit = analytics.max_profit_at_close(strategy)
    captured = (
        str(realized / max_profit)
        if max_profit is not None and max_profit > ZERO and not strategy.is_open
        else None
    )

    if strategy.is_open:
        ending = "open"
    elif strategy.closed_by_assignment:
        ending = "assigned"
    elif strategy.outcome_unverified:
        ending = "unverified"
    elif expirations and strategy.closed_at and strategy.closed_at.date() >= expirations[0]:
        ending = "expired"
    else:
        ending = "closed"

    open_pnl: str | None = None
    if strategy.is_open:
        # The P&L column must not read a credit as a profit. An open trade has
        # taken in its credit but has not kept it, so what belongs here is what
        # it would cost to close right now.
        computed = pnl_mod.compute_pnl(strategy)
        open_pnl = None if computed.open_pnl is None else str(computed.open_pnl)

    if strategy.is_open:
        outcome = "open"
    elif realized > ZERO:
        outcome = "win"
    elif realized < ZERO:
        outcome = "loss"
    else:
        outcome = "scratch"

    return {
        "id": strategy.id,
        "account": strategy.account_number,
        "underlying": strategy.underlying,
        "opened": strategy.opened_at.date().isoformat(),
        "closed": strategy.closed_at.date().isoformat() if strategy.closed_at else None,
        "is_open": strategy.is_open,
        "structure": strategy.strategy_type.value,
        "credit": str(credit),
        "realized_pnl": str(realized),
        "open_pnl": open_pnl,
        "captured": captured,
        "days_held": days_held,
        "dte_at_entry": strategy.dte_at_entry,
        "dte_at_close": dte_at_close,
        "dte_now": strategy.dte(today) if strategy.is_open else None,
        "roll_count": strategy.roll_count,
        "outcome": outcome,
        "ending": ending,
        "legs": _leg_lines(strategy),
    }


class SyncError(RuntimeError):
    """A sync that could not complete honestly and refused to guess."""


@dataclass(slots=True)
class StrategyView:
    """A strategy plus everything computed about it — what the UI renders."""

    strategy: Strategy
    pnl: StrategyPnL
    risk: StrategyRisk
    underlying_price: Decimal | None = None
    iv_rank: Decimal | None = None
    # Every field in the indicator catalogue, measured for this position and
    # for each of its legs. The page renders whichever the user chose.
    values: dict[str, Any] = field(default_factory=dict)
    leg_values: list[dict[str, Any]] = field(default_factory=list)
    # What to do about it. The table sorts on this rather than on a hidden score.
    verdict: Any = None
    # Set when this row is a strategy the user named. ``named_id`` links back to
    # it; ``parts`` is how many of his open trades were merged into this row.
    named_id: str | None = None
    named_name: str | None = None
    parts: int = 1


@dataclass(slots=True)
class SyncResult:
    transactions_imported: int
    strategies_built: int
    open_strategies: int
    quoted_strategies: int
    started_at: datetime
    finished_at: datetime
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class DeskService:
    def __init__(
        self,
        db: Database,
        client: TastyClient,
        marks: MarkService | None = None,
        *,
        rules: RuleSet | None = None,
    ) -> None:
        self._db = db
        self._client = client
        self._marks = marks or MarkService(_MarkClientAdapter(client))
        self._rules = rules or RuleSet()

        self._strategies: list[Strategy] = []
        self._named: list[playbook.NamedStrategy] = []
        self._quotes: dict[str, UnderlyingQuote] = {}
        self._greeks = greeks.portfolio_greeks([], {})
        # Yesterday's close per strategy, for "P&L today". Empty until the
        # snapshot job has run on a previous session.
        # symbol -> the broker's close price for it, which is what a day's
        # move is measured from. Filled from the positions endpoint.
        self._prior_close: dict[str, Decimal] = {}
        # symbol -> what one point of it is worth, straight from the broker.
        self._contract_size: dict[str, Decimal] = {}
        # (closed id, opened id) -> what the user said about that roll.
        self._roll_decisions: dict[tuple[str, str], str] = {}
        # When the open positions were last re-priced, and the lock that stops
        # three polling endpoints all re-pricing at once.
        self._priced_at: datetime | None = None
        self._price_lock = asyncio.Lock()
        self._refresh: asyncio.Task[None] | None = None
        self._resync: asyncio.Task[None] | None = None
        # The account's closing value last session, which changes once a day.
        self._prior_net_liq: Decimal | None = None
        self._prior_net_liq_for: date | None = None
        self._balances_cache: PortfolioSummary | None = None
        self._last_sync: datetime | None = None
        self._last_error: str | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup

    async def start(self) -> None:
        await self._db.connect()
        await self._db.migrate()
        self._strategies = await self._db.load_strategies(include_closed=True)
        logger.info("Loaded %d strategies from the local database", len(self._strategies))
        # Restarting is not news. The Activity tab promises "each one logged
        # once, not repeated while it stays true", and a run of identical
        # "started with 510 strategies" lines buries the entries that matter.
        summary = (
            f"Started with {len(self._strategies)} strategies "
            f"({sum(1 for s in self._strategies if s.is_open)} open)."
        )
        previous = await self._db.events(limit=1, kinds=["app.started"])
        if not previous or previous[0].get("summary") != summary:
            await self._db.record("app.started", summary)
        await self.load_named()
        await self.load_roll_decisions()
        # A restart must not make the app claim it has never synced. The data it
        # just loaded came from somewhere, and reporting "last sync: never" over
        # a database full of this morning's fills reads as a broken connection.
        with suppress(Exception):
            # The stored stamp first: a quiet sync writes no event, so the
            # event log is no longer the whole story.
            stored = await self._db.get_setting("last_sync")
            if stored:
                self._last_sync = datetime.fromisoformat(stored)
            else:
                done = await self._db.events(limit=1, kinds=["sync.completed"])
                if done:
                    stamp = done[0].get("at") or done[0].get("created_at")
                    if isinstance(stamp, datetime):
                        self._last_sync = stamp
                    elif isinstance(stamp, str):
                        self._last_sync = datetime.fromisoformat(stamp)

        # Marks are live, so they are not in the database that was just loaded.
        # Without this the dashboard opens with every P&L blank until the user
        # thinks to press Sync -- which looks like a broken app rather than an
        # unpriced one.
        # Booting must survive a missing credential or an unreachable broker:
        # the onboarding screen is what should appear then, not a stack trace.
        # The close prices a day's move is measured from live on the positions
        # endpoint, so they have to be fetched before the first refresh or the
        # column opens blank until the user presses Sync.
        with suppress(Exception):
            await self._load_prior_closes(await self._client.accounts())
        with suppress(Exception):
            await self.refresh_marks()
        with suppress(Exception):
            await self.summary()

    async def ensure_fresh(self, max_age: float = 20.0) -> None:
        """Keep the marks current without making anyone wait for them.

        The page polls every thirty seconds, but polling an endpoint is not the
        same as the numbers moving: the marks behind them were only refreshed
        at startup and on a sync, so the dashboard sat on whatever prices it
        had opened with until the user pressed Sync. It looked live and was
        not.

        So a request that finds the prices stale starts a refresh and answers
        immediately with what it has. Waiting for the broker instead would put
        two or three seconds on every page load to change figures by a few
        dollars, which is a bad trade: the numbers on screen are seconds old,
        they are labelled with how old, and the next poll is thirty seconds
        away. The one time it does wait is the first call of all, when there is
        nothing to answer with.

        Lazily rather than on a timer, because an app nobody is looking at
        should not be calling the broker, and behind a lock because the three
        endpoints poll together and one refresh serves all of them.
        """
        if not self._marks_stale(max_age):
            return
        if self._priced_at is None:
            await self._reprice()
            return
        if self._refresh is None or self._refresh.done():
            self._refresh = asyncio.create_task(self._reprice())

    async def ensure_journal(self, max_age: float = 300.0) -> None:
        """Pull new fills if the journal has not been read for a while.

        Prices refreshing on their own is only half of "is this page current".
        The other half is the book itself: close a position at the broker and
        this app went on showing it, and telling the user to close it, until he
        thought to press Sync. It had just told him to take profit on a call he
        had bought back an hour earlier.

        Five minutes, in the background, and only while someone is looking at
        the page. A sync reads transactions and rebuilds the book, which is
        heavier than a re-price, so it is not something to do every thirty
        seconds and not something to make anyone wait for.
        """
        if self._last_sync is not None:
            if (datetime.now(UTC) - self._last_sync).total_seconds() < max_age:
                return
        if self._resync is not None and not self._resync.done():
            return
        self._resync = asyncio.create_task(self._quiet_sync())

    async def _quiet_sync(self) -> None:
        with suppress(Exception):
            await self.sync()

    def _marks_stale(self, max_age: float) -> bool:
        if self._priced_at is None:
            return True
        return (datetime.now(UTC) - self._priced_at).total_seconds() >= max_age

    async def _reprice(self) -> None:
        """New marks, and the summary rebuilt on top of them."""
        async with self._price_lock:
            if not self._marks_stale(5.0):
                return
            await self.refresh_marks()
            # Built from the marks that just changed, so it has to go -- and
            # rebuilding it here, in the background, is what keeps the next
            # request from paying for it.
            self._balances_cache = None
            with suppress(Exception):
                await self.summary()

    async def refresh_marks(self) -> list[str]:
        """Re-price the open positions without touching transaction history.

        Separate from sync() because pricing and bookkeeping fail for different
        reasons and at different rates: quotes go stale in seconds, the journal
        changes when a fill happens. Returns whatever went wrong, so a caller
        can say "prices are missing" instead of showing zeros.
        """
        problems: list[str] = []
        open_strategies = [s for s in self._strategies if s.is_open]
        if not open_strategies:
            return problems
        try:
            await self._marks.refresh(open_strategies)
            self._apply_prior_closes(open_strategies)
            # SPY rides along because every beta-weighted figure is expressed in
            # it: without its price the book's exposure has no unit to be in.
            # VIX too: the what-if page states volatility as a VIX level,
            # because that is the number a trader already has in their head.
            wanted = sorted(
                {s.underlying for s in open_strategies} | {greeks.REFERENCE_SYMBOL, VIX_SYMBOL}
            )
            self._quotes = await self._marks.underlying_quotes(wanted)
            self._priced_at = datetime.now(UTC)
        except Exception as exc:
            logger.warning("Could not refresh marks", exc_info=True)
            problems.append(str(exc))
            await self._db.record(
                "marks.unavailable",
                f"Could not price open positions: {exc}",
                severity="warning",
            )
        return problems

    async def stop(self) -> None:
        for task in (self._refresh, self._resync):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        await self._db.close()

    # ------------------------------------------------------------------- sync

    async def sync(self, *, full: bool = False) -> SyncResult:
        """Pull new transactions, rebuild strategies, and re-price the open ones.

        Serialised behind a lock: two concurrent syncs would both rebuild the
        strategy list from a half-written transaction table.
        """
        async with self._lock:
            started = datetime.now(UTC)
            warnings: list[str] = []
            previous = {s.id: s for s in self._strategies}

            # Every account, not the first one. The first account of three was
            # a dormant one holding six cents, so the dashboard reported an
            # empty book while the real one carried twenty-two positions.
            accounts = [a for a in await self._client.accounts() if not a.is_closed]
            if not accounts:
                raise SyncError("No open accounts are visible on this login.")

            imported = 0
            strategies: list[Strategy] = []
            per_account: list[tuple[Any, list[Strategy]]] = []
            overrides = await self._db.get_manual_overrides()
            rules = pairing.PairingRules(decisions=await self._db.get_pairing_rules())  # type: ignore[arg-type]

            for account in accounts:
                start_date: date | None = None
                if not full:
                    last = await self._db.last_transaction_date(account.account_number)
                    if last is not None:
                        start_date = last - timedelta(days=RESYNC_OVERLAP_DAYS)
                if start_date is None:
                    start_date = (datetime.now(UTC) - timedelta(days=INITIAL_HISTORY_DAYS)).date()

                rows = await self._client.transactions(account, start_date=start_date)
                imported += await self._db.upsert_transactions(rows)

                stored = await self._db.get_transactions(account_number=account.account_number)
                history, dropped = _as_transactions(stored)
                if dropped:
                    await self._db.record(
                        "sync.failed",
                        f"{dropped} of {len(stored)} stored transactions could not be read back.",
                        severity="error",
                        detail={"dropped": dropped, "stored": len(stored)},
                    )
                    # Falling back to `rows` here would rebuild the journal from
                    # whatever this one call fetched -- five days, on an
                    # incremental sync -- and quietly empty the dashboard.
                    # Refusing is the honest failure; an empty portfolio that
                    # looks real is not.
                    raise SyncError(
                        f"{dropped} of {len(stored)} stored transactions could not be read back "
                        "from the local database, so the rebuild would be incomplete. "
                        "This is a bug in Tasty Desk, not in your account. "
                        "Re-run a full sync (tastydesk sync --full) to repair the table."
                    )

                built = self._reconstruct(history, account.account_number, overrides, rules)
                per_account.append((account, built))
                strategies.extend(built)

            self._strategies = strategies
            open_strategies = [s for s in strategies if s.is_open]

            # Attribute buying power before the rows are written, or the
            # database never sees it and the next restart loads strategies with
            # the figure missing again.
            await self._attribute_buying_power(accounts, open_strategies)
            await self._load_prior_closes(accounts)
            # Marks first: entry context needs live IV rank and leg deltas.
            warnings.extend(f"marks unavailable: {p}" for p in await self.refresh_marks())
            await self._capture_entry_context(open_strategies)
            for account, built in per_account:
                await self._db.save_strategies(built, reconcile_account=account.account_number)

            # A pricing failure must not lose the rebuild. Positions stay
            # visible with their marks missing, which the P&L reports as
            # "partially quoted" rather than as a number.
            warnings.extend(f"marks unavailable: {p}" for p in await self.refresh_marks())

            self._balances_cache = await self._build_summary(accounts, open_strategies)
            self._last_sync = datetime.now(UTC)
            self._last_error = None

            quoted = sum(1 for s in open_strategies if pnl_mod.compute_pnl(s).fully_quoted)

            await self._notice_lifecycle(previous)
            today = market_today()
            net_liq = self._balances_cache.net_liquidating_value if self._balances_cache else None
            crossings = await self._notice_crossings([self._view(s, today, net_liq) for s in open_strategies])
            # Logged only when something changed. The app syncs itself every
            # few minutes now, and an Activity tab that promises "each one
            # logged once, not repeated while it stays true" cannot carry a
            # line every five minutes saying nothing happened.
            changed = imported > 0 or len(open_strategies) != len(
                [s for s in previous.values() if s.is_open]
            )
            if changed or full:
                await self._db.record(
                    "sync.completed",
                    f"Synced {imported} new transactions; {len(open_strategies)} open positions, "
                    f"{quoted} fully priced.",
                    detail={
                        "imported": imported,
                        "strategies": len(strategies),
                        "open": len(open_strategies),
                        "quoted": quoted,
                        "warnings": warnings,
                        "crossings": crossings,
                    },
                )
            # The clock in the header reads this, and it has to be right even
            # when the sync was quiet enough not to be worth an entry.
            with suppress(Exception):
                await self._db.set_setting("last_sync", self._last_sync.isoformat())

            # Every sync records where the positions stand. "P&L today" is the
            # difference against the last mark taken before today, and until
            # now the only thing writing those marks was a launchd job that has
            # to be installed by hand — so on a machine without it the column
            # was permanently blank, and max adverse excursion never accrued.
            # A sync is exactly the moment the marks are freshest.
            with suppress(Exception):
                await self.snapshot()

            return SyncResult(
                transactions_imported=imported,
                strategies_built=len(strategies),
                open_strategies=len(open_strategies),
                quoted_strategies=quoted,
                started_at=started,
                finished_at=self._last_sync,
                warnings=warnings,
            )

    async def snapshot(self) -> int:
        """Record today's mark for every open strategy.

        Max adverse excursion cannot be reconstructed from transactions alone,
        so without this job the "did I respect my 2x stop?" report can never be
        answered. It is cheap and it is the only way that history accrues.

        Recorded against the real trades, never against the merged rows the
        Positions tab shows. A merged row's id belongs to no trade in the
        journal, so a snapshot filed under it would be looked up by nothing: the
        excursion history for every position the user grouped would quietly stop
        accruing, and the stop-loss report would go blank on exactly the trades
        he cares most about.
        """
        written = 0
        skipped = 0
        now = datetime.now(UTC)
        today = market_today()
        net_liq = self._balances_cache.net_liquidating_value if self._balances_cache else None
        for view in [self._view(s, today, net_liq) for s in self._strategies if s.is_open]:
            if view.pnl.open_pnl is None or view.pnl.cost_to_close is None:
                # An unpriced strategy has no P&L to record. Writing zero here
                # would make a weekend run look like a day the trade was exactly
                # flat, and that fabricated zero then becomes its best-ever
                # excursion -- corrupting the very history the 2x-stop report
                # depends on. A missing day is recoverable; a false one is not.
                skipped += 1
                continue
            await self._db.save_snapshot(
                view.strategy.id,
                now,
                mark_value=view.pnl.cost_to_close,
                open_pnl=view.pnl.open_pnl,
                pct_of_credit=view.pnl.pct_of_credit,
                underlying_price=view.underlying_price,
                worst_short_delta=view.risk.worst_short_delta,
            )
            written += 1
        if skipped:
            logger.warning(
                "Skipped %d unpriced strategies in today's snapshot; they had no live marks", skipped
            )
        return written

    # ------------------------------------------------------------------ views

    def _quote_for(self, strategy: Strategy) -> UnderlyingQuote | None:
        """The quote that prices this strategy, whatever it is booked against.

        An outright futures contract is booked against the product — the
        broker's underlying for it is "/ZB" — and a product has no price of its
        own. The contract month does, and it is the leg's own symbol. Without
        this last fallback the futures rows on the Legs page had no underlying
        price, no moneyness and nothing to measure a delta in dollars against.
        """
        found = self._quotes.get(strategy.underlying) or self._quotes.get(
            product_root(strategy.underlying)
        )
        if found is not None and (found.mark or found.last) is not None:
            return found
        for leg in strategy.legs:
            month = self._quotes.get(leg.symbol)
            if month is not None and (month.mark or month.last) is not None:
                return month
        return found

    def _view(
        self,
        strategy: Strategy,
        today: date,
        net_liq: Decimal | None,
        *,
        name: str | None = None,
        parts: int = 1,
        baseline_ids: Sequence[str] | None = None,
    ) -> StrategyView:
        # A position that is open is judged on what is open: P&L, % of max
        # profit, the verdict and the risk all come from the legs held now,
        # never from what the rolls before them banked.
        if strategy.is_open and strategy.legs:
            strategy = strategy.open_legs_only()
        computed = pnl_mod.compute_pnl(strategy)
        quote = self._quote_for(strategy)
        assessment = risk_mod.assess(strategy, computed, quote, today, net_liq)
        price = (quote.mark or quote.last) if quote else None
        call = indicators.verdict_for(
            strategy,
            computed,
            assessment,
            profit_target=self._rules.profit_target_pct,
            dte_exit=self._rules.dte_exit,
            stop_multiple=self._rules.stop_loss_multiple,
        )
        # Measured leg by leg against the broker's own close price, so the
        # figure matches the one on the tastytrade screen and a merged row
        # needs no special case: its legs are its members' legs.
        day_change = pnl_mod.day_change(strategy)
        reference = self._quotes.get(greeks.REFERENCE_SYMBOL)
        return StrategyView(
            strategy=strategy,
            pnl=computed,
            risk=assessment,
            underlying_price=price,
            iv_rank=quote.iv_rank if quote else None,
            values=indicators.strategy_values(
                strategy,
                computed,
                assessment,
                today=today,
                quote=quote,
                price=price,
                net_liq=net_liq,
                name=name,
                parts=parts,
                premium=pnl_mod.premium_at_risk(strategy),
                beta=quote.beta if quote else None,
                reference_price=(reference.mark or reference.last) if reference else None,
                verdict=call,
                day_change=day_change,
            ),
            verdict=call,
            leg_values=[
                indicators.leg_values(leg, today=today, price=price) for leg in strategy.legs
            ],
        )

    def _merge_open_members(self, named: playbook.NamedStrategy) -> list[Strategy]:
        """The open trades of a named strategy, merged where that is honest.

        The user grouped a long June LEAP and a short September call and called
        it a PMCC. Shown as two rows the short call is a naked call with
        undefined risk, and the app was calling it Critical — on a position
        whose loss is capped by the LEAP sitting beside it. That is the exact
        mistake this application was built to stop making, so a strategy he has
        named is assessed as one thing.

        A strategy spanning two contract months — a calendarised strangle in
        /ZSF7 and /ZSX6 — still gets one row, because that is how the user
        trades it. What it does not get is one price: those two months trade at
        different levels, so anything measured against spot (distance to the
        short strike, sigma, breach) is computed per month by
        :meth:`_merged_risk` and the row reports the worst of them. The prices
        themselves are listed per month rather than averaged into a number that
        describes neither.
        """
        by_id = {s.id: s for s in self._strategies}
        members = [by_id[tid] for tid in named.member_ids if tid in by_id and by_id[tid].is_open]
        if len(members) <= 1:
            return members

        underlyings = {m.underlying for m in members}
        legs: list[Leg] = []
        for m in members:
            legs.extend(m.legs)

        powers = [m.buying_power_used for m in members if m.buying_power_used is not None]
        structure, profile = classify_legs(legs)
        merged = Strategy(
            id=f"named:{named.id}",
            account_number=members[0].account_number,
            # The front month names the row when a strategy spans several; the
            # rest are named in the legs and in the per-month risk reasons.
            underlying=sorted(underlyings)[0] if len(underlyings) == 1 else _front_month(members),
            strategy_type=structure,
            risk_profile=profile,
            legs=legs,
            opened_at=min(m.opened_at for m in members),
            closed_at=None,
            net_credit=sum((m.net_credit for m in members), ZERO),
            closing_cash_flow=sum((m.closing_cash_flow for m in members), ZERO),
            fees=sum((m.fees for m in members), ZERO),
            order_ids=[oid for m in members for oid in m.order_ids],
            roll_count=sum(m.roll_count for m in members),
            buying_power_used=sum(powers, ZERO) if powers else None,
            manual_group=True,
            notes=named.name,
        )
        return [merged]

    def _merged_risk(
        self,
        merged: Strategy,
        members: list[Strategy],
        today: date,
        net_liq: Decimal | None,
    ) -> StrategyRisk:
        """Risk for a strategy that spans more than one contract month.

        Distance to a strike, sigma and "has it been breached" are all measured
        against spot, and /ZSF7 and /ZSX6 have two different spots. So each
        month is assessed against its own price and the row reports the worst
        level, with every reason labelled by the month it came from. Nothing is
        averaged: an average of two underlying prices describes neither.
        """
        if len({m.underlying for m in members}) <= 1:
            quote = self._quotes.get(merged.underlying)
            return risk_mod.assess(
                merged, pnl_mod.compute_pnl(merged), quote, today, net_liq
            )

        per_month = []
        for member in sorted(members, key=lambda m: m.underlying):
            quote = self._quotes.get(member.underlying)
            per_month.append(
                (
                    member.underlying,
                    risk_mod.assess(
                        member, pnl_mod.compute_pnl(member), quote, today, net_liq
                    ),
                )
            )

        worst = max(per_month, key=lambda pair: (pair[1].level.rank, pair[1].score))[1]
        reasons: list[RiskReason] = []
        for underlying, assessment in per_month:
            for reason in assessment.reasons:
                reasons.append(
                    RiskReason(
                        code=f"{underlying}:{reason.code}",
                        level=reason.level,
                        message=f"{underlying}: {reason.message}",
                    )
                )

        dtes = [a.dte for _, a in per_month if a.dte is not None]
        deltas = [a.worst_short_delta for _, a in per_month if a.worst_short_delta is not None]
        return StrategyRisk(
            level=worst.level,
            score=max(a.score for _, a in per_month),
            reasons=reasons,
            dte=min(dtes) if dtes else None,
            worst_short_delta=max(deltas) if deltas else None,
            distance_to_short_pct=worst.distance_to_short_pct,
            short_strike_in_moves=worst.short_strike_in_moves,
            # From the same month the rest of this reading came from, or the
            # row shows a blank beside a sentence quoting the number.
            expected_move=worst.expected_move,
            breached=any(a.breached for _, a in per_month),
            breached_side=worst.breached_side,
            assignment_risk=any(a.assignment_risk for _, a in per_month),
            pin_risk=any(a.pin_risk for _, a in per_month),
            pct_of_net_liq=worst.pct_of_net_liq,
        )

    def _named_views(
        self, named: playbook.NamedStrategy, today: date, net_liq: Decimal | None
    ) -> list[StrategyView]:
        """The live reading of one named strategy: its open trades, merged.

        The positions table and the strategy page both ask this, and they must
        not be able to disagree about what a strategy is doing right now.
        """
        merged = self._merge_open_members(named)
        if not merged:
            return []
        by_id = {s.id: s for s in self._strategies}
        members = [tid for tid in named.member_ids if tid in by_id and by_id[tid].is_open]
        open_members = [by_id[tid] for tid in members]

        views: list[StrategyView] = []
        for part in merged:
            whole = part.id.startswith("named:")
            view = self._view(
                part,
                today,
                net_liq,
                name=named.name,
                parts=len(members) if whole else 1,
                baseline_ids=members if whole else None,
            )
            if whole:
                # The merged reading replaces the one assessed a moment ago,
                # so the verdict has to be taken again against it — a row
                # reading Danger beside "no rule has fired yet" is the app
                # contradicting itself on one line.
                view.risk = self._merged_risk(part, open_members, today, net_liq)
                view.verdict = indicators.verdict_for(
                    part,
                    view.pnl,
                    view.risk,
                    profit_target=self._rules.profit_target_pct,
                    dte_exit=self._rules.dte_exit,
                    stop_multiple=self._rules.stop_loss_multiple,
                )
                view.values["verdict"] = {
                    "action": view.verdict.action,
                    "reason": view.verdict.reason,
                    "rank": view.verdict.rank,
                    "tone": view.verdict.tone,
                }
                view.values["risk_level"] = view.risk.level.value
                view.parts = len(members)
            view.named_id = named.id
            view.named_name = named.name
            views.append(view)
        return views

    async def open_views(self) -> list[StrategyView]:
        # Every number on this page is priced off the marks, and every row on
        # it comes from the journal. Both are checked for staleness here, and
        # both refresh behind the request rather than in front of it.
        with suppress(Exception):
            await self.ensure_fresh()
        with suppress(Exception):
            await self.ensure_journal()
        today = market_today()
        if self._balances_cache is None:
            # Concentration is meaningless without a net liq to divide by, and
            # whichever endpoint the dashboard happens to call first should not
            # decide whether the column has numbers in it.
            with suppress(Exception):
                await self.summary()
        net_liq = self._balances_cache.net_liquidating_value if self._balances_cache else None

        claimed: set[str] = set()
        views: list[StrategyView] = []
        for named in self._named:
            part_views = self._named_views(named, today, net_liq)
            if not part_views:
                continue
            claimed.update(
                tid
                for tid in named.member_ids
                if any(s.id == tid and s.is_open for s in self._strategies)
            )
            views.extend(part_views)

        views.extend(
            self._view(s, today, net_liq)
            for s in self._strategies
            if s.is_open and s.id not in claimed
        )
        views.sort(key=lambda v: (v.risk.level.rank, v.risk.score), reverse=True)
        return views

    async def scenario(
        self,
        price_shift: Decimal,
        iv_shift: Decimal,
        days: int,
        by_beta: bool = True,
        strategy_id: str | None = None,
    ) -> dict[str, object]:
        """Every open position priced under one set of conditions.

        The same dials for the whole book, because that is the question worth
        asking of a book: not what one strangle does if wheat moves, but what
        everything does together if the market moves two percent and a week
        goes by. Each product moves by the same proportion rather than the same
        number of points, which is the only way a soybean row and a Best Buy
        row can be added up at the bottom.

        "Now" here is the model's own reading with every dial at zero, not the
        live mark. The two differ a little — the model prices from the leg's
        implied volatility, the mark comes from the book — and subtracting one
        from the other would put that difference into every change. The live
        P&L is reported beside it so the gap is visible rather than hidden.
        """
        views = await self.open_views()
        # One position, when asked for: the same dials, on the trade you are
        # actually deciding about rather than on everything at once.
        if strategy_id is not None:
            views = [v for v in views if v.strategy.id == strategy_id]
        today = market_today()
        flat = scenario_mod.Scenario()
        # Every contract month the app holds a price for, so an option on the
        # March bond is priced off March rather than off whatever month the
        # position happens to be booked under.
        spots = {
            symbol: (quote.mark or quote.last)
            for symbol, quote in self._quotes.items()
            if (quote.mark or quote.last) is not None
        }

        rows: list[dict[str, object]] = []
        now_total: Decimal | None = ZERO
        then_total: Decimal | None = ZERO
        for view in views:
            spot = view.underlying_price
            # Moved with the market rather than all by the same amount. A 10%
            # fall in SPY is not a 10% fall in the thirty-year bond, and pricing
            # it as one put $21,000 of loss on two bond contracts that would
            # likely have gained. Each product moves by its own beta to SPY,
            # the same figure the beta-weighted delta is built from; one with
            # no published beta moves by the full amount, which is the
            # cautious reading rather than the flattering one.
            beta = None
            if by_beta:
                quote = self._quote_for(view.strategy)
                beta = quote.beta if quote is not None else None
            shift = price_shift * beta if beta is not None else price_shift
            asked_here = scenario_mod.Scenario(price_shift=shift, iv_shift=iv_shift, days=days)
            now = scenario_mod.strategy_pnl(view.strategy, spot, flat, today, spots)
            then = scenario_mod.strategy_pnl(view.strategy, spot, asked_here, today, spots)
            change = None if now is None or then is None else then - now
            delta_now = scenario_mod.strategy_delta(view.strategy, spot, flat, today, spots)
            delta_then = scenario_mod.strategy_delta(view.strategy, spot, asked_here, today, spots)
            # Each strike's delta, now and under the scenario: how far it is
            # from the money is what moves it, and this is where that shows.
            legs = []
            for leg in view.strategy.legs:
                own = spots.get(scenario_mod.leg_underlying(leg), spot)
                if leg.is_future and leg.mark is not None:
                    own = leg.mark
                d_now = scenario_mod.leg_delta(leg, own, flat, today)
                d_then = scenario_mod.leg_delta(leg, own, asked_here, today)
                legs.append(
                    {
                        "side": "short" if leg.is_short else "long",
                        "quantity": leg.quantity,
                        "right": (
                            leg.option_type.value
                            if leg.option_type
                            else ("futures" if leg.is_future else "shares")
                        ),
                        "strike": leg.strike,
                        "expiration": leg.expiration,
                        "underlying_now": own,
                        "underlying_then": None if own is None else own * (Decimal(1) + shift),
                        "delta_now": d_now,
                        "delta_then": d_then,
                        "position_delta_now": None if d_now is None else d_now * leg.signed_quantity,
                        "position_delta_then": None if d_then is None else d_then * leg.signed_quantity,
                    }
                )
            if now is None:
                now_total = None
            elif now_total is not None:
                now_total += now
            if then is None:
                then_total = None
            elif then_total is not None:
                then_total += then

            rows.append(
                {
                    "id": view.strategy.id,
                    "underlying": view.strategy.underlying,
                    "name": view.named_name or view.strategy.strategy_type.value,
                    "structure": view.strategy.strategy_type.value,
                    "dte": view.risk.dte,
                    "price": spot,
                    "price_then": None if spot is None else spot * (Decimal(1) + shift),
                    "beta": beta,
                    "live_pnl": view.pnl.open_pnl,
                    "now": now,
                    "then": then,
                    "change": change,
                    "delta_now": delta_now,
                    "delta_then": delta_then,
                    "legs": legs,
                    "priced": now is not None and then is not None,
                }
            )

        rows.sort(key=lambda r: (r["change"] is None, r["change"] or ZERO))

        def level(symbol: str) -> Decimal | None:
            quote = self._quotes.get(symbol)
            return None if quote is None else (quote.mark or quote.last)

        return {
            # The real levels the dials are drawn in: SPY's price and VIX,
            # so a move reads as "SPY to 627" and "VIX to 25", not as a
            # percentage of something the page never showed.
            "spy": level(greeks.REFERENCE_SYMBOL),
            "vix": level(VIX_SYMBOL),
            "price_shift": price_shift,
            "iv_shift": iv_shift,
            "days": days,
            "by_beta": by_beta,
            "positions": rows,
            "now": now_total,
            "then": then_total,
            "change": None if now_total is None or then_total is None else then_total - now_total,
            "unpriced": sum(1 for r in rows if not r["priced"]),
        }

    async def closed_views(self, limit: int = 200) -> list[StrategyView]:
        today = market_today()
        closed = [s for s in self._strategies if not s.is_open]
        closed.sort(key=lambda s: s.closed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [self._view(s, today, None) for s in closed[:limit]]

    async def summary(self) -> PortfolioSummary:
        await self.ensure_fresh()
        if self._balances_cache is None:
            accounts = [a for a in await self._client.accounts() if not a.is_closed]
            self._balances_cache = await self._build_summary(
                accounts, [s for s in self._strategies if s.is_open]
            )
        return self._balances_cache

    async def portfolio_greeks(self) -> greeks.GreekTotals:
        """Beta-weighted delta, theta and vega for the open book.

        Built as a side effect of the summary because both need the same
        balances call; asking for it first is what guarantees it is populated.
        """
        await self.summary()
        return self._greeks

    async def _capture_entry_context(self, open_strategies: list[Strategy]) -> int:
        """Record IV rank and short-strike delta for positions opened just now.

        These two are what turn "my win rate is 74%" into "my win rate is 81%
        when I sell above 35 IV rank and 62% when I sell below it", which is the
        difference between a scoreboard and something that changes a decision.

        Neither is in the transaction record, so they cannot be recovered for a
        trade opened two years ago. The temptation is to backfill with today's
        figures; that would file today's volatility as the reason for a trade
        made in a different market, and every slice built on it would be
        fiction. So capture is limited to positions opened within the last few
        days, and everything older stays honestly unknown.
        """
        cutoff = market_today() - timedelta(days=_ENTRY_CONTEXT_GRACE_DAYS)
        captured = 0

        for strategy in open_strategies:
            if strategy.opened_at.date() < cutoff:
                continue

            if strategy.iv_rank_at_entry is None:
                quote = self._quotes.get(strategy.underlying)
                if quote is not None and quote.iv_rank is not None:
                    strategy.iv_rank_at_entry = quote.iv_rank
                    captured += 1
                if quote is not None and strategy.underlying_price_at_entry is None:
                    price = quote.mark or quote.last
                    if price is not None:
                        strategy.underlying_price_at_entry = price

            if strategy.short_delta_at_entry is None:
                deltas = [abs(leg.delta) for leg in strategy.short_legs if leg.delta is not None]
                if deltas:
                    strategy.short_delta_at_entry = max(deltas)
                    captured += 1

        return captured

    @staticmethod
    def _margin_group_for(underlying: str, groups: Mapping[str, Decimal]) -> str | None:
        """Which margin group an underlying belongs to.

        Futures options report a contract month ("/ZSF7", "/ZSX6") while the
        broker margins the product ("/ZS"), and both of those months sit in the
        same group. Resolving to the group first is what stops each of them
        claiming the whole requirement.
        """
        key = underlying.strip().upper()
        if key in groups:
            return key
        if key.startswith("/"):
            matches = [g for g in groups if g.startswith("/") and key.startswith(g)]
            if matches:
                # Longest prefix wins, so /MES beats /M if both ever appear.
                return max(matches, key=len)
        return None

    async def _load_prior_closes(self, accounts: list[Any]) -> None:
        """The broker's close price for every symbol currently held.

        This is the basis "P&L today" is measured from, and it comes from
        tastytrade rather than from this app's own history: the platform
        backfills it with the fill price for anything opened today, so a
        position that did not exist yesterday still reports the right move,
        and the number on this page matches the number on theirs.
        """
        closes: dict[str, Decimal] = {}
        sizes: dict[str, Decimal] = {}
        for account in accounts:
            with suppress(Exception):
                for position in await self._client.positions(account):
                    symbol = getattr(position, "symbol", None)
                    if not symbol:
                        continue
                    value = getattr(position, "close_price", None)
                    if value is not None:
                        closes[str(symbol)] = Decimal(str(value))
                    # The contract size, from the broker rather than backed out
                    # of the fill. An outright futures buy moves no cash -- the
                    # value on the row is zero -- so there is nothing to divide,
                    # and /ZBZ6 was coming out at a multiplier of 1: a contract
                    # worth $1,000 a point priced as though it were worth one.
                    size = getattr(position, "multiplier", None)
                    if size is not None and Decimal(str(size)) > 0:
                        sizes[str(symbol)] = Decimal(str(size))
        if closes:
            self._prior_close = closes
        if sizes:
            self._contract_size = sizes

    def _apply_prior_closes(self, strategies: Sequence[Strategy]) -> None:
        for strategy in strategies:
            for leg in strategy.legs:
                leg.prior_close = self._prior_close.get(leg.symbol)
                size = self._contract_size.get(leg.symbol)
                if size is not None and size != leg.multiplier:
                    leg.multiplier = size

    async def _attribute_buying_power(self, accounts: list[Any], open_strategies: list[Strategy]) -> None:
        """Set ``buying_power_used`` on each open strategy from the margin report.

        Without this the figure is never populated, which silently kills the one
        metric that actually ranks strategies for a premium seller: profit per
        buying-power-day. A credit spread earning $50 on $500 over ten days
        beats one earning $80 on $2,000 over forty, and win rate hides that.

        The broker margins a whole product as one group — which is also how the
        risk works, since two short /ZB structures offset each other — so a
        strategy's share is apportioned by the credit it took in, as a proxy for
        how much of the group's risk is its own. The share is an estimate; the
        group total is exact.
        """
        # Summed across accounts, not overwritten by the last one read. The
        # positions are shown as one book, so the requirement behind them has
        # to be the whole requirement: /ZB held in two accounts is margined
        # twice, and reporting one of the two would understate what the product
        # is holding and flatter every return-on-margin figure built on it.
        margins: dict[str, Decimal] = {}
        for account in accounts:
            try:
                for group, requirement in (
                    await self._client.margin_by_underlying(account)
                ).items():
                    margins[group] = margins.get(group, ZERO) + requirement
            except Exception:
                logger.warning("Could not read margin requirements", exc_info=True)

        if not margins:
            return

        # Group by the margin group, never by the strategy's own underlying:
        # /ZSF7 and /ZSX6 are one /ZS requirement between them.
        by_group: dict[str, list[Strategy]] = {}
        for strategy in open_strategies:
            group = self._margin_group_for(strategy.underlying, margins)
            if group is not None:
                by_group.setdefault(group, []).append(strategy)

        for group, members in by_group.items():
            requirement = margins[group]
            weights = [abs(s.net_credit) or Decimal(1) for s in members]
            total = sum(weights, ZERO)
            for strategy, weight in zip(members, weights, strict=True):
                share = (weight / total) if total else Decimal(1) / Decimal(len(members))
                strategy.buying_power_used = (requirement * share).quantize(Decimal("0.01"))

    async def _build_summary(self, accounts: list[Any], open_strategies: list[Strategy]) -> PortfolioSummary:
        """Totals across every account, because the user has more than one.

        Reporting a single account's net liq while showing all accounts'
        positions would make the concentration figures nonsense.
        """
        totals: dict[str, Decimal] = dict.fromkeys(
            (
                "net_liquidating_value",
                "cash_balance",
                "used_derivative_buying_power",
                "derivative_buying_power",
                "maintenance_requirement",
            ),
            ZERO,
        )
        numbers: list[str] = []
        for acct in accounts:
            balances = await self._client.balances(acct)
            numbers.append(balances.account_number)
            for name in totals:
                totals[name] += getattr(balances, name)

        open_pnl: Decimal | None = ZERO
        day_open: Decimal | None = None
        day_covers = 0
        for s in open_strategies:
            computed = pnl_mod.compute_pnl(s)
            # One unpriced strategy makes the portfolio total unknowable. Saying
            # so beats quietly reporting a number that is missing a position.
            if computed.open_pnl is None:
                open_pnl = None
            elif open_pnl is not None:
                open_pnl += computed.open_pnl

            # The open positions' move, summed over the ones the broker gave a
            # close price for. One it did not is left out and counted, rather
            # than folded in as a zero.
            moved = pnl_mod.day_change(s)
            if moved is not None:
                day_open = (day_open or ZERO) + moved
                day_covers += 1

        # The account's own day: what it is worth now less what it closed at
        # last session. This is the figure the broker prints under the net liq,
        # and unlike the sum of the open positions it includes whatever was
        # closed today -- a trade bought back this morning is gone from the
        # positions list, but the money it made is in the account.
        today = market_today()
        if self._prior_net_liq_for != today:
            total: Decimal | None = ZERO
            for acct in accounts:
                closed_at = await self._client.prior_close_net_liq(acct, today)
                if closed_at is None:
                    total = None
                    break
                total += closed_at
            # Last session's close does not change during the session, so it is
            # fetched once a day rather than on every poll.
            self._prior_net_liq = total
            self._prior_net_liq_for = today
        prior_net_liq = self._prior_net_liq
        day_change = (
            None if prior_net_liq is None else totals["net_liquidating_value"] - prior_net_liq
        )

        reference = self._quotes.get(greeks.REFERENCE_SYMBOL)
        self._greeks = greeks.portfolio_greeks(
            open_strategies,
            self._quotes,
            reference_price=(reference.mark or reference.last) if reference else None,
        )

        year_start = date(market_today().year, 1, 1)
        realized_ytd = sum(
            (
                s.realized_pnl
                for s in self._strategies
                if not s.is_open
                and s.closed_at
                and s.closed_at.date() >= year_start
                # An unconfirmed expiry is excluded for the same reason
                # analytics excludes it: one guess must not move the year.
                and not s.outcome_unverified
            ),
            ZERO,
        )

        return PortfolioSummary(
            account_number=" + ".join(numbers) if len(numbers) > 1 else numbers[0],
            net_liquidating_value=totals["net_liquidating_value"],
            cash_balance=totals["cash_balance"],
            buying_power_used=totals["used_derivative_buying_power"],
            buying_power_available=totals["derivative_buying_power"],
            maintenance_requirement=totals["maintenance_requirement"],
            open_strategies=len(open_strategies),
            net_delta=self._greeks.beta_weighted_delta,
            net_theta=self._greeks.theta,
            open_pnl=open_pnl,
            realized_pnl_ytd=realized_ytd,
            as_of=datetime.now(UTC),
            day_change=day_change if day_change is not None else day_open,
            day_change_open=day_open,
            day_change_of=day_covers,
        )

    def _strategy_by_id(self, strategy_id: str) -> Strategy | None:
        """A stored trade, or one of the merged rows the Positions tab shows.

        A row for a strategy the user named has an id of its own ("named:ns-…")
        because it is several of his trades shown as one. It is not in the
        journal, so anything that looks a trade up by id has to be able to build
        it — otherwise the payoff diagram silently 404s on exactly the positions
        he grouped himself.
        """
        found = next((s for s in self._strategies if s.id == strategy_id), None)
        if found is not None:
            return found
        if not strategy_id.startswith("named:"):
            return None
        wanted = strategy_id.removeprefix("named:")
        named = next((n for n in self._named if n.id == wanted), None)
        if named is None:
            return None
        merged = self._merge_open_members(named)
        return next((m for m in merged if m.id == strategy_id), None)

    async def payoff_curve(self, strategy_id: str, points: int = 81) -> dict[str, object] | None:
        """P&L at expiration across a range of underlying prices.

        The range is anchored on the strikes rather than on the current price, so
        the picture always contains the structure: a strangle whose underlying has
        run far past the short call still shows both wings and where the trade
        turns over. Points outside a sensible band tell the reader nothing.
        """
        strategy = self._strategy_by_id(strategy_id)
        if strategy is None:
            return None

        strikes = [leg.strike for leg in strategy.legs if leg.strike is not None]
        quote = self._quote_for(strategy)
        spot = (quote.mark or quote.last) if quote else None
        anchors = [*strikes, *([spot] if spot else [])]
        if not anchors:
            return None

        low, high = min(anchors), max(anchors)
        pad = max((high - low) * Decimal("0.35"), high * Decimal("0.06"))
        low, high = max(low - pad, Decimal("0.01")), high + pad
        step = (high - low) / (points - 1)

        curve = []
        for i in range(points):
            price = low + step * i
            curve.append({"price": price, "pnl": pnl_mod.payoff_at(strategy, price)})

        # A diagonal's legs do not expire together, so payoff_at() — which
        # settles every leg on the same day — draws a shape rather than a
        # valuation, and breakevens(), max_profit() and max_loss() all refuse
        # it outright. The page was left drawing a confident line under the
        # words "at expiration" with a dash where its breakeven should be. It
        # now says which of the two it is holding, and reads the crossings off
        # the line actually drawn so the picture is at least self-consistent.
        exact = not strategy.is_multi_expiration
        crossings = pnl_mod.breakevens(strategy) if exact else _zero_crossings(curve)
        note = None
        if not exact:
            expirations = strategy.expirations
            near, far = expirations[0], expirations[-1]
            note = (
                f"These legs do not expire together. The line settles them all on "
                f"{near:%b %-d, %Y}, but the {far:%b %-d, %Y} leg still has "
                f"{(far - near).days} days of time value left that day, and no strike "
                f"arithmetic can know what that is worth. Read the shape, not the numbers."
            )

        return {
            "points": curve,
            "breakevens": crossings,
            "exact": exact,
            "note": note,
            "strikes": sorted(set(strikes)),
            "spot": spot,
            "max_profit": pnl_mod.max_profit(strategy),
            "max_loss": pnl_mod.max_loss(strategy),
        }

    def needs_review(self) -> list[dict[str, object]]:
        """Closed trades whose outcome could not be confirmed.

        Surfaced rather than buried: these are excluded from every total, so a
        user comparing the dashboard against their broker statement needs to
        know which trades are missing and why.
        """
        return [
            {
                "id": s.id,
                "underlying": s.underlying,
                "structure": s.strategy_type.value,
                "closed": s.closed_at.date().isoformat() if s.closed_at else None,
                "recorded_pnl": str(s.realized_pnl),
                "legs": [f"{leg.direction.value.lower()} {leg.quantity:g} x {leg.symbol}" for leg in s.legs],
                "why": (
                    "Closed at expiry with no closing transaction, so the recorded cash "
                    "flows may be missing an exercise or assignment. Excluded from every "
                    "total until confirmed."
                ),
            }
            for s in analytics.unverified_strategies(self._strategies)
        ]

    # ---------------------------------------------------------------- events

    async def _notice_crossings(self, views: list[StrategyView]) -> list[str]:
        """Log the moments a position crosses something the user cares about.

        Each crossing is announced once. Without that, a trade sitting past its
        profit target would produce the same notice on every refresh for three
        weeks, and a log that repeats itself is a log nobody reads. The event
        table itself is the record of what has already been said.
        """
        noticed: list[str] = []

        async def notice(kind: str, view: StrategyView, summary: str, severity: str = "notable") -> None:
            sid = view.strategy.id
            if await self._db.has_noticed(kind, sid):
                return
            await self._db.record(
                kind,
                summary,
                severity=severity,
                strategy_id=sid,
                detail={
                    "underlying": view.strategy.underlying,
                    "structure": view.strategy.strategy_type.value,
                    "dte": view.risk.dte,
                    "pct_of_credit": (
                        None if view.pnl.pct_of_credit is None else str(view.pnl.pct_of_credit)
                    ),
                    "open_pnl": None if view.pnl.open_pnl is None else str(view.pnl.open_pnl),
                },
            )
            noticed.append(f"{kind}: {summary}")

        for view in views:
            s_, pnl, risk = view.strategy, view.pnl, view.risk
            name = f"{s_.underlying} {s_.strategy_type.value}"

            captured = pnl.pct_of_max_profit
            if captured is not None and captured >= self._rules.profit_target_pct:
                await notice(
                    "position.hit_profit_target",
                    view,
                    f"{name} reached {captured:.0%} of max profit — your target is "
                    f"{self._rules.profit_target_pct:.0%}.",
                )

            entry_dte = view.strategy.front_entry_dte
            if (
                risk.dte is not None
                and 0 <= risk.dte <= self._rules.dte_exit
                # Not news on something sold short-dated on purpose.
                and (entry_dte is None or entry_dte > self._rules.dte_exit)
            ):
                await notice(
                    "position.entered_gamma_window",
                    view,
                    f"{name} is at {risk.dte} DTE — inside your {self._rules.dte_exit}-day line.",
                )

            pct = pnl.pct_of_credit
            if pct is not None and pct <= -self._rules.stop_loss_multiple:
                await notice(
                    "position.passed_stop",
                    view,
                    f"{name} is down {abs(pct):.0%} of the credit collected — past your "
                    f"{self._rules.stop_loss_multiple:g}x stop.",
                    severity="warning",
                )

            if risk.breached:
                side = risk.breached_side or "short strike"
                await notice(
                    "position.breached",
                    view,
                    f"{name} has traded through its {side}.",
                    severity="warning",
                )

            if risk.assignment_risk:
                await notice(
                    "position.short_leg_in_the_money",
                    view,
                    f"{name} has a short leg in the money with {risk.dte} days left.",
                    severity="warning",
                )

            quote = self._quotes.get(s_.underlying)
            exps = s_.expirations
            if quote and quote.earnings_date and exps and quote.earnings_date <= exps[0]:
                await notice(
                    "position.earnings_before_expiry",
                    view,
                    f"{name} has earnings on {quote.earnings_date.isoformat()}, before its "
                    f"{exps[0].isoformat()} expiry.",
                )

        return noticed

    async def _notice_lifecycle(self, before: dict[str, Strategy]) -> None:
        """Log positions that appeared or closed since the previous sync."""
        after = {s.id: s for s in self._strategies}

        for sid, strategy in after.items():
            if sid in before:
                continue
            await self._db.record(
                "position.opened",
                f"{strategy.underlying} {strategy.strategy_type.value} opened for "
                f"{strategy.net_credit:+.2f}.",
                strategy_id=sid,
                detail={"underlying": strategy.underlying, "credit": str(strategy.net_credit)},
            )

        for sid, strategy in after.items():
            was_open = sid in before and before[sid].is_open
            if was_open and not strategy.is_open:
                await self._db.record(
                    "position.closed",
                    f"{strategy.underlying} {strategy.strategy_type.value} closed for "
                    f"{strategy.realized_pnl:+.2f}.",
                    severity="notable",
                    strategy_id=sid,
                    detail={
                        "underlying": strategy.underlying,
                        "realized": str(strategy.realized_pnl),
                        "assigned": strategy.closed_by_assignment,
                    },
                )

    async def events(
        self,
        *,
        limit: int = 100,
        since: datetime | None = None,
        min_severity: str | None = None,
    ) -> list[dict[str, object]]:
        return await self._db.events(limit=limit, since=since, min_severity=min_severity)

    async def event_counts(self, since: datetime | None = None) -> dict[str, int]:
        return await self._db.event_counts(since)

    # ------------------------------------------------------ named strategies

    def open_legs(self) -> list[dict[str, object]]:
        """Every open leg as its own line.

        The broker's order grouping is not the same thing as a strategy, and the
        user asked to be the one who decides. So nothing is combined here: each
        leg stands alone with everything needed to judge it, and the strategies
        it belongs to are listed beside it rather than replacing it.
        """
        today = market_today()
        net_liq = self._balances_cache.net_liquidating_value if self._balances_cache else None
        membership = self._membership()

        rows: list[dict[str, object]] = []
        for strategy in self._strategies:
            if not strategy.is_open:
                continue
            view = self._view(strategy, today, net_liq)
            quote = self._quote_for(strategy)
            for leg in strategy.legs:
                dte = leg.dte(today)
                rows.append(
                    {
                        "leg_id": f"{strategy.id}::{leg.symbol}",
                        "trade_id": strategy.id,
                        "account": strategy.account_number,
                        "underlying": strategy.underlying,
                        "product": playbook.product_of(strategy.underlying),
                        "symbol": leg.symbol,
                        "side": leg.direction.value,
                        "right": (
                            leg.option_type.value
                            if leg.option_type
                            else ("futures" if leg.is_future else "shares")
                        ),
                        "strike": None if leg.strike is None else str(leg.strike),
                        "expiration": None if leg.expiration is None else leg.expiration.isoformat(),
                        "dte": dte,
                        "quantity": str(leg.quantity),
                        "open_price": str(leg.open_price),
                        "mark": None if leg.mark is None else str(leg.mark),
                        "mark_estimated": leg.mark_estimated,
                        "delta": None if leg.delta is None else str(leg.delta),
                        "theta": None if leg.theta is None else str(leg.theta),
                        "iv": None if leg.iv is None else str(leg.iv),
                        # The leg's own date, not the trade's. Deciding which
                        # legs belong together is mostly a question of what was
                        # opened together, and a diagonal's weekly is months
                        # younger than the trade that holds it.
                        "opened_at": (leg.opened_at or strategy.opened_at).isoformat(),
                        "trade_opened_at": strategy.opened_at.isoformat(),
                        "legs_in_trade": len(strategy.legs),
                        "underlying_price": (
                            None
                            if quote is None or (quote.mark or quote.last) is None
                            else str(quote.mark or quote.last)
                        ),
                        # Reference only. Risk still belongs to the whole
                        # structure; these are here so a leg can be read, not
                        # so it can be judged.
                        "trade_structure": strategy.strategy_type.value,
                        "trade_open_pnl": (None if view.pnl.open_pnl is None else str(view.pnl.open_pnl)),
                        "in_strategies": membership.get(strategy.id, []),
                    }
                )

        rows.sort(key=lambda r: (str(r["underlying"]), str(r["expiration"] or ""), str(r["right"])))
        return rows

    def _membership(self) -> dict[str, list[dict[str, str]]]:
        out: dict[str, list[dict[str, str]]] = {}
        for named in self._named:
            for trade_id in named.member_ids:
                out.setdefault(trade_id, []).append({"id": named.id, "name": named.name})
        return out

    async def load_named(self) -> None:
        rows = await self._db.get_named_strategies()
        self._named = [playbook.from_row(row) for row in rows]

    async def create_named_strategy(
        self, name: str, trade_ids: Sequence[str], note: str | None = None
    ) -> dict[str, object]:
        """Define a strategy from trades the user picked, and name it."""
        label = (name or "").strip()
        if not label:
            raise ValueError("A strategy needs a name")
        by_id = {s.id: s for s in self._strategies}
        chosen = [by_id[tid] for tid in dict.fromkeys(trade_ids) if tid in by_id]
        if not chosen:
            raise ValueError("None of those trades exist")

        products = {playbook.product_of(s.underlying) for s in chosen}
        if len(products) > 1:
            # The user was explicit: a strategy lives in one ticker.
            raise ValueError(
                f"Those legs span {len(products)} products ({', '.join(sorted(products))}). "
                "A strategy has to be one ticker."
            )

        signature = playbook.signature_of(chosen)
        # No slashes in the id: a futures product is "/ZB", and an id carrying
        # that breaks every URL it appears in.
        slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{signature.product}-{label}").strip("-").lower()
        strategy_id = f"ns-{slug[:48]}"
        named = playbook.NamedStrategy(
            id=strategy_id,
            name=label,
            product=signature.product,
            signature=signature,
            member_ids=[s.id for s in chosen],
            note=note,
            name_reading=playbook.read_name(label),
        )

        await self._db.save_named_strategy(playbook.to_row(named))
        await self._db.set_named_members(strategy_id, named.member_ids)
        await self._db.record(
            "strategy.defined",
            f'Defined "{label}" on {signature.product} from {len(chosen)} trade(s).',
            severity="notable",
            detail={"id": strategy_id, "shape": signature.describe()},
        )
        await self.load_named()
        return self.named_detail(strategy_id)

    def _named_live(
        self, named: playbook.NamedStrategy, today: date
    ) -> dict[str, object]:
        """What this strategy is doing right now, position by position.

        A named strategy is a thing he is *running*, not a folder of receipts.
        The question the page has to answer first is what it is carrying today —
        delta, theta, how close the underlying is to a short strike, how many
        days are left — and only then how the idea has done in the past.
        """
        net_liq = self._balances_cache.net_liquidating_value if self._balances_cache else None
        views = self._named_views(named, today, net_liq)

        rows: list[dict[str, object]] = []
        for view in views:
            v = view.values
            risk = view.risk
            rows.append(
                {
                    "id": view.strategy.id,
                    "underlying": view.strategy.underlying,
                    "structure": view.strategy.strategy_type.value,
                    "parts": view.parts,
                    "legs": _leg_lines(view.strategy),
                    "opened": view.strategy.opened_at.date(),
                    "days_held": v["days_in_trade"],
                    "dte": risk.dte,
                    "expiry": v["expiry"],
                    "dte_at_entry": v["dte_at_entry"],
                    "open_pnl": view.pnl.open_pnl,
                    "day_change": v["day_change"],
                    "credit": v["credit"],
                    "pct_of_credit": view.pnl.pct_of_credit,
                    "pct_of_max_profit": view.pnl.pct_of_max_profit,
                    "max_loss": view.pnl.max_loss,
                    "bp": v["bp"],
                    "net_delta": v["net_delta"],
                    "delta_dollars": v["delta_dollars"],
                    "theta": v["theta"],
                    "vega": v["vega"],
                    "underlying_price": v["price"],
                    "iv_rank": v["iv_rank"],
                    "distance_pct": risk.distance_to_short_pct,
                    "expected_move": risk.expected_move,
                    "short_delta": risk.worst_short_delta,
                    "risk_level": risk.level.value,
                    "breached": risk.breached,
                    "breached_side": risk.breached_side,
                    "verdict": v["verdict"],
                    "reasons": [r.message for r in risk.reasons],
                }
            )

        def total(key: str) -> Decimal | None:
            values = [row[key] for row in rows]
            if not values or any(value is None for value in values):
                # One unpriced leg makes a book total a guess. The page says
                # "not all of it is priced" rather than printing a smaller
                # number as if it were the whole.
                return None
            return sum((Decimal(str(value)) for value in values), ZERO)

        dtes = [row["dte"] for row in rows if row["dte"] is not None]
        return {
            "positions": rows,
            "count": len(rows),
            "open_pnl": total("open_pnl"),
            "day_change": total("day_change"),
            "credit": total("credit"),
            "delta_dollars": total("delta_dollars"),
            "theta": total("theta"),
            "vega": total("vega"),
            "bp": total("bp"),
            "dte": min(dtes) if dtes else None,
            "worst_risk": (
                max((view.risk.level for view in views), key=lambda level: level.rank).value
                if views
                else None
            ),
        }

    def named_strategies(self) -> list[dict[str, object]]:
        return [self.named_detail(named.id) for named in self._named]

    def named_detail(self, strategy_id: str) -> dict[str, object]:
        named = next((n for n in self._named if n.id == strategy_id), None)
        if named is None:
            raise KeyError(strategy_id)
        by_id = {s.id: s for s in self._strategies}
        members = [by_id[tid] for tid in named.member_ids if tid in by_id]
        stats = analytics.performance(members)
        today = market_today()

        return {
            "id": named.id,
            "name": named.name,
            "product": named.product,
            "note": named.note,
            "name_reading": named.name_reading,
            "shape": named.signature.describe(),
            "signature": {
                "legs": [leg.describe() for leg in named.signature.legs],
                "expiry_pattern": named.signature.expiry_pattern,
                "window_minutes": named.signature.window_minutes,
            },
            "member_count": len(members),
            "live": self._named_live(named, today),
            "members": [
                _member_row(s, today)
                for s in sorted(members, key=lambda s: s.opened_at, reverse=True)
            ],
            "performance": _stats_dict(stats),
        }

    def named_matches(self, strategy_id: str) -> dict[str, object]:
        """Every older trade that could belong here, each with how sure we are.

        Nothing is filtered by confidence on the way out. The user sets the bar
        himself with a slider, and a list that had already been cut at some
        threshold could not answer "what would I be including at 90?".
        """
        named = next((n for n in self._named if n.id == strategy_id), None)
        if named is None:
            raise KeyError(strategy_id)

        by_id = {s.id: s for s in self._strategies}
        members = [by_id[tid] for tid in named.member_ids if tid in by_id]
        claimed = {tid for other in self._named for tid in other.member_ids}
        today = market_today()

        candidates: list[dict[str, object]] = []
        for trade in self._strategies:
            if trade.id in claimed or not trade.legs:
                continue
            if playbook.product_of(trade.underlying) != named.product:
                continue

            verdict = confidence.assess(trade, members, name=named.name)
            if verdict.confidence <= 0.0 and not verdict.reasons:
                continue
            shape_score, shape_reasons, shape_misses = playbook.score_match(
                named.signature, playbook.signature_of([trade])
            )
            candidates.append(
                {
                    **_member_row(trade, today),
                    "trade_id": trade.id,
                    "confidence": verdict.confidence,
                    "verdict": verdict.verdict,
                    # The reasons are the confidence module's own. The shape
                    # score rides along as a second opinion, not as filler for
                    # an empty list — two vocabularies in one column read as a
                    # contradiction ("same structure" beside "different legs").
                    "reasons": verdict.reasons,
                    "misses": verdict.misses,
                    "shape_reasons": list(shape_reasons),
                    "shape_misses": list(shape_misses),
                    "unknowns": verdict.unknowns,
                    "not_applicable": verdict.not_applicable,
                    "roll_of": verdict.roll_of,
                    "shape_score": shape_score,
                    # Kept so older callers and the MCP tools keep working.
                    "score": verdict.confidence,
                    "confident": verdict.confidence >= confidence.DEFAULT_THRESHOLD,
                }
            )

        candidates.sort(key=lambda c: (-float(c["confidence"]), str(c["opened"])))
        sure = [c for c in candidates if float(c["confidence"]) >= confidence.DEFAULT_THRESHOLD]
        return {
            "strategy_id": strategy_id,
            "threshold": confidence.DEFAULT_THRESHOLD,
            "candidates": candidates,
            # The old split, still populated, so nothing that reads this breaks.
            "confident": sure,
            "review": [c for c in candidates if c not in sure and float(c["confidence"]) >= 0.5],
            "confident_pnl": str(sum((Decimal(str(c["realized_pnl"])) for c in sure), ZERO)),
        }

    # --------------------------------------------------------------- settings

    async def get_settings(self) -> dict[str, object]:
        """Preferences, with the defaults filled in."""
        stored = await self._db.all_settings()

        def columns(key: str, catalogue: Sequence[Any]) -> list[str]:
            raw = stored.get(key)
            known = {f.id for f in catalogue}
            if raw:
                chosen = [c for c in json.loads(raw) if c in known]
                if chosen:
                    return chosen
            return [f.id for f in catalogue if f.default]

        # A column list saved before a field existed cannot contain it, and the
        # user has no way to know a new one is there. So new defaults are folded
        # into a saved list once — recorded, and reversible from the picker like
        # any other choice.
        async def catch_up(key: str, catalogue: Sequence[Any], added: Sequence[str]) -> None:
            if stored.get(f"{key}_migrated") == _COLUMN_MIGRATION:
                return
            current = columns(key, catalogue)
            known = {f.id for f in catalogue}
            fresh = [c for c in added if c in known and c not in current]
            if fresh:
                current = [c for c in current if c != "position_on_risk"]
                # Each new column lands beside the one it belongs with rather
                # than at the front, so a list the user arranged stays arranged.
                beside = {
                    "verdict": "underlying",
                    "day_change": "open_pnl",
                    "pct_of_max_profit": "pct_of_credit",
                }
                for column in fresh:
                    after = beside.get(column)
                    at = current.index(after) + 1 if after in current else len(current)
                    current.insert(at, column)
                await self._db.set_setting(key, json.dumps(current))
            await self._db.set_setting(f"{key}_migrated", _COLUMN_MIGRATION)

        await catch_up(
            "position_columns",
            indicators.STRATEGY_FIELDS,
            ("verdict", "day_change", "pct_of_max_profit"),
        )
        stored = await self._db.all_settings()

        return {
            "match_threshold": float(
                stored.get("match_threshold", confidence.DEFAULT_THRESHOLD)
            ),
            "match_threshold_default": confidence.DEFAULT_THRESHOLD,
            "position_columns": columns("position_columns", indicators.STRATEGY_FIELDS),
            "leg_columns": columns("leg_columns", indicators.LEG_FIELDS),
        }

    async def set_setting(self, key: str, value: str) -> dict[str, object]:
        if key == "match_threshold":
            number = float(value)
            if not 0.5 <= number <= 1.0:
                raise ValueError("The confidence bar has to be between 50% and 100%.")
            value = str(number)
            note = f"Confidence bar for counting a trade set to {number:.0%}."
        elif key in ("position_columns", "leg_columns"):
            catalogue = (
                indicators.STRATEGY_FIELDS if key == "position_columns" else indicators.LEG_FIELDS
            )
            known = {f.id for f in catalogue}
            chosen = [c for c in json.loads(value) if c in known]
            if not chosen:
                raise ValueError("Pick at least one column.")
            value = json.dumps(chosen)
            where = "Positions" if key == "position_columns" else "Leg detail"
            note = f"{where} now shows {len(chosen)} columns."
        else:
            raise KeyError(key)
        await self._db.set_setting(key, value)
        await self._db.record(
            "settings.changed", note, severity="info", detail={"key": key, "value": value}
        )
        return await self.get_settings()

    async def match_threshold(self) -> float:
        return float((await self.get_settings())["match_threshold"])

    async def performance_by_named(
        self, start: date | None = None, end: date | None = None
    ) -> dict[str, PerformanceStats]:
        """Performance grouped by the strategies the user defined himself.

        This is the grouping that matters to him: not "naked call" and "put
        credit spread", which are shapes the classifier read off the fills, but
        "my /ZB strangle" and "Headge PPMCC", which are the ideas he actually
        trades. A trade counts towards a strategy when he put it there, or when
        the app is at least as sure as his own confidence bar.

        Anything that belongs to no strategy is reported under "not in a
        strategy" rather than dropped, so the totals still add up to his book.
        """
        threshold = await self.match_threshold()
        window = {s.id for s in self._window(start, end)}
        by_id = {s.id: s for s in self._strategies}

        groups: dict[str, list[Strategy]] = {}
        claimed: set[str] = set()
        for named in self._named:
            members = list(named.member_ids)
            report = self.named_matches(named.id)
            members += [
                str(c["trade_id"])
                for c in report["candidates"]  # type: ignore[index]
                if float(c["confidence"]) >= threshold  # type: ignore[index]
            ]
            trades = [
                by_id[tid]
                for tid in dict.fromkeys(members)
                if tid in by_id and tid in window
            ]
            claimed.update(t.id for t in trades)
            label = f"{named.name} ({named.product})"
            groups.setdefault(label, []).extend(trades)

        rest = [s for s in self._strategies if s.id in window and s.id not in claimed]
        if rest:
            groups["not in a strategy"] = rest

        stats = {k: analytics.performance(v) for k, v in groups.items()}
        return dict(
            sorted(stats.items(), key=lambda kv: (kv[0] == "not in a strategy", -kv[1].trades))
        )

    def named_matches_all(self) -> dict[str, object]:
        """Candidates for every named strategy in one answer.

        The threshold slider has to be instant, and it can only be instant if
        the page already holds every candidate and its confidence. Fetching per
        strategy on each drag would make the control feel broken.
        """
        return {named.id: self.named_matches(named.id) for named in self._named}

    async def adopt_matches(self, strategy_id: str, trade_ids: Sequence[str]) -> dict[str, object]:
        """Add trades to a named strategy, permanently and on the record.

        What a strategy contains decides its win rate, so a change to it has to
        be traceable. Without an entry here, membership could move and the only
        way to find out would be to read the database by hand — which is exactly
        the position this app exists to get the user out of.
        """
        ids = list(trade_ids)
        await self._db.set_named_members(strategy_id, ids)
        await self.load_named()
        named = next((n for n in self._named if n.id == strategy_id), None)
        await self._db.record(
            "strategy.adopted",
            f'Added {len(ids)} trade(s) to "{named.name if named else strategy_id}".',
            severity="notable",
            detail={"id": strategy_id, "trade_ids": ids},
        )
        return self.named_detail(strategy_id)

    async def drop_member(self, strategy_id: str, trade_id: str) -> dict[str, object]:
        removed = await self._db.remove_named_member(strategy_id, trade_id)
        await self.load_named()
        named = next((n for n in self._named if n.id == strategy_id), None)
        if removed:
            await self._db.record(
                "strategy.dropped",
                f'Removed a trade from "{named.name if named else strategy_id}".',
                severity="notable",
                detail={"id": strategy_id, "trade_id": trade_id},
            )
        return self.named_detail(strategy_id)

    async def delete_named_strategy(self, strategy_id: str) -> bool:
        removed = await self._db.delete_named_strategy(strategy_id)
        await self.load_named()
        return removed

    # -------------------------------------------------------------- pairing

    def _reconstruct(
        self,
        history: list,
        account_number: str,
        overrides: dict[str, str],
        rules: pairing.PairingRules,
    ) -> list[Strategy]:
        """Build a book in two passes, because one is not enough.

        The first pass groups legs the broker filled under one order id. That
        misses everything legged in: sell the call, sell the put a minute later,
        and the journal holds two trades where the trader made one decision. So
        a second pass looks for trades that belong together and rebuilds with
        them merged.

        Only structurally unmistakable pairs are merged on their own — a matched
        strangle or vertical at one expiry, in one size, opened within minutes.
        Everything else waits for an answer, because a wrong merge is worse than
        a missed one: a missed one is visible as two trades, a wrong one is
        invisible.
        """
        first = grouping.close_expired(
            grouping.match_rolls(
                grouping.build_strategies(history, account_number, manual_overrides=overrides),
                separated=self._separated_rolls(),
            ),
            market_today(),
        )

        inferred = pairing.apply_pairings(pairing.find_candidates(first, rules), rules)
        if not inferred:
            return first

        combined = {**inferred, **overrides}  # an explicit answer always wins
        return grouping.close_expired(
            grouping.match_rolls(
                grouping.build_strategies(history, account_number, manual_overrides=combined),
                separated=self._separated_rolls(),
            ),
            market_today(),
        )

    def _separated_rolls(self) -> frozenset[tuple[str, str]]:
        """The pairs he has said are not one trade, as (parent, absorbed).

        The same table that remembers an answered proposal remembers this one,
        and for the same reason: an answer given once is an answer.
        """
        return frozenset(
            pair for pair, decision in self._roll_decisions.items() if decision == "separate"
        )

    def _describe_pattern(self, pattern: str) -> str:
        """A sentence about what a pattern covers, captured before it is applied."""
        rules = pairing.PairingRules(decisions={})
        for candidate in pairing.find_candidates(self._strategies, rules):
            if candidate.pattern != pattern:
                continue
            by_id = {s.id: s for s in self._strategies}
            left, right = by_id.get(candidate.left_id), by_id.get(candidate.right_id)
            if left is None or right is None:
                continue
            return (
                f"{candidate.underlying} {candidate.kind.value}: "
                f"{left.strategy_type.value} + {right.strategy_type.value}, "
                f"opened {candidate.gap_seconds // 60} min apart, "
                f"combined {candidate.combined_pnl:+,.0f}"
            )
        return _readable_pattern(pattern)

    async def pairing_decisions(self) -> list[dict[str, object]]:
        """Questions already answered, so they can be seen and changed."""
        return [
            {
                "pattern": row["pattern"],
                "decision": row["decision"],
                "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
                # Answers given before descriptions were stored, and merges
                # whose two halves no longer exist to describe, still have to
                # read as something: an answer nobody can identify is an answer
                # nobody can change their mind about.
                "note": row["note"] or _readable_pattern(row["pattern"]),
            }
            for row in await self._db.get_pairing_decisions()
        ]

    async def undo_pairing(self, pattern: str) -> int:
        await self._db.clear_pairing_rule(pattern)
        await self._db.record(
            "pairing.reopened",
            f"Reopened: {pattern}",
            severity="notable",
            detail={"pattern": pattern},
        )
        return await self.rebuild()

    async def pairing_candidates(self) -> list[dict[str, object]]:
        """Pairs still waiting on a decision, biggest money first.

        Every fact that bears on "one trade or two" is included, because the
        question cannot be answered from a structure name. The strongest signal
        is not the shape at all: two positions opened a minute apart AND closed
        a minute apart were one decision, whatever their legs look like.
        """
        rules = pairing.PairingRules(decisions=await self._db.get_pairing_rules())  # type: ignore[arg-type]
        found = pairing.find_candidates(self._strategies, rules)
        by_id = {s.id: s for s in self._strategies}

        out: list[dict[str, object]] = []
        seen_patterns: set[str] = set()
        for candidate in found:
            if candidate.confidence is pairing.Confidence.CERTAIN:
                continue
            like_this = sum(1 for c in found if c.pattern == candidate.pattern)
            first_of_pattern = candidate.pattern not in seen_patterns
            seen_patterns.add(candidate.pattern)

            left = by_id.get(candidate.left_id)
            right = by_id.get(candidate.right_id)
            if left is None or right is None:
                continue

            out.append(
                {
                    "pattern": candidate.pattern,
                    "left_id": candidate.left_id,
                    "right_id": candidate.right_id,
                    "underlying": candidate.underlying,
                    "kind": candidate.kind.value,
                    "would_become": candidate.merged_type.value,
                    "gap_minutes": candidate.gap_seconds // 60,
                    "reason": candidate.reason,
                    "combined_pnl": str(candidate.combined_pnl),
                    "others_like_it": like_this - 1,
                    "first_of_pattern": first_of_pattern,
                    "links": self._pair_links(left, right),
                    "sides": [self._pair_side(left), self._pair_side(right)],
                }
            )
        return out

    def _pair_side(self, strategy: Strategy) -> dict[str, object]:
        today = market_today()
        return {
            "id": strategy.id,
            "structure": strategy.strategy_type.value,
            "realized_pnl": str(strategy.realized_pnl),
            "credit": str(strategy.net_credit),
            "opened_at": strategy.opened_at.isoformat(),
            "closed_at": strategy.closed_at.isoformat() if strategy.closed_at else None,
            "is_open": strategy.is_open,
            "days_held": ((strategy.closed_at - strategy.opened_at).days if strategy.closed_at else None),
            "dte_at_entry": strategy.dte_at_entry,
            "roll_count": strategy.roll_count,
            "legs": [
                {
                    "side": leg.direction.value,
                    "quantity": str(leg.quantity),
                    "right": leg.option_type.value if leg.option_type else "shares",
                    "strike": None if leg.strike is None else str(leg.strike),
                    "expiration": None if leg.expiration is None else leg.expiration.isoformat(),
                    "dte_now": leg.dte(today),
                    "open_price": str(leg.open_price),
                    "delta": None if leg.delta is None else str(leg.delta),
                }
                for leg in strategy.legs
            ],
        }

    @staticmethod
    def _pair_links(left: Strategy, right: Strategy) -> list[dict[str, object]]:
        """The specific things these two trades do and do not share."""
        links: list[dict[str, object]] = []

        def add(label: str, value: str, weight: str) -> None:
            links.append({"label": label, "value": value, "weight": weight})

        gap = abs((right.opened_at - left.opened_at).total_seconds())
        add(
            "Opened",
            "the same minute" if gap < 60 else f"{int(gap // 60)} minutes apart",
            "strong" if gap < 300 else "weak",
        )

        # The most telling signal there is. Two positions opened together AND
        # closed together were one decision, whatever the legs look like.
        if left.closed_at and right.closed_at:
            close_gap = abs((right.closed_at - left.closed_at).total_seconds())
            add(
                "Closed",
                "the same minute"
                if close_gap < 60
                else f"{int(close_gap // 3600)} hours apart"
                if close_gap < 86400
                else f"{int(close_gap // 86400)} days apart",
                "strong" if close_gap < 300 else "weak",
            )
        elif left.is_open and right.is_open:
            add("Closed", "both still open", "neutral")
        else:
            still = left.id if left.is_open else right.id
            add("Closed", f"one closed, one still open ({still.split(':')[-1]})", "weak")

        left_exp = set(left.expirations)
        right_exp = set(right.expirations)
        if left_exp and right_exp:
            if left_exp == right_exp:
                add("Expiry", f"identical ({sorted(left_exp)[0].isoformat()})", "strong")
            else:
                spread = abs((sorted(right_exp)[0] - sorted(left_exp)[0]).days)
                add("Expiry", f"{spread} days apart", "weak" if spread > 45 else "neutral")

        if left.dte_at_entry is not None and right.dte_at_entry is not None:
            diff = abs(left.dte_at_entry - right.dte_at_entry)
            add(
                "DTE at entry",
                f"{left.dte_at_entry} and {right.dte_at_entry}"
                + (" — the same" if diff == 0 else f" — {diff} apart"),
                "strong" if diff == 0 else "neutral" if diff <= 7 else "weak",
            )

        deltas = []
        for trade in (left, right):
            values = [abs(leg.delta) for leg in trade.legs if leg.delta is not None]
            deltas.append(max(values) if values else None)
        if deltas[0] is not None and deltas[1] is not None:
            diff = abs(deltas[0] - deltas[1])
            add(
                "Delta now",
                f"{deltas[0]:.2f} and {deltas[1]:.2f}"
                + (" — matched" if diff <= Decimal("0.05") else f" — {diff:.2f} apart"),
                "strong" if diff <= Decimal("0.05") else "neutral",
            )

        sizes = [sum((leg.quantity for leg in t.legs), ZERO) for t in (left, right)]
        add(
            "Size",
            f"{sizes[0]:g} and {sizes[1]:g}" + (" — equal" if sizes[0] == sizes[1] else ""),
            "strong" if sizes[0] == sizes[1] else "neutral",
        )

        credits = [left.net_credit, right.net_credit]
        both_credit = all(c > ZERO for c in credits)
        both_debit = all(c < ZERO for c in credits)
        add(
            "Cash",
            f"{credits[0]:+,.0f} and {credits[1]:+,.0f}"
            + (
                " — both credits"
                if both_credit
                else " — both debits"
                if both_debit
                else " — one paid, one received"
            ),
            "neutral" if (both_credit or both_debit) else "weak",
        )

        if left.roll_count or right.roll_count:
            add("Rolls", f"{left.roll_count} and {right.roll_count}", "neutral")

        return links

    async def decide_pairing(self, pattern: str, decision: str, note: str | None = None) -> int:
        """Record an answer and rebuild the journal with it applied.

        The answer is described as it is stored, because once a pair is merged
        the two halves no longer exist to be described later — and an answered
        question the user cannot see is an answered question they cannot change.
        """
        if note is None:
            note = self._describe_pattern(pattern)
        await self._db.set_pairing_rule(pattern, decision, note)
        await self._db.record(
            "pairing.decided",
            f"{'Merged' if decision == 'merge' else 'Kept apart'}: {pattern}",
            severity="notable",
            detail={"pattern": pattern, "decision": decision},
        )
        return await self.rebuild()

    # ------------------------------------------------------------- questions

    async def ask(self, question: str) -> dict[str, object]:
        """Queue a question from the dashboard for a Claude session to answer.

        A queue rather than a live call, because this app holds no Anthropic
        key — there is nothing for it to ask. The trade is honest: answers
        arrive when a session next looks, and the interface says so instead of
        imitating a chat window that would simply never reply.

        The fact sheet at the moment of asking is stored alongside, so the
        answer is written against the book as it stood when the question
        occurred to him, not as it stands whenever it gets picked up.
        """
        text = question.strip()
        if not text:
            raise ValueError("A question cannot be empty")
        if len(text) > 4000:
            raise ValueError("That question is too long")

        import json

        from tastydesk.api.serialize import encode

        try:
            context = json.dumps(encode(self.position_facts()))
        except Exception:  # a question is still worth queueing without context
            logger.warning("Could not capture facts for a question", exc_info=True)
            context = None

        question_id = await self._db.ask(text, context)
        await self._db.record(
            "question.asked",
            f"Asked: {text[:140]}",
            severity="notable",
            detail={"question_id": question_id},
        )
        return {"id": question_id, "question": text}

    async def pending_questions(self) -> list[dict[str, object]]:
        return await self._db.pending_questions()

    async def answer(self, question_id: int, answer: str) -> bool:
        if not answer.strip():
            raise ValueError("An empty answer is worse than none")
        stored = await self._db.answer_question(question_id, answer.strip())
        if stored:
            await self._db.record(
                "question.answered",
                f"Answered question {question_id}.",
                detail={"question_id": question_id},
            )
        return stored

    async def question_thread(self, limit: int = 30) -> list[dict[str, object]]:
        return await self._db.question_thread(limit)

    async def forget_question(self, question_id: int) -> bool:
        return await self._db.delete_question(question_id)

    # ----------------------------------------------------------------- facts

    def position_facts(self) -> dict[str, object]:
        """Everything computable about the book, with no verdict attached.

        This is the seam between the two halves of this application. Code owns
        the arithmetic — P&L, distances, deltas, days, dollar amounts — because
        those must match the broker to the cent and read the same on every
        refresh. Judgment is left out on purpose: what to do about a position,
        which of them matter today, whether a pattern in the history is real,
        are questions where a hardcoded threshold is a poor substitute for
        reading the situation.

        So no field here says "danger". The thresholds the dashboard sorts by
        still exist in :mod:`tastydesk.core.risk`, because a table has to put
        something at the top before anyone has looked at it, but they are a
        fallback ordering rather than the opinion. The opinion comes from
        whoever reads this.
        """
        today = market_today()
        summary = self._balances_cache
        net_liq = summary.net_liquidating_value if summary else None
        views = [self._view(s, today, net_liq) for s in self._strategies if s.is_open]

        return {
            "as_of": datetime.now(MARKET_TZ).isoformat(),
            "market_date": today.isoformat(),
            "rules": {
                "profit_target_pct": str(self._rules.profit_target_pct),
                "dte_exit": self._rules.dte_exit,
                "stop_loss_multiple": str(self._rules.stop_loss_multiple),
            },
            "portfolio": self._portfolio_facts(summary, views),
            "positions": [self._one_position_facts(v, today) for v in views],
            "history_by_underlying": self._history_facts(),
            "needs_review": [
                {
                    "id": s.id,
                    "underlying": s.underlying,
                    "structure": s.strategy_type.value,
                    "closed": s.closed_at.date().isoformat() if s.closed_at else None,
                    "recorded_pnl": str(s.realized_pnl),
                    "why": (
                        "Closed by expiry with no closing transaction found, so the recorded "
                        "cash flows may be missing an exercise or assignment. Excluded from "
                        "every total until confirmed."
                    ),
                }
                for s in analytics.unverified_strategies(self._strategies)
            ],
        }

    def _portfolio_facts(
        self, summary: PortfolioSummary | None, views: list[StrategyView]
    ) -> dict[str, object]:
        if summary is None:
            return {"available": False, "why": "no balances fetched yet; run a sync"}
        used = summary.buying_power_used
        net_liq = summary.net_liquidating_value
        g = self._greeks
        return {
            "available": True,
            "net_liq": str(net_liq),
            "cash": str(summary.cash_balance),
            "buying_power_used": str(used),
            "buying_power_used_pct_of_net_liq": (str(used / net_liq) if net_liq else None),
            "buying_power_available": str(summary.buying_power_available),
            "open_positions": len(views),
            # Named for what it is. A bare "net delta" across futures and
            # equities would be read as a number, and it is not one: the only
            # figure that adds up across this book is beta-weighted to SPY.
            "beta_weighted_delta_spy": (
                None if g.beta_weighted_delta is None else str(g.beta_weighted_delta)
            ),
            "beta_weighted_delta_units": "SPY share equivalents",
            "dollar_delta": None if g.dollar_delta is None else str(g.dollar_delta),
            "dollars_per_1pct_spy": (
                None if g.dollars_per_spy_percent is None else str(g.dollars_per_spy_percent)
            ),
            "net_theta": None if summary.net_theta is None else str(summary.net_theta),
            "net_theta_units": "dollars per day",
            "net_vega": None if g.vega is None else str(g.vega),
            "net_vega_units": "dollars per 1 point of implied volatility",
            "greeks_unmeasured": {
                "no_beta": list(g.missing_beta),
                "no_price": list(g.missing_price),
                "legs_without_delta": len(g.missing_delta),
            },
            "open_pnl": None if summary.open_pnl is None else str(summary.open_pnl),
            "realized_pnl_ytd": None if summary.realized_pnl_ytd is None else str(summary.realized_pnl_ytd),
            "underlyings": sorted({v.strategy.underlying for v in views}),
        }

    def _one_position_facts(self, view: StrategyView, today: date) -> dict[str, object]:
        s, pnl, risk = view.strategy, view.pnl, view.risk
        quote = self._quotes.get(s.underlying) or self._quotes.get(product_root(s.underlying))
        days_held = (datetime.now(UTC) - s.opened_at).days

        # What a one-point move in the underlying is worth, in money. The raw
        # sum of leg deltas is in contract units and does not travel.
        price = (quote.mark or quote.last) if quote else None
        legs_priced = [greeks.leg_dollar_delta(leg, price) for leg in s.legs]
        dollar_delta = None if any(d is None for d in legs_priced) else sum(legs_priced, ZERO)

        earnings = quote.earnings_date if quote else None
        exp = s.expirations[0] if s.expirations else None

        return {
            "id": s.id,
            "underlying": s.underlying,
            "structure": s.strategy_type.value,
            "risk_profile": s.risk_profile.value,
            "opened": s.opened_at.date().isoformat(),
            "days_held": days_held,
            "roll_count": s.roll_count,
            "notes": s.notes,
            "legs": [
                {
                    "side": leg.direction.value,
                    "kind": (leg.option_type.value if leg.option_type else "shares"),
                    "strike": None if leg.strike is None else str(leg.strike),
                    "expiry": None if leg.expiration is None else leg.expiration.isoformat(),
                    "quantity": str(leg.quantity),
                    "open_price": str(leg.open_price),
                    "mark": None if leg.mark is None else str(leg.mark),
                    "mark_estimated": leg.mark_estimated,
                    "delta": None if leg.delta is None else str(leg.delta),
                    "theta": None if leg.theta is None else str(leg.theta),
                    "iv": None if leg.iv is None else str(leg.iv),
                }
                for leg in s.legs
            ],
            "money": {
                "credit_collected": str(pnl.net_credit),
                "cost_to_close": None if pnl.cost_to_close is None else str(pnl.cost_to_close),
                "open_pnl": None if pnl.open_pnl is None else str(pnl.open_pnl),
                "pct_of_credit": None if pnl.pct_of_credit is None else str(pnl.pct_of_credit),
                "max_profit": None if pnl.max_profit is None else str(pnl.max_profit),
                "pct_of_max_profit": (None if pnl.pct_of_max_profit is None else str(pnl.pct_of_max_profit)),
                "max_loss": None if pnl.max_loss is None else str(pnl.max_loss),
                "max_loss_is_undefined": pnl.max_loss is None,
                "pct_of_max_loss": None if pnl.pct_of_max_loss is None else str(pnl.pct_of_max_loss),
                "fully_quoted": pnl.fully_quoted,
                "legs_quoted": f"{pnl.quoted_legs}/{pnl.total_legs}",
                "buying_power_used": None if s.buying_power_used is None else str(s.buying_power_used),
                "pct_of_net_liq": None if risk.pct_of_net_liq is None else str(risk.pct_of_net_liq),
            },
            "position": {
                "dte": risk.dte,
                "expiration": None if exp is None else exp.isoformat(),
                "multiple_expirations": s.is_multi_expiration,
                "underlying_price": None if view.underlying_price is None else str(view.underlying_price),
                "distance_to_short_pct": (
                    None if risk.distance_to_short_pct is None else str(risk.distance_to_short_pct)
                ),
                # Named for the thing rather than for the statistic. Claude's
                # answers come back into the app's own tabs, and a field called
                # sigma is a word the user does not want to read there.
                "expected_move_by_expiry": (
                    None if risk.expected_move is None else str(risk.expected_move)
                ),
                "worst_short_delta": (
                    None if risk.worst_short_delta is None else str(risk.worst_short_delta)
                ),
                # In the underlying's own units (contracts x multiplier), which
                # is meaningful for one product and meaningless across several.
                "net_delta_in_underlying_units": (
                    None if s.net_position_delta is None else str(s.net_position_delta)
                ),
                "dollar_delta": None if dollar_delta is None else str(dollar_delta),
                "net_theta": None if s.net_theta is None else str(s.net_theta),
                "breached": risk.breached,
                "breached_side": risk.breached_side,
                "short_leg_in_the_money": risk.assignment_risk,
                "pin_risk": risk.pin_risk,
            },
            "context": {
                "iv_rank_now": None if view.iv_rank is None else str(view.iv_rank),
                "iv_rank_at_entry": (None if s.iv_rank_at_entry is None else str(s.iv_rank_at_entry)),
                "underlying_price_at_entry": (
                    None if s.underlying_price_at_entry is None else str(s.underlying_price_at_entry)
                ),
                "dte_at_entry": s.dte_at_entry,
                "earnings_date": None if earnings is None else earnings.isoformat(),
                "days_to_earnings": None if earnings is None else (earnings - today).days,
                "earnings_before_expiry": (None if (earnings is None or exp is None) else earnings <= exp),
                "ex_dividend_date": (
                    None
                    if (quote is None or quote.ex_dividend_date is None)
                    else quote.ex_dividend_date.isoformat()
                ),
            },
            "rule_flags": {
                "at_or_past_profit_target": (
                    None
                    if pnl.pct_of_max_profit is None
                    else pnl.pct_of_max_profit >= self._rules.profit_target_pct
                ),
                "inside_dte_exit": None if risk.dte is None else risk.dte <= self._rules.dte_exit,
                "past_stop_multiple": (
                    None
                    if pnl.pct_of_credit is None
                    else pnl.pct_of_credit <= -self._rules.stop_loss_multiple
                ),
            },
            # The fallback ordering, labelled as such. Present so the dashboard
            # has something to sort by before anyone has read the facts above.
            "computed_level": risk.level.value,
            "computed_score": risk.score,
        }

    def _history_facts(self) -> dict[str, object]:
        """How past trades in each underlying actually went.

        Context for reading an open position: a strangle on a name where the
        last four went badly is a different proposition from the same strangle
        on a name that has paid every time.
        """
        out: dict[str, object] = {}
        for underlying, stats in analytics.by_underlying(self._strategies).items():
            if stats.trades == 0:
                continue
            out[underlying] = {
                "closed_trades": stats.trades,
                "wins": stats.wins,
                "losses": stats.losses,
                "win_rate": stats.win_rate,
                "total_pnl": str(stats.total_pnl),
                "expectancy": None if stats.expectancy is None else str(stats.expectancy),
                "avg_days_in_trade": (
                    None if stats.avg_days_in_trade is None else str(stats.avg_days_in_trade)
                ),
            }
        return out

    def loss_shape(self, start: date | None = None, end: date | None = None) -> dict[str, object]:
        """How each strategy loses, not just how often it wins."""
        from dataclasses import asdict

        window = self._window(start, end)
        overall = analytics.loss_shape(window, "all closed trades")
        per_type = analytics.loss_shape_by_strategy(window)
        return {
            "overall": asdict(overall),
            "by_strategy": {name: asdict(shape) for name, shape in per_type.items()},
        }

    # -------------------------------------------------------------- grouping

    def roll_candidates(self) -> list[object]:
        """Rolls executed as two orders, minus the ones already answered.

        An answer given once is an answer. "Separate" used to live in the
        page's own memory and die with the page, so a backlog worked through
        last week came back in full the next time the tab was opened.
        """
        answered = self._roll_decisions
        return [
            c
            for c in grouping.suggest_roll_links(self._strategies)
            if (c.closed_id, c.opened_id) not in answered
        ]

    async def load_roll_decisions(self) -> None:
        with suppress(Exception):
            self._roll_decisions = await self._db.roll_decisions()

    async def decide_roll(self, closed_id: str, opened_id: str, decision: str) -> None:
        """Record what the user said about one proposed roll."""
        if decision not in ("linked", "separate"):
            raise ValueError("A roll is either linked or separate.")
        await self._db.set_roll_decision(closed_id, opened_id, decision)
        self._roll_decisions[(closed_id, opened_id)] = decision

    async def separate_all_rolls(self) -> int:
        """Answer the whole backlog at once, for a list nobody will click through.

        Only ever 'separate': merging ninety pairs on one click would rewrite
        the journal on an assumption, and this button exists precisely because
        the user already knows these are not rolls.
        """
        pending = [
            (c.closed_id, c.opened_id, "separate")
            for c in grouping.suggest_roll_links(self._strategies)
            if (c.closed_id, c.opened_id) not in self._roll_decisions
        ]
        written = await self._db.set_roll_decisions(pending)
        for closed_id, opened_id, decision in pending:
            self._roll_decisions[(closed_id, opened_id)] = decision
        if written:
            await self._db.record(
                "grouping.rolls_separated",
                f"Marked {written} proposed rolls as separate trades.",
                severity="notable",
                detail={"count": written},
            )
        return written

    async def link_strategies(self, strategy_ids: Sequence[str]) -> int:
        """Merge several trades into one, by the user's explicit instruction.

        The group id is the earliest member's, so the merged trade keeps a
        recognisable identity and a rebuild lands on the same id every time.
        """
        if len(strategy_ids) < 2:
            raise ValueError("Linking needs at least two strategies")
        known = {s.id: s for s in self._strategies}
        missing = [i for i in strategy_ids if i not in known]
        if missing:
            raise KeyError(f"Unknown strategy id(s): {', '.join(missing)}")

        group = min(strategy_ids, key=lambda i: (known[i].opened_at, i))
        for sid in strategy_ids:
            await self._db.set_manual_override(sid, group)
        await self._db.record(
            "grouping.linked",
            f"Linked {len(strategy_ids)} trades into {group}.",
            severity="notable",
            strategy_id=group,
            detail={"strategy_ids": list(strategy_ids)},
        )
        # Linking is an answer too: without this the pair comes back as a
        # proposal the moment the strategies are rebuilt under their new id.
        if len(strategy_ids) == 2:
            with suppress(Exception):
                await self.decide_roll(strategy_ids[0], strategy_ids[1], "linked")
        await self.rebuild()
        return len(strategy_ids)

    async def unlink_strategy(self, strategy_id: str) -> None:
        await self._db.clear_manual_override(strategy_id)
        await self.rebuild()

    async def rebuild(self) -> int:
        """Re-derive strategies from stored transactions, without hitting the broker.

        Used after a grouping change: the transactions have not moved, only the
        instruction about how to read them. It has to walk the accounts exactly
        as a sync does — rebuilding every account's history under one account
        number produces ids that belong to no account, and skipping the pairing
        pass means an answer is saved and then silently ignored.
        """
        async with self._lock:
            stored_all = await self._db.get_transactions()
            _, dropped = _as_transactions(stored_all)
            if dropped:
                await self._db.record(
                    "sync.failed",
                    f"{dropped} of {len(stored_all)} stored transactions could not be read back.",
                    severity="error",
                    detail={"dropped": dropped, "stored": len(stored_all)},
                )
                raise SyncError(
                    f"{dropped} of {len(stored_all)} stored transactions could not be read back; "
                    "run a full sync to repair the table."
                )

            overrides = await self._db.get_manual_overrides()
            rules = pairing.PairingRules(decisions=await self._db.get_pairing_rules())  # type: ignore[arg-type]

            accounts = sorted({row["account_number"] for row in stored_all if row.get("account_number")})
            rebuilt: list[Strategy] = []
            per_account: list[tuple[str, list[Strategy]]] = []
            for account_number in accounts:
                rows = await self._db.get_transactions(account_number=account_number)
                history, _ = _as_transactions(rows)
                built = self._reconstruct(history, account_number, overrides, rules)
                per_account.append((account_number, built))
                rebuilt.extend(built)

            # Buying power is not in the transaction record — it comes from the
            # broker's margin report — so a rebuild that skipped this step wrote
            # nulls over it and silently killed profit-per-buying-power-day, the
            # one metric that actually ranks strategies for a premium seller.
            with suppress(Exception):
                live = [a for a in await self._client.accounts() if not a.is_closed]
                await self._attribute_buying_power(live, [s for s in rebuilt if s.is_open])

            for account_number, built in per_account:
                await self._db.save_strategies(built, reconcile_account=account_number)

            self._strategies = rebuilt
            with suppress(Exception):
                await self.refresh_marks()
            return len(rebuilt)

    # ------------------------------------------------------------- analytics

    def _window(self, start: date | None = None, end: date | None = None) -> list[Strategy]:
        """The trades a report covers. See :func:`analytics.in_period`."""
        return analytics.in_period(self._strategies, start, end)

    def periods(self) -> dict[str, object]:
        """Which years and months actually contain closed trades.

        Offering a month with nothing in it invites the reader to conclude they
        had a flat month when in fact they had no month at all.
        """
        years: dict[int, int] = {}
        months: dict[str, int] = {}
        first: date | None = None
        last: date | None = None
        for s in analytics.closed_strategies(self._strategies):
            assert s.closed_at is not None
            day = s.closed_at.date()
            years[day.year] = years.get(day.year, 0) + 1
            key = f"{day.year:04d}-{day.month:02d}"
            months[key] = months.get(key, 0) + 1
            first = day if first is None or day < first else first
            last = day if last is None or day > last else last
        return {
            "first_close": first.isoformat() if first else None,
            "last_close": last.isoformat() if last else None,
            "years": [{"year": y, "trades": n} for y, n in sorted(years.items(), reverse=True)],
            "months": [{"month": m, "trades": n} for m, n in sorted(months.items(), reverse=True)],
        }

    def performance(self, start: date | None = None, end: date | None = None) -> PerformanceStats:
        return analytics.performance(self._window(start, end))

    def performance_by_strategy(
        self, start: date | None = None, end: date | None = None
    ) -> dict[str, PerformanceStats]:
        return {
            str(k): v for k, v in analytics.by_strategy_type(self._window(start, end)).items()
        }

    def performance_by_underlying(
        self, start: date | None = None, end: date | None = None
    ) -> dict[str, PerformanceStats]:
        return analytics.by_underlying(self._window(start, end))

    def performance_by_bucket(
        self, dimension: str, start: date | None = None, end: date | None = None
    ) -> dict[str, PerformanceStats]:
        return analytics.by_bucket(self._window(start, end), dimension)

    async def rules(self) -> dict[str, object]:
        mae = await self._db.max_adverse_excursion()
        return analytics.rule_adherence(self._strategies, self._rules, mae_by_strategy=mae or None)

    # ---------------------------------------------------------------- health

    async def health(self) -> dict[str, object]:
        client_health: ClientHealth | None = None
        try:
            client_health = await self._client.health()
        except Exception as exc:
            self._last_error = str(exc)

        return {
            "credentials_present": bool(client_health and client_health.credentials_present),
            "session_ok": bool(client_health and client_health.session_ok),
            "account_count": client_health.account_count if client_health else 0,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "last_error": (client_health.last_error if client_health else None) or self._last_error,
            "strategies_loaded": len(self._strategies),
            "open_strategies": sum(1 for s in self._strategies if s.is_open),
            "checked_at": datetime.now(UTC).isoformat(),
        }


class _MarkClientAdapter:
    """Bridges core.client.TastyClient to the protocol MarkService expects.

    MarkService wants a ``get_session()`` plus optional batched helpers; the
    account client exposes a session manager and its own rate-limited
    ``quotes``/``market_metrics``. Routing through this adapter keeps every
    outbound request under the same rate limiter instead of opening a second,
    unthrottled path to the broker.
    """

    def __init__(self, client: TastyClient) -> None:
        self._client = client

    async def get_session(self):  # noqa: ANN201 - structural typing
        return await self._client._session()  # noqa: SLF001

    async def market_data(self, **buckets):  # noqa: ANN003, ANN201
        result = await self._client.quotes(
            option_symbols=buckets.get("options"),
            equity_symbols=buckets.get("equities"),
            index_symbols=buckets.get("indices"),
            future_symbols=buckets.get("futures"),
            future_option_symbols=buckets.get("future_options"),
        )
        return list(result.values())

    async def market_metrics(self, symbols):  # noqa: ANN001, ANN201
        result = await self._client.market_metrics(symbols)
        return list(result.values())


def _as_transactions(rows: list[dict]) -> tuple[list, int]:
    """Rehydrate stored rows into SDK Transaction objects.

    Returns the objects and how many rows could not be read. The count matters:
    a partial rebuild is indistinguishable from a small account, so the caller
    refuses rather than presenting a short history as the whole story.
    """
    from tastytrade.account import Transaction

    out = []
    dropped = 0
    for row in rows:
        try:
            out.append(Transaction.model_validate(row))
        except Exception:
            dropped += 1
            logger.warning("Stored transaction %s could not be read back", row.get("id"), exc_info=True)
    return out, dropped
