"""The local HTTP server.

Binds to 127.0.0.1 only. This process holds a read-only credential for a real
brokerage account, so it is not something to expose on a network interface by
default; remote access, if it is ever wanted, belongs behind a device-
authenticated tunnel rather than an open port.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tastydesk.api.serialize import encode, encode_view
from tastydesk.core.auth import CredentialError, SessionManager
from tastydesk.core.client import TastyClient
from tastydesk.core.db import Database
from tastydesk.service import DeskService

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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Sync failed")
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}") from exc
    return encode(result)


@app.post("/api/snapshot")
async def snapshot() -> dict:
    return {"snapshots_written": await svc().snapshot()}


@app.get("/api/portfolio/summary")
async def summary() -> dict:
    return encode(await svc().summary())


@app.get("/api/strategies/open")
async def open_strategies() -> list[dict]:
    return [encode_view(v) for v in await svc().open_views()]


@app.get("/api/strategies/closed")
async def closed_strategies(limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    return [encode_view(v) for v in await svc().closed_views(limit)]


@app.get("/api/performance")
async def performance() -> dict:
    return encode(svc().performance())


@app.get("/api/performance/by-strategy")
async def performance_by_strategy() -> dict:
    return encode(svc().performance_by_strategy())


@app.get("/api/performance/by-underlying")
async def performance_by_underlying() -> dict:
    return encode(svc().performance_by_underlying())


@app.get("/api/performance/by-bucket")
async def performance_by_bucket(dimension: str = Query(...)) -> dict:
    allowed = {"dte_at_entry", "iv_rank_at_entry", "short_delta_at_entry"}
    if dimension not in allowed:
        raise HTTPException(status_code=400, detail=f"dimension must be one of {sorted(allowed)}")
    return encode(svc().performance_by_bucket(dimension))


@app.get("/api/performance/rules")
async def rules() -> dict:
    return encode(await svc().rules())


# The built single-page app, served from the same origin so the browser needs no
# CORS exception and the whole thing is one process to start and stop.
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unknown API route")
        return FileResponse(WEB_DIST / "index.html")
