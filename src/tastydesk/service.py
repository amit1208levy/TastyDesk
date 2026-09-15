"""The application layer: one object that owns syncing, caching and derived views.

Everything the dashboard shows and everything the MCP server answers comes
through here, so the two can never drift apart. That was an explicit
requirement: there must be no gap between what Claude sees in conversation and
what the dashboard renders, and the way to guarantee it is to give them one
code path rather than two that agree by convention.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from tastydesk.core import analytics, grouping
from tastydesk.core import pnl as pnl_mod
from tastydesk.core import risk as risk_mod
from tastydesk.core.analytics import PerformanceStats, RuleSet
from tastydesk.core.client import ClientHealth, TastyClient
from tastydesk.core.db import Database
from tastydesk.core.marks import MarkService
from tastydesk.core.models import (
    ZERO,
    PortfolioSummary,
    Strategy,
    StrategyPnL,
    StrategyRisk,
    UnderlyingQuote,
)

logger = logging.getLogger(__name__)

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
        self._quotes: dict[str, UnderlyingQuote] = {}
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

    async def stop(self) -> None:
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

            account = await self._client.primary_account()

            start_date: date | None = None
            if not full:
                last = await self._db.last_transaction_date()
                if last is not None:
                    start_date = last - timedelta(days=RESYNC_OVERLAP_DAYS)
            if start_date is None:
                start_date = (datetime.now(UTC) - timedelta(days=INITIAL_HISTORY_DAYS)).date()

            rows = await self._client.transactions(account, start_date=start_date)
            imported = await self._db.upsert_transactions(rows)

            stored = await self._db.get_transactions()
            overrides = await self._db.get_manual_overrides()

            history, dropped = _as_transactions(stored)
            if dropped:
                # Falling back to `rows` here would rebuild the entire journal
                # from whatever this one call fetched -- five days, on an
                # incremental sync -- and quietly empty the dashboard. Refusing
                # is the honest failure; an empty portfolio that looks real is not.
                raise SyncError(
                    f"{dropped} of {len(stored)} stored transactions could not be read back "
                    "from the local database, so the rebuild would be incomplete. "
                    "This is a bug in Tasty Desk, not in your account. "
                    "Re-run with a full sync (tastydesk sync --full) to repair the table."
                )

            strategies = grouping.build_strategies(
                history,
                account.account_number,
                manual_overrides=overrides,
            )
            strategies = grouping.match_rolls(strategies)

            await self._db.save_strategies(strategies, reconcile_account=account.account_number)
            self._strategies = strategies

            open_strategies = [s for s in strategies if s.is_open]
            if open_strategies:
                try:
                    await self._marks.refresh(open_strategies)
                except Exception as exc:  # the dashboard is still useful unpriced
                    warnings.append(f"Live prices unavailable: {exc}")
                    logger.warning("Mark refresh failed", exc_info=True)

                symbols = sorted({s.underlying for s in open_strategies})
                try:
                    self._quotes = await self._marks.underlying_quotes(symbols)
                except Exception as exc:
                    warnings.append(f"Underlying quotes unavailable: {exc}")
                    logger.warning("Underlying quote refresh failed", exc_info=True)

            self._balances_cache = await self._build_summary(account, open_strategies)
            self._last_sync = datetime.now(UTC)
            self._last_error = None

            quoted = sum(1 for s in open_strategies if pnl_mod.compute_pnl(s).fully_quoted)

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
        """
        written = 0
        skipped = 0
        now = datetime.now(UTC)
        for view in await self.open_views():
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

    def _view(self, strategy: Strategy, today: date, net_liq: Decimal | None) -> StrategyView:
        computed = pnl_mod.compute_pnl(strategy)
        quote = self._quotes.get(strategy.underlying)
        assessment = risk_mod.assess(strategy, computed, quote, today, net_liq)
        return StrategyView(
            strategy=strategy,
            pnl=computed,
            risk=assessment,
            underlying_price=quote.mark or quote.last if quote else None,
            iv_rank=quote.iv_rank if quote else None,
        )

    async def open_views(self) -> list[StrategyView]:
        today = market_today()
        net_liq = self._balances_cache.net_liquidating_value if self._balances_cache else None
        views = [self._view(s, today, net_liq) for s in self._strategies if s.is_open]
        views.sort(key=lambda v: (v.risk.level.rank, v.risk.score), reverse=True)
        return views

    async def closed_views(self, limit: int = 200) -> list[StrategyView]:
        today = market_today()
        closed = [s for s in self._strategies if not s.is_open]
        closed.sort(key=lambda s: s.closed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [self._view(s, today, None) for s in closed[:limit]]

    async def summary(self) -> PortfolioSummary:
        if self._balances_cache is None:
            account = await self._client.primary_account()
            self._balances_cache = await self._build_summary(
                account, [s for s in self._strategies if s.is_open]
            )
        return self._balances_cache

    async def _build_summary(self, account: object, open_strategies: list[Strategy]) -> PortfolioSummary:
        balances = await self._client.balances(account)  # type: ignore[arg-type]

        open_pnl: Decimal | None = ZERO
        net_delta: Decimal | None = ZERO
        net_theta: Decimal | None = ZERO
        for s in open_strategies:
            computed = pnl_mod.compute_pnl(s)
            # One unpriced strategy makes the portfolio total unknowable. Saying
            # so beats quietly reporting a number that is missing a position.
            if computed.open_pnl is None:
                open_pnl = None
            elif open_pnl is not None:
                open_pnl += computed.open_pnl

            d = s.net_position_delta
            net_delta = None if d is None or net_delta is None else net_delta + d
            t = s.net_theta
            net_theta = None if t is None or net_theta is None else net_theta + t

        year_start = date(market_today().year, 1, 1)
        realized_ytd = sum(
            (
                s.realized_pnl
                for s in self._strategies
                if not s.is_open and s.closed_at and s.closed_at.date() >= year_start
            ),
            ZERO,
        )

        return PortfolioSummary(
            account_number=balances.account_number,
            net_liquidating_value=balances.net_liquidating_value,
            cash_balance=balances.cash_balance,
            buying_power_used=balances.used_derivative_buying_power,
            buying_power_available=balances.derivative_buying_power,
            maintenance_requirement=balances.maintenance_requirement,
            open_strategies=len(open_strategies),
            net_delta=net_delta,
            net_theta=net_theta,
            open_pnl=open_pnl,
            realized_pnl_ytd=realized_ytd,
            as_of=datetime.now(UTC),
        )

    # ------------------------------------------------------------- analytics

    def performance(self) -> PerformanceStats:
        return analytics.performance(self._strategies)

    def performance_by_strategy(self) -> dict[str, PerformanceStats]:
        return {str(k): v for k, v in analytics.by_strategy_type(self._strategies).items()}

    def performance_by_underlying(self) -> dict[str, PerformanceStats]:
        return analytics.by_underlying(self._strategies)

    def performance_by_bucket(self, dimension: str) -> dict[str, PerformanceStats]:
        return analytics.by_bucket(self._strategies, dimension)

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
