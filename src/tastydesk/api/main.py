"""The local HTTP server.

Binds to 127.0.0.1 only. This process holds a read-only credential for a real
brokerage account, so it is not something to expose on a network interface by
default; remote access, if it is ever wanted, belongs behind a device-
authenticated tunnel rather than an open port.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tastydesk.api.serialize import encode, encode_view
from tastydesk.core import indicators
from tastydesk.core.auth import CredentialError, SessionManager
from tastydesk.core.briefs import BriefStore
from tastydesk.core.client import TastyClient
from tastydesk.core.db import Database
from tastydesk.service import NEW_ERA, DeskService, SyncError

logger = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def build_service() -> DeskService:
    sessions = SessionManager()
    client = TastyClient(sessions)
    return DeskService(Database(), client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = build_service()
    await service.start()
    app.state.service = service
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title="Tasty Desk", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def _era(request: Request, call_next):
    """A page in New Levy says so on every request; reports then read the new life."""
    token = NEW_ERA.set(request.headers.get("x-era") == "new")
    try:
        return await call_next(request)
    finally:
        NEW_ERA.reset(token)


def svc() -> DeskService:
    return app.state.service


@app.exception_handler(CredentialError)
async def _credentials_missing(_request, exc: CredentialError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/api/health")
async def health() -> dict:
    return encode(await svc().health())


@app.post("/api/sync")
async def sync(full: bool = Query(False)) -> dict:
    try:
        result = await svc().sync(full=full)
    except CredentialError as exc:
        # Worth logging too: "I pressed sync and nothing happened" is a real
        # report, and the answer is in here rather than in a 503 nobody saw.
        await _record_failure(
            "sync.blocked",
            "Sync was attempted with no credentials in the Keychain.",
            severity="warning",
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        # Log it where it can be read later, not only to whatever terminal
        # happened to be attached when it happened.
        logger.exception("Sync failed")
        await _record_failure("sync.failed", f"Sync failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}") from exc
    return encode(result)


async def _record_failure(kind: str, summary: str, severity: str = "error") -> None:
    try:
        await svc()._db.record(kind, summary, severity=severity)  # noqa: SLF001
    except Exception:  # pragma: no cover - never let logging mask the real error
        logger.debug("Could not record %s", kind, exc_info=True)


@app.post("/api/snapshot")
async def snapshot() -> dict:
    return {"snapshots_written": await svc().snapshot()}


@app.get("/api/portfolio/summary")
async def summary() -> dict:
    return encode(await svc().summary())


@app.get("/api/portfolio/greeks")
async def portfolio_greeks() -> dict:
    """Directional exposure in units that add up across a mixed book."""
    totals = await svc().portfolio_greeks()
    out = encode(totals)
    out["dollars_per_spy_percent"] = encode(totals.dollars_per_spy_percent)
    out["fully_measured"] = totals.fully_measured
    out["dominant"] = encode(totals.dominant)
    return out


@app.get("/api/strategies/open")
async def open_strategies() -> list[dict]:
    return [encode_view(v) for v in await svc().open_views()]


@app.get("/api/strategies/closed")
async def closed_strategies(limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    return [encode_view(v) for v in await svc().closed_views(limit)]


@app.get("/api/scenario")
async def scenario(
    price: float = Query(0.0, ge=-0.9, le=0.9),
    iv: float = Query(0.0, ge=-0.9, le=3.0),
    days: int = Query(0, ge=0, le=400),
    mode: str = Query("beta", pattern="^(beta|flat)$"),
    strategy: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """The open book priced under one set of conditions.

    ``price`` and ``iv`` are fractions rather than points: 0.02 is two percent
    up, and an implied volatility shift is relative to whatever each leg is
    already trading at.
    """
    return encode(
        await svc().scenario(
            Decimal(str(price)),
            Decimal(str(iv)),
            days,
            by_beta=mode == "beta",
            strategy_ids=strategy,
        )
    )


@app.get("/api/scenario/curve")
async def scenario_curve(
    iv: float = Query(0.0, ge=-0.9, le=3.0),
    days: int = Query(0, ge=0, le=400),
    mode: str = Query("beta", pattern="^(beta|flat)$"),
    strategy: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """P&L across price moves of -20% to +20%, today and under the scenario."""
    return encode(
        await svc().scenario_curve(
            Decimal(str(iv)), days, by_beta=mode == "beta", strategy_ids=strategy
        )
    )


@app.get("/api/strategies/payoff")
async def payoff(id: str = Query(...)) -> dict:
    """The expiration diagram for one row on the Positions tab.

    The id travels as a query parameter, not in the path. A futures trade's id
    contains its underlying — "5WZ55394:/ZSF7:501030373" — and the slash does
    not survive path routing however it is encoded, so every futures position's
    payoff chart was quietly 404ing while the equity ones worked.
    """
    curve = await svc().payoff_curve(id)
    if curve is None:
        raise HTTPException(status_code=404, detail=f"No strategy with id {id}")
    return encode(curve)


@app.get("/api/strategies/{strategy_id}/payoff")
async def payoff_by_path(strategy_id: str) -> dict:
    """The old shape, kept for anything still calling it."""
    curve = await svc().payoff_curve(strategy_id)
    if curve is None:
        raise HTTPException(status_code=404, detail=f"No strategy with id {strategy_id}")
    return encode(curve)


@app.get("/api/legs/open")
async def open_legs() -> list[dict]:
    """Every open leg on its own line. Nothing is grouped here by the app."""
    return encode(svc().open_legs())


@app.get("/api/strategies/named")
async def named_strategies() -> list[dict]:
    # This page now leads with what each strategy is carrying right now, so it
    # needs the same live marks the positions table does.
    with suppress(Exception):
        await svc().ensure_fresh()
    return encode(svc().named_strategies())


@app.post("/api/strategies/named")
async def create_named(payload: dict) -> dict:
    try:
        return encode(
            await svc().create_named_strategy(
                str(payload.get("name", "")),
                list(payload.get("trade_ids") or []),
                payload.get("note"),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/strategies/named/matches")
async def named_matches_all() -> dict:
    """Every named strategy's candidates, so the confidence slider is instant."""
    return encode(svc().named_matches_all())


@app.get("/api/strategies/named/{strategy_id}/matches")
async def named_matches(strategy_id: str) -> dict:
    try:
        return encode(svc().named_matches(strategy_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No strategy {strategy_id}") from exc


@app.post("/api/strategies/named/{strategy_id}/adopt")
async def adopt_matches(strategy_id: str, payload: dict) -> dict:
    try:
        return encode(await svc().adopt_matches(strategy_id, list(payload.get("trade_ids") or [])))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No strategy {strategy_id}") from exc


@app.post("/api/strategies/named/{strategy_id}/plan")
async def save_plan(strategy_id: str, payload: dict) -> dict:
    """Save a new version of this strategy's plan; earlier versions are kept."""
    try:
        return encode(await svc().save_plan(strategy_id, payload))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No strategy {strategy_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/strategies/named/{strategy_id}/drop")
async def drop_member_post(strategy_id: str, payload: dict) -> dict:
    """Remove one trade from a named strategy.

    A POST with the id in the body, not a DELETE with it in the path: trade ids
    carry the underlying, and a futures underlying such as /CLZ6 contains a
    slash that no amount of percent-encoding survives routing intact.
    """
    trade_id = str(payload.get("trade_id") or "")
    if not trade_id:
        raise HTTPException(status_code=400, detail="trade_id is required")
    try:
        return encode(await svc().drop_member(strategy_id, trade_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No strategy {strategy_id}") from exc


@app.delete("/api/strategies/named/{strategy_id}/members/{trade_id}")
async def drop_member(strategy_id: str, trade_id: str) -> dict:
    try:
        return encode(await svc().drop_member(strategy_id, trade_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No strategy {strategy_id}") from exc


@app.delete("/api/strategies/named/{strategy_id}")
async def delete_named(strategy_id: str) -> dict:
    return {"deleted": await svc().delete_named_strategy(strategy_id)}


@app.get("/api/pairing/candidates")
async def pairing_candidates() -> list[dict]:
    return encode(await svc().pairing_candidates())


@app.get("/api/pairing/decisions")
async def pairing_decisions() -> list[dict]:
    return encode(await svc().pairing_decisions())


@app.post("/api/pairing/undo")
async def pairing_undo(payload: dict) -> dict:
    pattern = str(payload.get("pattern", "")).strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    return {"pattern": pattern, "strategies": await svc().undo_pairing(pattern)}


@app.post("/api/pairing/decide")
async def pairing_decide(payload: dict) -> dict:
    pattern = str(payload.get("pattern", "")).strip()
    decision = str(payload.get("decision", "")).strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    try:
        strategies = await svc().decide_pairing(pattern, decision, payload.get("note"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"pattern": pattern, "decision": decision, "strategies": strategies}


@app.get("/api/needs-review")
async def needs_review() -> list[dict]:
    return encode(svc().needs_review())


@app.get("/api/events")
async def events(
    limit: int = Query(100, ge=1, le=1000),
    min_severity: str | None = Query(None, pattern="^(info|notable|warning|error)$"),
) -> list[dict]:
    return encode(await svc().events(limit=limit, min_severity=min_severity))


@app.get("/api/ask")
async def question_thread(limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    return encode(await svc().question_thread(limit))


@app.post("/api/ask")
async def ask(payload: dict) -> dict:
    try:
        return encode(await svc().ask(str(payload.get("question", ""))))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/ask/{question_id}")
async def forget_question(question_id: int) -> dict:
    return {"removed": await svc().forget_question(question_id)}


@app.get("/api/brief")
async def brief() -> dict:
    """The most recent daily brief, or null when none has been written yet."""
    latest = BriefStore().latest()
    if latest is None:
        return {"available": False, "brief": None}
    return {
        "available": True,
        "brief": {
            "on": latest.on.isoformat(),
            "markdown": latest.markdown,
            "written_at": latest.written_at.isoformat(),
            "is_stale": latest.is_stale,
        },
    }


@app.get("/api/grouping/roll-candidates")
async def roll_candidates() -> list[dict]:
    return encode(svc().roll_candidates())


@app.post("/api/grouping/link")
async def link(payload: dict) -> dict:
    ids = payload.get("strategy_ids") or []
    try:
        linked = await svc().link_strategies(list(ids))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"linked": linked}


@app.post("/api/grouping/roll-decision")
async def roll_decision(payload: dict) -> dict:
    """Remember what the user said about a proposed roll, so it is not re-asked."""
    if payload.get("all") == "separate":
        return {"separated": await svc().separate_all_rolls()}
    closed_id = str(payload.get("closed_id") or "")
    opened_id = str(payload.get("opened_id") or "")
    if not closed_id or not opened_id:
        raise HTTPException(status_code=400, detail="closed_id and opened_id are required")
    try:
        await svc().decide_roll(closed_id, opened_id, str(payload.get("decision") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"decided": [closed_id, opened_id]}


@app.post("/api/grouping/unlink")
async def unlink(payload: dict) -> dict:
    strategy_id = payload.get("strategy_id")
    if not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id is required")
    await svc().unlink_strategy(str(strategy_id))
    return {"unlinked": strategy_id}


def _period(start: str | None, end: str | None) -> tuple[date | None, date | None]:
    """Parse the from/to query pair. Both inclusive, both optional."""

    def one(value: str | None, label: str) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"{label} must be a date like 2026-01-31"
            ) from exc

    first, last = one(start, "from"), one(end, "to")
    if first and last and first > last:
        raise HTTPException(status_code=400, detail="'from' is after 'to'")
    return first, last


@app.get("/api/performance/periods")
async def performance_periods() -> dict:
    """The years and months that actually contain closed trades."""
    return encode(svc().periods())


@app.get("/api/performance")
async def performance(start: str | None = Query(None, alias="from"),
                      end: str | None = Query(None, alias="to")) -> dict:
    return encode(svc().performance(*_period(start, end)))


@app.get("/api/performance/pace")
async def performance_pace(start: str | None = Query(None, alias="from"),
                           end: str | None = Query(None, alias="to")) -> dict:
    """Trades a month over the period, and the month and year that implies."""
    return encode(svc().pace(*_period(start, end)))


@app.get("/api/performance/by-strategy")
async def performance_by_strategy(start: str | None = Query(None, alias="from"),
                                  end: str | None = Query(None, alias="to")) -> dict:
    return encode(svc().performance_by_strategy(*_period(start, end)))


@app.get("/api/fields")
async def fields() -> dict:
    """Every indicator the app can show, for the column picker to offer."""
    from dataclasses import asdict

    return {
        "strategy": [asdict(f) for f in indicators.STRATEGY_FIELDS],
        "leg": [asdict(f) for f in indicators.LEG_FIELDS],
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    return encode(await svc().get_settings())


@app.post("/api/settings")
async def set_settings(payload: dict) -> dict:
    """Change one preference. The key has to be one the service knows."""
    key = str(payload.get("key") or "")
    value = payload.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="value is required")
    try:
        return encode(await svc().set_setting(key, str(value)))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"No setting called {key}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/performance/by-named")
async def performance_by_named(start: str | None = Query(None, alias="from"),
                               end: str | None = Query(None, alias="to")) -> dict:
    """Performance grouped by the strategies the user defined and named."""
    first, last = _period(start, end)
    return encode(await svc().performance_by_named(first, last))


@app.get("/api/performance/by-underlying")
async def performance_by_underlying(start: str | None = Query(None, alias="from"),
                                    end: str | None = Query(None, alias="to")) -> dict:
    return encode(svc().performance_by_underlying(*_period(start, end)))


@app.get("/api/performance/by-bucket")
async def performance_by_bucket(dimension: str = Query(...),
                                start: str | None = Query(None, alias="from"),
                                end: str | None = Query(None, alias="to")) -> dict:
    allowed = {"dte_at_entry", "iv_rank_at_entry", "short_delta_at_entry"}
    if dimension not in allowed:
        raise HTTPException(status_code=400, detail=f"dimension must be one of {sorted(allowed)}")
    first, last = _period(start, end)
    return encode(svc().performance_by_bucket(dimension, first, last))


@app.get("/api/performance/loss-shape")
async def loss_shape(start: str | None = Query(None, alias="from"),
                     end: str | None = Query(None, alias="to")) -> dict:
    return encode(svc().loss_shape(*_period(start, end)))


@app.get("/api/performance/rules")
async def rules(start: str | None = Query(None, alias="from"),
                end: str | None = Query(None, alias="to")) -> dict:
    return encode(await svc().rules(*_period(start, end)))


@app.get("/api/performance/strategies")
async def performance_strategies(
    grouping: str = Query("named", pattern="^(named|structure|product)$"),
    start: str | None = Query(None, alias="from"),
    end: str | None = Query(None, alias="to"),
) -> list[dict]:
    """Each strategy's record in depth, for the Performance tab's deep dive."""
    return encode(await svc().strategy_reports(grouping, *_period(start, end)))


@app.get("/api/scanner")
async def scan(refresh: bool = Query(False)) -> dict:
    """Tom's scanner: his plan's entry rules over the market list, sized to this account."""
    try:
        return encode(await svc().scan(refresh=refresh))
    except SyncError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/tom")
async def tom() -> dict:
    """The book against Tom King's 2026 trading plan: today, the chart, the history."""
    return encode(await svc().tom_analysis())


# The built single-page app, served from the same origin so the browser needs no
# CORS exception and the whole thing is one process to start and stop.
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unknown API route")
        # index.html names the hashed bundle, so a cached copy pins the browser
        # to whichever build it first saw: the app keeps running old code after
        # an update, silently, and the only symptom is a feature that appears to
        # have vanished. The assets themselves are content-hashed and may cache
        # forever; this one file must not.
        return FileResponse(
            WEB_DIST / "index.html",
            # no-store rather than no-cache: no-cache still permits storing a
            # copy to revalidate, and some browsers serve that copy anyway.
            # This file is 450 bytes and names the hashed bundle, so a stale
            # one pins the whole app to an old build - the symptom being a
            # feature that silently does not exist.
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )
