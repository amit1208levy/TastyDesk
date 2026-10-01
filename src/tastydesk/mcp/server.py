"""Read-only MCP server over the same engine the dashboard uses.

This is what closes the gap the user asked about: when he asks "what is at risk
today?" in conversation, the answer comes from this server, which calls the same
DeskService the web dashboard calls. There is no second implementation to drift,
and when something breaks he sees the same error in both places.

Every tool here is a read. The OAuth grant carries the ``read`` scope only, so
the broker would reject a trade even if one were attempted, and nothing in this
module attempts one.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from tastydesk.api.serialize import encode, encode_view
from tastydesk.core.auth import CredentialError
from tastydesk.service import DeskService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Tasty Desk exposes a read-only view of the user's tastytrade options account.

Two things to keep in mind when reporting these numbers back:

1. Risk is measured per STRATEGY, never per leg. A put credit spread down 150%
   of its credit whose short put alone is down 300% is not a 300% problem — the
   long put gained at the same time. Quote pct_of_credit and pct_of_max_loss from
   the strategy, and treat per-leg numbers as structural detail only.
2. A null is not a zero. When a price was unavailable the field is null and the
   honest answer is "not quoted right now", never 0.
"""


@asynccontextmanager
async def _lifespan(_server: MCPServer):
    from tastydesk.api.main import build_service

    service = build_service()
    await service.start()
    try:
        yield {"service": service}
    finally:
        await service.stop()


mcp = MCPServer(
    name="tasty-desk",
    title="Tasty Desk",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    lifespan=_lifespan,
)

_service: DeskService | None = None


async def _svc() -> DeskService:
    global _service
    if _service is None:
        from tastydesk.api.main import build_service

        _service = build_service()
        await _service.start()
    return _service


async def _svc_facts() -> dict[str, Any]:
    return (await _svc()).position_facts()


def _guard(exc: Exception) -> dict[str, Any]:
    """Turn a failure into something actionable rather than a stack trace."""
    if isinstance(exc, CredentialError):
        return {"error": "credentials_missing", "detail": str(exc)}
    logger.exception("MCP tool failed")
    return {"error": type(exc).__name__, "detail": str(exc)}


@mcp.tool(description="Account totals: net liq, buying power used, open P&L, net delta and theta.")
async def portfolio_summary() -> dict[str, Any]:
    try:
        return encode(await (await _svc()).summary())
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "Open strategies with strategy-level P&L and risk, worst first. "
        "Each entry carries pct_of_credit (+1.0 = full credit captured, -1.5 = down "
        "150% of credit), pct_of_max_loss for defined-risk structures, DTE, the worst "
        "short-strike delta, and the plain-language reasons behind the danger level."
    )
)
async def list_open_strategies(limit: int = 50) -> dict[str, Any]:
    try:
        views = await (await _svc()).open_views()
        return {"count": len(views), "strategies": [encode_view(v) for v in views[:limit]]}
    except Exception as exc:
        return _guard(exc)


@mcp.tool(description="Closed trades, most recent first, with realized P&L net of fees.")
async def list_closed_strategies(limit: int = 50) -> dict[str, Any]:
    try:
        views = await (await _svc()).closed_views(limit)
        return {"count": len(views), "strategies": [encode_view(v) for v in views]}
    except Exception as exc:
        return _guard(exc)


@mcp.tool(description="Everything about one strategy: legs, greeks, breakevens, risk reasons.")
async def strategy_detail(strategy_id: str) -> dict[str, Any]:
    try:
        service = await _svc()
        for view in (await service.open_views()) + (await service.closed_views(2000)):
            if view.strategy.id == strategy_id:
                from tastydesk.core.pnl import breakevens

                out = encode_view(view)
                out["breakevens"] = encode(breakevens(view.strategy))
                return out
        return {"error": "not_found", "detail": f"No strategy with id {strategy_id}"}
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "Performance statistics. by='overall' | 'strategy' | 'underlying' | "
        "'dte_at_entry' | 'iv_rank_at_entry' | 'short_delta_at_entry'. "
        "Every figure reports its sample size; a high win rate over a handful of "
        "trades is not evidence."
    )
)
async def performance(
    by: Literal[
        "overall", "strategy", "underlying", "dte_at_entry", "iv_rank_at_entry", "short_delta_at_entry"
    ] = "overall",
) -> dict[str, Any]:
    try:
        service = await _svc()
        if by == "overall":
            return encode(service.performance())
        if by == "strategy":
            return encode(service.performance_by_strategy())
        if by == "underlying":
            return encode(service.performance_by_underlying())
        return encode(service.performance_by_bucket(by))
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "How often the user follows his own rules: manage at 50% of max profit, "
        "close or roll at 21 DTE, stop at 2x the credit received. Reports P&L when "
        "followed versus violated, and says when a rule is not measurable rather "
        "than guessing."
    )
)
async def rule_adherence() -> dict[str, Any]:
    try:
        return encode(await (await _svc()).rules())
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "The book held up against Tom King's 2026 trading plan, which the user is trying to "
        "trade like. Today: buying power, delta, theta and vega against Tom's targets, every "
        "open position's size and stop under Tom's own exits, and the market regime from "
        "Tom's four chart tests (SPY and each product held). History: how often each of "
        "Tom's rules was kept, and the P&L when it was kept versus broken. Each finding is a "
        "measurement against a stated rule, not advice; the 'analog' flag marks checks run "
        "on structures Tom does not trade."
    )
)
async def tom_analysis() -> dict[str, Any]:
    try:
        return encode(await (await _svc()).tom_analysis())
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "Tom King's scanner: the entry checklists from his 2026 trading plan (naked puts, "
        "60-DTE futures strangles, the 11x Bear Trap on /MES, 120-DTE /MES puts, 5-DTE SPX put "
        "spreads, Dynamic PMCC) run over a fixed list of liquid products. Each candidate carries "
        "the checks it passed and failed, and for those that pass, legs with real strikes and "
        "deltas from the live chain, the stop and target in dollars, a size capped by Tom's limits, "
        "and how buying power and greeks would read with it on. Candidates match stated rules; "
        "they are not advice. refresh=true rescans instead of using the ten-minute cache."
    )
)
async def tom_scanner(refresh: bool = False) -> dict[str, Any]:
    try:
        return encode(await (await _svc()).scan(refresh=refresh))
    except Exception as exc:
        return _guard(exc)


@mcp.tool(description="Pull the latest transactions and re-price open positions. Read-only.")
async def sync(full: bool = False) -> dict[str, Any]:
    try:
        return encode(await (await _svc()).sync(full=full))
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "The complete computed fact sheet for the open book, with NO verdict attached — "
        "this is the tool to use when asked what to do, what is at risk, or what needs "
        "attention. Per position: every leg with its greeks, the money (credit collected, "
        "P&L, % of credit, % of max loss, whether max loss is undefined at all), the "
        "position (DTE, distance to the short strike beside the move the market prices by "
        "expiry, worst short "
        "delta, breach, whether a short leg is in the money), context (IV rank now and at "
        "entry, earnings and ex-dividend dates, whether earnings falls before expiry), and "
        "flags for the user's own three rules. Plus portfolio totals and how past trades in "
        "each underlying actually went.\n\n"
        "computed_level and computed_score are a FALLBACK ORDERING for the dashboard, not an "
        "opinion — form your own read from the facts. Remember that risk belongs to the "
        "strategy: never call a position dangerous because one leg moved a lot, and treat a "
        "null as 'not quoted', never as zero."
    )
)
async def position_facts() -> dict[str, Any]:
    try:
        return encode(await _svc_facts())
    except Exception as exc:
        return _guard(exc)


@mcp.tool(
    description=(
        "What has actually happened in the app, newest first — call this to catch up rather "
        "than guessing. Records syncs and their failures, positions opening and closing, and "
        "the moment a position crossed something the user cares about: reaching 50% of max "
        "profit, entering the 21-DTE window, passing the 2x stop, trading through a short "
        "strike, a short leg going in the money, earnings landing before expiry. Also "
        "questions asked and answered, and trades linked by hand.\n\n"
        "Each crossing is logged once, so a list of them is a list of things that changed, "
        "not a list of things that are true. severity is info | notable | warning | error; "
        "pass min_severity='warning' for just the things that went wrong or need hands."
    )
)
async def recent_events(limit: int = 100, min_severity: str | None = None) -> dict[str, Any]:
    try:
        service = await _svc()
        return {
            "counts": await service.event_counts(),
            "events": encode(await service.events(limit=limit, min_severity=min_severity)),
        }
    except Exception as exc:
        return _guard(exc)


@mcp.tool(description="Connection status: credentials, session, last sync, and any current error.")
async def health() -> dict[str, Any]:
    try:
        return encode(await (await _svc()).health())
    except Exception as exc:
        return _guard(exc)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
