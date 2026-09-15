"""The MCP server, exercised the way Claude actually talks to it.

Calling ``mcp.call_tool()`` in-process looks like a test but is not one: it
bypasses the session the server expects and blocks. The only meaningful check
is a real stdio client, which is what this does.

No credentials are needed. The point of these tests is the failure path — that
a missing credential produces something a person can act on rather than a
traceback, and that an unreachable broker never fabricates a number.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = {
    "portfolio_summary",
    "list_open_strategies",
    "list_closed_strategies",
    "strategy_detail",
    "performance",
    "rule_adherence",
    "sync",
    "health",
}


def _params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "tastydesk.mcp.server"],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        cwd=str(PROJECT_ROOT),
    )


async def _call(name: str, args: dict | None = None) -> dict:
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=60)
            result = await asyncio.wait_for(session.call_tool(name, args or {}), timeout=90)
            return json.loads(result.content[0].text)


@pytest.mark.slow
async def test_every_tool_is_registered() -> None:
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=60)
            tools = await session.list_tools()

    names = {t.name for t in tools.tools}
    assert EXPECTED_TOOLS <= names

    # The app is read only. A tool that could mutate the account would be a
    # bug in the wiring, not merely a policy problem.
    forbidden = ("place", "order", "cancel", "replace", "close_position", "trade")
    assert not [n for n in names if any(word in n for word in forbidden)]


@pytest.mark.slow
async def test_health_names_the_actual_problem() -> None:
    """Without credentials, health must say which thing is missing."""
    body = await _call("health")

    assert body["credentials_present"] is False
    assert body["session_ok"] is False
    # Vagueness here costs the user a debugging session, so assert the message
    # points at the fix rather than merely reporting failure.
    assert "setup-credentials" in (body["last_error"] or "")


@pytest.mark.slow
async def test_unreachable_broker_returns_an_error_not_a_number() -> None:
    """A summary that cannot be computed must not come back as zeros."""
    body = await _call("portfolio_summary")

    assert body.get("error") == "credentials_missing"
    assert "net_liquidating_value" not in body


@pytest.mark.slow
async def test_empty_account_is_empty_not_broken() -> None:
    body = await _call("list_open_strategies")

    assert body["count"] == 0
    assert body["strategies"] == []
