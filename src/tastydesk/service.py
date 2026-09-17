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
from collections.abc import Sequence
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

    async def payoff_curve(self, strategy_id: str, points: int = 81) -> dict[str, object] | None:
        """P&L at expiration across a range of underlying prices.

        The range is anchored on the strikes rather than on the current price, so
        the picture always contains the structure: a strangle whose underlying has
        run far past the short call still shows both wings and where the trade
        turns over. Points outside a sensible band tell the reader nothing.
        """
        strategy = next((s for s in self._strategies if s.id == strategy_id), None)
        if strategy is None:
            return None

        strikes = [leg.strike for leg in strategy.legs if leg.strike is not None]
        quote = self._quotes.get(strategy.underlying)
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

        return {
            "points": curve,
            "breakevens": pnl_mod.breakevens(strategy),
            "strikes": sorted(set(strikes)),
            "spot": spot,
            "max_profit": pnl_mod.max_profit(strategy),
            "max_loss": pnl_mod.max_loss(strategy),
        }

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
        return {"id": question_id, "question": text}

    async def pending_questions(self) -> list[dict[str, object]]:
        return await self._db.pending_questions()

    async def answer(self, question_id: int, answer: str) -> bool:
        if not answer.strip():
            raise ValueError("An empty answer is worse than none")
        return await self._db.answer_question(question_id, answer.strip())

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
        }

    def _portfolio_facts(
        self, summary: PortfolioSummary | None, views: list[StrategyView]
    ) -> dict[str, object]:
        if summary is None:
            return {"available": False, "why": "no balances fetched yet; run a sync"}
        used = summary.buying_power_used
        net_liq = summary.net_liquidating_value
        return {
            "available": True,
            "net_liq": str(net_liq),
            "cash": str(summary.cash_balance),
            "buying_power_used": str(used),
            "buying_power_used_pct_of_net_liq": (str(used / net_liq) if net_liq else None),
            "buying_power_available": str(summary.buying_power_available),
            "open_positions": len(views),
            "net_delta": None if summary.net_delta is None else str(summary.net_delta),
            "net_theta": None if summary.net_theta is None else str(summary.net_theta),
            "open_pnl": None if summary.open_pnl is None else str(summary.open_pnl),
            "realized_pnl_ytd": None if summary.realized_pnl_ytd is None else str(summary.realized_pnl_ytd),
            "underlyings": sorted({v.strategy.underlying for v in views}),
        }

    def _one_position_facts(self, view: StrategyView, today: date) -> dict[str, object]:
        s, pnl, risk = view.strategy, view.pnl, view.risk
        quote = self._quotes.get(s.underlying)
        days_held = (datetime.now(UTC) - s.opened_at).days

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
                "distance_to_short_sigma": (
                    None if risk.distance_to_short_sigma is None else str(risk.distance_to_short_sigma)
                ),
                "worst_short_delta": (
                    None if risk.worst_short_delta is None else str(risk.worst_short_delta)
                ),
                "net_delta": None if s.net_position_delta is None else str(s.net_position_delta),
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

    # -------------------------------------------------------------- grouping

    def roll_candidates(self) -> list[object]:
        """Rolls that were executed as two orders and so were not auto-detected."""
        return grouping.suggest_roll_links(self._strategies)

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
        await self.rebuild()
        return len(strategy_ids)

    async def unlink_strategy(self, strategy_id: str) -> None:
        await self._db.clear_manual_override(strategy_id)
        await self.rebuild()

    async def rebuild(self) -> int:
        """Re-derive strategies from stored transactions, without hitting the broker.

        Used after a grouping change: the transactions have not moved, only the
        instruction about how to read them.
        """
        async with self._lock:
            stored = await self._db.get_transactions()
            history, dropped = _as_transactions(stored)
            if dropped:
                raise SyncError(
                    f"{dropped} of {len(stored)} stored transactions could not be read back; "
                    "run a full sync to repair the table."
                )
            overrides = await self._db.get_manual_overrides()
            account = self._strategies[0].account_number if self._strategies else ""
            strategies = grouping.match_rolls(
                grouping.build_strategies(history, account, manual_overrides=overrides)
            )
            await self._db.save_strategies(strategies, reconcile_account=account or None)
            self._strategies = strategies
            return len(strategies)

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
