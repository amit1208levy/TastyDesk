"""Read-only data access layer for tastytrade.

Every broker call in Tasty Desk goes through :class:`TastyClient`. Nothing else
in the app imports ``tastytrade.Account`` or the market-data helpers directly,
so there is exactly one place where rate limiting, retries, pagination and
batching are implemented, and exactly one place to audit.

*** THIS MODULE NEVER MUTATES ANYTHING AT THE BROKER. ***
It calls no order entry point of the SDK -- no place_order, no
place_complex_order, no replace_order, no delete_order, no delete_complex_order
-- and no method here may ever be added that does. The OAuth grant is expected
to carry only the ``read`` scope; this module is the second lock on that door.
``tests/test_client.py`` asserts by introspection that the public surface
contains no method whose name suggests mutation.

Why the throttle exists
-----------------------
tastytrade answers with 429 and will block a client that keeps hammering it.
A dashboard that refreshes positions, quotes and metrics can easily fire a
dozen calls in a burst, so a single shared minimum-interval limiter sits in
front of *every* request. Two requests per second is comfortably polite and
still repaints a portfolio in well under a second of throttle delay.

Backoff is deliberately jitter-free: with one process and one limiter there is
no thundering herd to spread out, and a predictable delay is far easier to
explain to a user staring at a spinner.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TypeVar

import httpx
from tastytrade import Account, Session
from tastytrade.account import AccountBalance, CurrentPosition, NetLiqOhlc, Transaction
from tastytrade.market_data import MarketData, get_market_data_by_type
from tastytrade.metrics import MarketMetricInfo, get_market_metrics

from tastydesk.core.auth import CredentialError, SessionManager, credentials_present

logger = logging.getLogger(__name__)

__all__ = ["ClientHealth", "TastyClient", "BrokerError"]

T = TypeVar("T")

#: Requests per second across the whole process. See module docstring.
DEFAULT_RATE_PER_SECOND = 2.0
#: Total tries per call, including the first. Four tries spans ~3.5s of backoff.
MAX_ATTEMPTS = 4
BASE_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
#: Documented batch limit of the market-data endpoint.
QUOTE_BATCH_SIZE = 100
#: Transactions per page. The SDK's own default; the API accepts up to 250.
HISTORY_PAGE_SIZE = 250
#: Refuse to loop forever if the API ever stops honouring page-offset.
MAX_HISTORY_PAGES = 400

_LEADING_CODE = re.compile(r"^\s*(\d{3})\b")
_RATE_LIMIT_HINTS = ("rate limit", "too many requests", "rate_limit")


class BrokerError(RuntimeError):
    """A broker call failed after every retry was exhausted."""


def mask(value: str | None) -> str:
    """Render an account number or token safe to log: all but the last 4 hidden."""
    if not value:
        return "<none>"
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _status_code_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status for an SDK exception.

    httpx errors carry a real response. ``TastytradeError`` does not: the SDK
    flattens the JSON error body into a string that usually starts with the
    code, so we read the leading three digits and fall back to sniffing for the
    words the API uses when it throttles.
    """
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    text = str(exc)
    if match := _LEADING_CODE.match(text):
        return int(match.group(1))
    lowered = text.lower()
    if any(hint in lowered for hint in _RATE_LIMIT_HINTS):
        return 429
    return None


def _is_retryable(exc: BaseException) -> bool:
    """Retry only what a second attempt could plausibly fix.

    A 429 clears with time and a 5xx is the broker's problem, not ours. Every
    other 4xx is a bad request, a dead token or a missing resource -- repeating
    it just burns the rate budget and delays the error the user needs to see.
    """
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    code = _status_code_of(exc)
    if code is None:
        return False
    return code == 429 or 500 <= code < 600


def _retry_delay(attempt: int) -> float:
    """Exponential, capped, no jitter. ``attempt`` is 1 for the first failure."""
    return min(BASE_RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _dedupe(symbols: Iterable[str] | None) -> list[str]:
    """Preserve order, drop repeats and blanks -- a repeat wastes batch space."""
    seen: dict[str, None] = {}
    for symbol in symbols or ():
        if symbol:
            seen.setdefault(symbol, None)
    return list(seen)


class _RateLimiter:
    """Minimum-interval throttle shared by every call on one client.

    A token bucket would allow a burst, which is exactly the shape of traffic
    that gets a client blocked. A flat minimum gap between request *starts* is
    simpler and strictly politer. The clock and sleep are injectable so tests
    can prove the spacing without actually waiting.
    """

    def __init__(
        self,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._min_interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._next_allowed: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        # The lock serialises the reservation itself, so concurrent callers
        # queue up one interval apart instead of all reading the same "now".
        async with self._lock:
            now = self._clock()
            if self._next_allowed is not None and self._next_allowed > now:
                await self._sleeper(self._next_allowed - now)
                now = max(self._clock(), self._next_allowed)
            self._next_allowed = now + self._min_interval


@dataclass(frozen=True, slots=True)
class ClientHealth:
    """Why the broker is or is not reachable, in words the user can act on.

    Both the dashboard and the MCP server show ``last_error`` verbatim when a
    fetch fails, so it names the specific broken thing and the specific fix.
    """

    credentials_present: bool
    session_ok: bool
    account_count: int
    last_error: str | None
    checked_at: datetime

    @property
    def ok(self) -> bool:
        return self.credentials_present and self.session_ok and self.last_error is None


_NO_CREDENTIALS = (
    "no credentials in the macOS Keychain - run scripts/setup-credentials.sh "
    "to store your client secret and refresh token"
)
_NO_ACCOUNTS = (
    "signed in, but the grant returned no accounts - check at my.tastytrade.com "
    "that the OAuth grant covers your trading account"
)


def _diagnose(exc: BaseException, *, during: str) -> str:
    """Turn an SDK exception into a sentence that names the fix."""
    if isinstance(exc, CredentialError):
        return _NO_CREDENTIALS
    if isinstance(exc, httpx.TimeoutException):
        return "tastytrade did not answer in time - the API or your network is slow; try again"
    if isinstance(exc, httpx.TransportError):
        return "cannot reach api.tastytrade.com - check your network connection"

    code = _status_code_of(exc)
    if code == 401:
        return (
            "refresh token rejected (401) - create a new grant at my.tastytrade.com "
            "(Manage -> My Profile -> API) and re-run scripts/setup-credentials.sh"
        )
    if code == 403:
        return (
            "access refused (403) - the OAuth grant is missing the 'read' scope, "
            "or this account is not covered by it"
        )
    if code == 404:
        return f"tastytrade has no such resource (404) while fetching {during}"
    if code == 429:
        return "rate limited by tastytrade (429) - too many requests; wait a minute and try again"
    if code is not None and 500 <= code < 600:
        return (
            f"tastytrade server error ({code}) - the broker's API is having trouble; "
            "nothing is wrong with your setup"
        )

    detail = str(exc).strip() or exc.__class__.__name__
    if during in ("the session", "accounts"):
        # Authentication failures often arrive as an unparseable error body, so
        # point at the most likely culprit rather than echoing SDK noise alone.
        return (
            f"could not sign in to tastytrade while fetching {during}: {detail} "
            "- if this persists, create a new grant at my.tastytrade.com"
        )
    return f"tastytrade call for {during} failed: {detail}"


class TastyClient:
    """Async, read-only, rate-limited access to one tastytrade login."""

    def __init__(
        self,
        sessions: SessionManager,
        *,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sessions = sessions
        self._limiter = _RateLimiter(rate_per_second, clock=clock, sleeper=sleeper)
        self._max_attempts = max(1, max_attempts)
        self._sleeper = sleeper
        self._accounts: list[Account] | None = None
        self._announced = False

    # ---------------------------------------------------------------- plumbing

    async def _guard(self, label: str, call: Callable[[], Awaitable[T]]) -> T:
        """Throttle, run, and retry the transient failures. Never loops forever."""
        last: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._limiter.acquire()
            try:
                return await call()
            except Exception as exc:
                last = exc
                if not _is_retryable(exc) or attempt == self._max_attempts:
                    break
                delay = _retry_delay(attempt)
                logger.warning(
                    "tastytrade %s failed (%s); retrying in %.1fs (attempt %d of %d)",
                    label,
                    _status_code_of(exc) or exc.__class__.__name__,
                    delay,
                    attempt + 1,
                    self._max_attempts,
                )
                await self._sleeper(delay)
        assert last is not None
        raise BrokerError(_diagnose(last, during=label)) from last

    async def _session(self) -> Session:
        return await self._sessions.get()

    # ----------------------------------------------------------------- accounts

    async def accounts(self, *, refresh: bool = False) -> list[Account]:
        """Every open account on the login. Cached: this list changes ~never."""
        if self._accounts is not None and not refresh:
            return self._accounts

        session = await self._session()
        found = await self._guard("accounts", lambda: Account.get(session))
        accounts = list(found) if isinstance(found, list) else [found]
        self._accounts = accounts
        if not self._announced:
            logger.info(
                "Connected to tastytrade (read-only): %d account(s) %s",
                len(accounts),
                [mask(a.account_number) for a in accounts],
            )
            self._announced = True
        return accounts

    async def primary_account(self) -> Account:
        """The account the dashboard shows. First is right for a single-account user."""
        accounts = await self.accounts()
        if not accounts:
            raise BrokerError(_NO_ACCOUNTS)
        return accounts[0]

    # ---------------------------------------------------------------- portfolio

    async def positions(self, account: Account) -> list[CurrentPosition]:
        """Open positions with marks, so the caller can price a strategy at once."""
        session = await self._session()
        return await self._guard(
            "positions",
            lambda: account.get_positions(session, include_marks=True),
        )

    async def balances(self, account: Account) -> AccountBalance:
        session = await self._session()
        return await self._guard("balances", lambda: account.get_balances(session))

    async def transactions(
        self,
        account: Account,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]:
        """Full transaction history for the window, following every page.

        A premium seller racks up thousands of transactions a year and the API
        hands them back a page at a time. We ask for an explicit page offset
        rather than trusting a server-side cursor, and stop when a page comes
        back shorter than we asked for -- that is the last page.
        """
        session = await self._session()
        out: list[Transaction] = []
        for page in range(MAX_HISTORY_PAGES):
            offset = page

            async def fetch(offset: int = offset) -> list[Transaction]:
                return await account.get_history(
                    session,
                    start_date=start_date,
                    end_date=end_date,
                    per_page=HISTORY_PAGE_SIZE,
                    page_offset=offset,
                )

            got = await self._guard("transaction history", fetch)
            out.extend(got)
            if len(got) < HISTORY_PAGE_SIZE:
                return out
        logger.warning(
            "Stopped paging transaction history at %d pages (%d rows) - the API kept returning full pages",
            MAX_HISTORY_PAGES,
            len(out),
        )
        return out

    async def net_liq_history(self, account: Account, time_back: str = "1y") -> list[NetLiqOhlc]:
        session = await self._session()
        return await self._guard(
            "net liq history",
            lambda: account.get_net_liquidating_value_history(session, time_back=time_back),
        )

    # -------------------------------------------------------------- market data

    async def market_metrics(self, symbols: Sequence[str]) -> dict[str, MarketMetricInfo]:
        """IV rank, liquidity, earnings and dividend dates, keyed by underlying.

        Missing symbols are simply absent from the result: an underlying the
        API has no metrics for must not take the whole refresh down with it.
        """
        wanted = _dedupe(symbols)
        if not wanted:
            return {}
        session = await self._session()
        out: dict[str, MarketMetricInfo] = {}
        for batch in _chunk(wanted, QUOTE_BATCH_SIZE):

            async def fetch(batch: Sequence[str] = batch) -> list[MarketMetricInfo]:
                return await get_market_metrics(session, batch)

            for metric in await self._guard("market metrics", fetch):
                out[metric.symbol] = metric
        return out

    async def quotes(
        self,
        option_symbols: Sequence[str] | None = None,
        equity_symbols: Sequence[str] | None = None,
        index_symbols: Sequence[str] | None = None,
        future_symbols: Sequence[str] | None = None,
        future_option_symbols: Sequence[str] | None = None,
    ) -> dict[str, MarketData]:
        """Marks for any mix of instruments, keyed by symbol.

        The endpoint accepts at most 100 symbols per call, counted across all
        instrument types, so chunking happens on the combined list: an iron
        condor portfolio is mostly options with a handful of underlyings and
        would otherwise waste a whole request on the underlyings.
        """
        buckets: list[tuple[str, list[str]]] = [
            ("options", _dedupe(option_symbols)),
            ("equities", _dedupe(equity_symbols)),
            ("indices", _dedupe(index_symbols)),
            ("futures", _dedupe(future_symbols)),
            ("future_options", _dedupe(future_option_symbols)),
        ]
        flat = [(kind, symbol) for kind, symbols in buckets for symbol in symbols]
        if not flat:
            return {}

        session = await self._session()
        out: dict[str, MarketData] = {}
        for batch in _chunk(flat, QUOTE_BATCH_SIZE):
            kwargs: dict[str, list[str]] = {}
            for kind, symbol in batch:
                kwargs.setdefault(kind, []).append(symbol)

            async def fetch(kwargs: dict[str, list[str]] = kwargs) -> list[MarketData]:
                return await get_market_data_by_type(session, **kwargs)

            for quote in await self._guard("quotes", fetch):
                out[quote.symbol] = quote
        return out

    # ------------------------------------------------------------------- health

    async def health(self) -> ClientHealth:
        """Diagnose the connection. Never raises -- this is the fallback path."""
        checked_at = datetime.now(UTC)

        try:
            have_creds = credentials_present()
        except Exception:  # pragma: no cover - credentials_present swallows its own
            have_creds = False
        if not have_creds:
            return ClientHealth(
                credentials_present=False,
                session_ok=False,
                account_count=0,
                last_error=_NO_CREDENTIALS,
                checked_at=checked_at,
            )

        try:
            await self._session()
        except Exception as exc:
            logger.warning("tastytrade session could not be opened: %s", exc.__class__.__name__)
            return ClientHealth(
                credentials_present=True,
                session_ok=False,
                account_count=0,
                last_error=_diagnose(exc, during="the session"),
                checked_at=checked_at,
            )

        # Listing accounts is the cheapest call that actually exercises the
        # token, so a green health check means real data will load too.
        try:
            accounts = await self.accounts(refresh=True)
        except BrokerError as exc:
            return ClientHealth(
                credentials_present=True,
                session_ok=False,
                account_count=0,
                last_error=str(exc),
                checked_at=checked_at,
            )
        except Exception as exc:
            return ClientHealth(
                credentials_present=True,
                session_ok=False,
                account_count=0,
                last_error=_diagnose(exc, during="accounts"),
                checked_at=checked_at,
            )

        return ClientHealth(
            credentials_present=True,
            session_ok=True,
            account_count=len(accounts),
            last_error=None if accounts else _NO_ACCOUNTS,
            checked_at=checked_at,
        )
