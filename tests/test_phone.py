"""The phone listener answers only a paired phone; the dashboard only this Mac."""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tastydesk.api import phone
from tastydesk.api.phone import PHONE_ORIGIN, PhoneGate, _digest

TOKEN = "correct-horse-battery-staple"
PHONE = 8788


async def _ok(_request):
    return JSONResponse({"ok": True})


inner = Starlette(routes=[Route("/api/health", _ok), Route("/", _ok)])


def _client(app, port: int, host: str = "127.0.0.1") -> httpx.AsyncClient:
    # ASGITransport puts the URL's host and port in scope["server"], which is
    # exactly what uvicorn fills from the socket a connection arrived on.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}:{port}")


@pytest.fixture
def gated():
    return PhoneGate(inner, PHONE, _digest(TOKEN))


async def test_dashboard_listener_needs_no_token(gated):
    async with _client(gated, 8787) as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/")).status_code == 200


async def test_dashboard_listener_refuses_a_foreign_host_name(gated):
    async with _client(gated, 8787) as c:
        r = await c.get("/api/health", headers={"Host": "my-mac.tail1234.ts.net"})
    assert r.status_code == 403


async def test_dashboard_listener_accepts_localhost_names(gated):
    async with _client(gated, 8787) as c:
        for host in ("localhost:8787", "LOCALHOST", "[::1]:8787", "127.0.0.1"):
            assert (await c.get("/api/health", headers={"Host": host})).status_code == 200


async def test_phone_listener_requires_the_token(gated):
    async with _client(gated, PHONE) as c:
        assert (await c.get("/api/health")).status_code == 401
        wrong = await c.get("/api/health", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert wrong.json() == {"detail": "Unauthorized"}
        right = await c.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})
        assert right.status_code == 200


async def test_phone_listener_serves_only_the_api(gated):
    async with _client(gated, PHONE) as c:
        r = await c.get("/", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404


async def test_phone_listener_allows_only_the_app_origin(gated):
    async with _client(gated, PHONE) as c:
        pre = await c.options(
            "/api/health",
            headers={
                "Origin": PHONE_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == PHONE_ORIGIN

        other = await c.options(
            "/api/health",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in other.headers

        r = await c.get(
            "/api/health", headers={"Origin": PHONE_ORIGIN, "Authorization": f"Bearer {TOKEN}"}
        )
        assert r.headers["access-control-allow-origin"] == PHONE_ORIGIN


async def test_unpaired_phone_listener_refuses_everything():
    gate = PhoneGate(inner, PHONE, None)
    async with _client(gate, PHONE) as c:
        r = await c.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 401


def test_pairing_keeps_only_a_hash(monkeypatch):
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(phone.keyring, "set_password", lambda s, a, v: stored.__setitem__((s, a), v))
    monkeypatch.setattr(phone.keyring, "get_password", lambda s, a: stored.get((s, a)))

    token = phone.new_pairing()
    assert len(token) >= 40
    assert token not in stored.values()
    assert phone.paired_digest() == _digest(token)
    assert phone.new_pairing() != token
