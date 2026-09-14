"""Tests for the read-only broker access layer.

Nothing here touches the network. Every SDK entry point is replaced with a fake
that records how it was called, because the things worth testing are our own
policies -- pagination, batching, throttling, retry classification -- not the
SDK's HTTP layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import pytest
from tastytrade.utils import TastytradeError

from tastydesk.core import client as client_mod
from tastydesk.core.client import (
    HISTORY_PAGE_SIZE,
    QUOTE_BATCH_SIZE,
    BrokerError,
    ClientHealth,
    TastyClient,
    mask,
)

# --------------------------------------------------------------------- fakes


class FakeClock:
    """A clock that only moves when something sleeps, so tests never wait."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSession:
    """Stands in for tastytrade.Session; the fakes only need identity."""


class FakeSessionManager:
    def __init__(self, session: Any | None = None, error: Exception | None = None) -> None:
        self.session = session or FakeSession()
        self.error = error
        self.gets = 0

    async def get(self) -> Any:
        self.gets += 1
        if self.error is not None:
            raise self.error
        return self.session


@dataclass
class FakeTransaction:
    id: int


@dataclass
class FakeQuote:
    symbol: str
    mark: str = "1.00"


@dataclass
class FakeMetric:
    symbol: str


class FakeAccount:
    """Only the read methods exist here -- by design."""

    def __init__(self, account_number: str = "5WX12345", pages: list[int] | None = None) -> None:
        self.account_number = account_number
        self._pages = pages or []
        self.history_calls: list[dict[str, Any]] = []
        self.position_calls = 0
        self.balance_calls = 0
        self.nlv_calls: list[str] = []
        self._next_id = 0

    async def get_history(self, session: Any, **kwargs: Any) -> list[FakeTransaction]:
        self.history_calls.append(kwargs)
        offset = kwargs["page_offset"]
        size = self._pages[offset] if offset < len(self._pages) else 0
        rows = [FakeTransaction(self._next_id + i) for i in range(size)]
        self._next_id += size
        return rows

    async def get_positions(self, session: Any, include_marks: bool = False) -> list[str]:
        self.position_calls += 1
        return ["position"]

    async def get_balances(self, session: Any) -> str:
        self.balance_calls += 1
        return "balance"

    async def get_net_liquidating_value_history(self, session: Any, time_back: str) -> list[str]:
        self.nlv_calls.append(time_back)
        return ["ohlc"]


def make_client(
    clock: FakeClock | None = None,
    sessions: FakeSessionManager | None = None,
    **kwargs: Any,
) -> TastyClient:
    clock = clock or FakeClock()
    return TastyClient(
        sessions or FakeSessionManager(),  # type: ignore[arg-type]
        clock=clock.time,
        sleeper=clock.sleep,
        **kwargs,
    )


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.tastytrade.com/accounts")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"status {status}", request=request, response=response)


# ----------------------------------------------------------------- pagination


async def test_transactions_assembles_every_page_and_stops_on_a_short_one() -> None:
    account = FakeAccount(pages=[HISTORY_PAGE_SIZE, HISTORY_PAGE_SIZE, 40])
    client = make_client()

    rows = await client.transactions(account, start_date=date(2024, 1, 1))  # type: ignore[arg-type]

    assert len(rows) == HISTORY_PAGE_SIZE * 2 + 40
    assert [c["page_offset"] for c in account.history_calls] == [0, 1, 2]
    assert {r.id for r in rows} == set(range(len(rows)))  # no page repeated or lost
    assert account.history_calls[0]["start_date"] == date(2024, 1, 1)
    assert account.history_calls[0]["per_page"] == HISTORY_PAGE_SIZE


async def test_transactions_stops_after_one_short_page() -> None:
    account = FakeAccount(pages=[3])
    client = make_client()

    rows = await client.transactions(account)  # type: ignore[arg-type]

    assert len(rows) == 3
    assert len(account.history_calls) == 1


async def test_transactions_stops_on_an_empty_first_page() -> None:
    account = FakeAccount(pages=[0])
    client = make_client()

    assert await client.transactions(account) == []  # type: ignore[arg-type]
    assert len(account.history_calls) == 1


# -------------------------------------------------------------------- quotes


async def test_quotes_chunks_150_symbols_into_two_calls_and_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_market_data(session: Any, **kwargs: Any) -> list[FakeQuote]:
        calls.append(kwargs)
        symbols = [s for values in kwargs.values() for s in values]
        return [FakeQuote(s) for s in symbols]

    monkeypatch.setattr(client_mod, "get_market_data_by_type", fake_market_data)
    symbols = [f"SPY  251219C{i:08d}" for i in range(150)]
    client = make_client()

    quotes = await client.quotes(option_symbols=symbols)

    assert len(calls) == 2
    assert len(calls[0]["options"]) == QUOTE_BATCH_SIZE
    assert len(calls[1]["options"]) == 50
    assert len(quotes) == 150
    assert quotes[symbols[0]].symbol == symbols[0]
    assert quotes[symbols[-1]].symbol == symbols[-1]


async def test_quotes_counts_the_batch_across_instrument_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_market_data(session: Any, **kwargs: Any) -> list[FakeQuote]:
        calls.append(kwargs)
        return [FakeQuote(s) for values in kwargs.values() for s in values]

    monkeypatch.setattr(client_mod, "get_market_data_by_type", fake_market_data)
    client = make_client()

    quotes = await client.quotes(
        option_symbols=[f"O{i}" for i in range(99)],
        equity_symbols=["SPY", "QQQ"],
    )

    # 101 symbols must not ride in a single request.
    assert len(calls) == 2
    assert sum(len(v) for v in calls[0].values()) == QUOTE_BATCH_SIZE
    assert len(quotes) == 101


async def test_quotes_dedupes_and_short_circuits_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_market_data(session: Any, **kwargs: Any) -> list[FakeQuote]:
        calls.append(kwargs)
        return [FakeQuote(s) for values in kwargs.values() for s in values]

    monkeypatch.setattr(client_mod, "get_market_data_by_type", fake_market_data)
    client = make_client()

    assert await client.quotes() == {}
    assert calls == []

    await client.quotes(equity_symbols=["SPY", "SPY", "QQQ"])
    assert calls[0]["equities"] == ["SPY", "QQQ"]


async def test_market_metrics_keys_by_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    async def fake_metrics(session: Any, symbols: Any) -> list[FakeMetric]:
        seen.append(list(symbols))
        return [FakeMetric(s) for s in symbols if s != "GONE"]

    monkeypatch.setattr(client_mod, "get_market_metrics", fake_metrics)
    client = make_client()

    metrics = await client.market_metrics(["SPY", "GONE", "IWM"])

    assert set(metrics) == {"SPY", "IWM"}  # an unknown symbol is simply absent
    assert seen == [["SPY", "GONE", "IWM"]]
    assert await client.market_metrics([]) == {}


# -------------------------------------------------------------- rate limiting


async def test_rate_limiter_spaces_calls_two_per_second() -> None:
    clock = FakeClock()
    account = FakeAccount()
    client = make_client(clock)

    for _ in range(3):
        await client.balances(account)  # type: ignore[arg-type]

    assert account.balance_calls == 3
    # First call goes straight through; each later one waits out the interval.
    assert clock.sleeps == [0.5, 0.5]
    assert clock.now == pytest.approx(1.0)


async def test_rate_limiter_honours_a_custom_rate() -> None:
    clock = FakeClock()
    account = FakeAccount()
    client = make_client(clock, rate_per_second=4.0)

    await client.balances(account)  # type: ignore[arg-type]
    await client.balances(account)  # type: ignore[arg-type]

    assert clock.sleeps == [0.25]


async def test_rate_limiter_does_not_wait_when_time_already_passed() -> None:
    clock = FakeClock()
    account = FakeAccount()
    client = make_client(clock)

    await client.balances(account)  # type: ignore[arg-type]
    clock.now += 10.0  # a slow caller: the budget has long since refilled
    await client.balances(account)  # type: ignore[arg-type]

    assert clock.sleeps == []


# --------------------------------------------------------------------- retry


class Flaky:
    """Fails with `error` for the first `failures` calls, then succeeds."""

    def __init__(self, error: Exception, failures: int) -> None:
        self.error = error
        self.failures = failures
        self.calls = 0

    async def get_balances(self, session: Any) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "balance"


async def test_429_is_retried_with_growing_backoff() -> None:
    clock = FakeClock()
    account = Flaky(TastytradeError("429: Too many requests"), failures=2)
    client = make_client(clock)

    assert await client.balances(account) == "balance"  # type: ignore[arg-type]

    assert account.calls == 3
    # Backoff doubles: 0.5s then 1.0s. The retry waits already exceed the
    # limiter's 0.5s spacing, so the throttle adds nothing on top of them.
    assert clock.sleeps == [0.5, 1.0]


async def test_http_429_response_is_retried() -> None:
    account = Flaky(http_error(429), failures=1)
    client = make_client()

    assert await client.balances(account) == "balance"  # type: ignore[arg-type]
    assert account.calls == 2


async def test_500_is_retried_and_gives_up_with_an_actionable_message() -> None:
    account = Flaky(http_error(503), failures=99)
    client = make_client()

    with pytest.raises(BrokerError) as caught:
        await client.balances(account)  # type: ignore[arg-type]

    assert account.calls == client_mod.MAX_ATTEMPTS  # capped, never forever
    assert "503" in str(caught.value)
    assert "nothing is wrong with your setup" in str(caught.value)


async def test_404_is_not_retried() -> None:
    account = Flaky(http_error(404), failures=99)
    client = make_client()

    with pytest.raises(BrokerError):
        await client.balances(account)  # type: ignore[arg-type]

    assert account.calls == 1


async def test_401_is_not_retried_and_names_the_fix() -> None:
    account = Flaky(TastytradeError("401: invalid token"), failures=99)
    client = make_client()

    with pytest.raises(BrokerError) as caught:
        await client.balances(account)  # type: ignore[arg-type]

    assert account.calls == 1
    assert "my.tastytrade.com" in str(caught.value)


async def test_connection_failure_is_retried_then_reported_plainly() -> None:
    account = Flaky(httpx.ConnectError("no route to host"), failures=99)
    client = make_client()

    with pytest.raises(BrokerError) as caught:
        await client.balances(account)  # type: ignore[arg-type]

    assert account.calls == client_mod.MAX_ATTEMPTS
    assert "network" in str(caught.value)


# -------------------------------------------------------------------- health


async def test_health_reports_missing_credentials_with_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod, "credentials_present", lambda: False)
    sessions = FakeSessionManager()
    client = make_client(sessions=sessions)

    health = await client.health()

    assert isinstance(health, ClientHealth)
    assert health.credentials_present is False
    assert health.session_ok is False
    assert health.account_count == 0
    assert health.ok is False
    assert health.last_error is not None
    assert "Keychain" in health.last_error
    assert "scripts/setup-credentials.sh" in health.last_error
    assert sessions.gets == 0  # no point calling the broker with no secrets


async def test_health_reports_a_rejected_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "credentials_present", lambda: True)

    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> list[Any]:
            raise http_error(401)

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    health = await client.health()

    assert health.credentials_present is True
    assert health.session_ok is False
    assert health.last_error is not None
    assert "401" in health.last_error
    assert "my.tastytrade.com" in health.last_error


async def test_health_reports_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "credentials_present", lambda: True)

    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> list[Any]:
            raise http_error(429)

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    health = await client.health()

    assert health.last_error is not None
    assert "rate limited" in health.last_error


async def test_health_reports_a_keychain_failure_on_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod, "credentials_present", lambda: True)
    sessions = FakeSessionManager(error=client_mod.CredentialError("gone"))
    client = make_client(sessions=sessions)

    health = await client.health()

    assert health.session_ok is False
    assert health.last_error is not None
    assert "setup-credentials.sh" in health.last_error


async def test_health_is_green_when_accounts_come_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "credentials_present", lambda: True)

    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> list[Any]:
            return [FakeAccount(), FakeAccount("5WX99999")]

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    health = await client.health()

    assert health.ok is True
    assert health.session_ok is True
    assert health.account_count == 2
    assert health.last_error is None
    assert health.checked_at.tzinfo is not None


async def test_health_flags_a_grant_that_covers_no_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod, "credentials_present", lambda: True)

    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> list[Any]:
            return []

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    health = await client.health()

    assert health.account_count == 0
    assert health.last_error is not None
    assert "no accounts" in health.last_error
    assert health.ok is False


# ------------------------------------------------------------------ accounts


async def test_accounts_are_cached_and_primary_is_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    first = FakeAccount("5WX11111")

    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> list[Any]:
            calls["n"] += 1
            return [first, FakeAccount("5WX22222")]

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    assert await client.primary_account() is first
    await client.accounts()
    assert calls["n"] == 1  # cached: the account list never changes mid-session

    await client.accounts(refresh=True)
    assert calls["n"] == 2


async def test_accounts_wraps_a_single_account_in_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only = FakeAccount()

    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> Any:
            return only

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    assert await client.accounts() == [only]


async def test_primary_account_raises_when_the_grant_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAccountApi:
        @staticmethod
        async def get(session: Any) -> list[Any]:
            return []

    monkeypatch.setattr(client_mod, "Account", FakeAccountApi)
    client = make_client()

    with pytest.raises(BrokerError):
        await client.primary_account()


async def test_positions_ask_for_marks_and_nlv_passes_the_window() -> None:
    account = FakeAccount()
    client = make_client()

    assert await client.positions(account) == ["position"]  # type: ignore[arg-type]
    assert account.position_calls == 1
    assert await client.net_liq_history(account, time_back="3m") == ["ohlc"]  # type: ignore[arg-type]
    assert account.nlv_calls == ["3m"]


# ------------------------------------------------------------- read-only-ness


def test_client_exposes_no_mutating_method() -> None:
    forbidden = ("place", "replace", "delete", "cancel")
    offenders = [
        name
        for name in dir(TastyClient)
        if any(word in name.lower() for word in forbidden) and callable(getattr(TastyClient, name))
    ]
    assert offenders == []


def test_client_source_never_mentions_an_order_entry_point() -> None:
    import inspect

    source = inspect.getsource(client_mod)
    # The docstring names them to say they are banned; no line may call one.
    calls = [
        line
        for line in source.splitlines()
        if any(f"{verb}(" in line for verb in ("place_order", "replace_order", "delete_order"))
    ]
    assert calls == []


# ----------------------------------------------------------------- log safety


def test_mask_hides_all_but_the_last_four() -> None:
    assert mask("5WX12345") == "****2345"
    assert mask("123") == "***"
    assert mask(None) == "<none>"
    assert "5WX1" not in mask("5WX12345")
