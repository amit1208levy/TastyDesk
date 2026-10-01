"""Daily price history, for the questions only a chart can answer.

Tom King reads the market regime off a daily chart — where SPY sits against its
21-day EMA, whether the 8 is above the 21, which side of price the parabolic
SAR is on, and whether RSI is above 50 — and the same four tests, run on a
product's own chart, decide whether he sells puts on it at all. None of that is
in a quote. It needs months of daily bars.

tastytrade's market-data stream would be the natural source, but this login is
not entitled to it (``quote_streamer.api_access_entitlement_not_granted``), and
nothing on the REST side carries history. So daily bars come from Yahoo
Finance's public chart endpoint. What leaves the machine is a ticker symbol —
"SPY", "ZB=F" — and nothing else: never an account number, a position, a size
or a credential.

Futures are read off Yahoo's continuous front-month series. That is the right
chart for a trend question about the product, and it is labelled as such rather
than passed off as the exact contract month held.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from tastydesk.core.occ import product_root

logger = logging.getLogger(__name__)

__all__ = ["Bar", "PriceHistory", "yahoo_symbol"]

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# An honest name. Yahoo answers it; it is also who is asking.
USER_AGENT = "TastyDesk/0.1 (local, read-only)"

# A trend question does not need second-by-second prices. Half an hour keeps a
# page that is open all day to a couple of dozen requests.
CACHE_SECONDS = 30 * 60
CONCURRENCY = 4
TIMEOUT_SECONDS = 12.0

# Product root -> Yahoo's continuous front-month symbol.
_FUTURES: dict[str, str] = {
    "/ES": "ES=F",
    "/MES": "MES=F",
    "/NQ": "NQ=F",
    "/MNQ": "MNQ=F",
    "/RTY": "RTY=F",
    "/M2K": "M2K=F",
    "/YM": "YM=F",
    "/MYM": "MYM=F",
    "/ZB": "ZB=F",
    "/UB": "UB=F",
    "/ZN": "ZN=F",
    "/ZF": "ZF=F",
    "/ZT": "ZT=F",
    "/CL": "CL=F",
    "/MCL": "MCL=F",
    "/QM": "QM=F",
    "/NG": "NG=F",
    "/RB": "RB=F",
    "/HO": "HO=F",
    "/GC": "GC=F",
    "/MGC": "MGC=F",
    "/SI": "SI=F",
    "/SIL": "SIL=F",
    "/HG": "HG=F",
    "/PL": "PL=F",
    "/ZC": "ZC=F",
    "/ZS": "ZS=F",
    "/ZW": "ZW=F",
    "/ZL": "ZL=F",
    "/ZM": "ZM=F",
    "/KE": "KE=F",
    "/HE": "HE=F",
    "/LE": "LE=F",
    "/GF": "GF=F",
    "/6A": "6A=F",
    "/6B": "6B=F",
    "/6C": "6C=F",
    "/6E": "6E=F",
    "/6J": "6J=F",
    "/6S": "6S=F",
    "/6N": "6N=F",
    "/6M": "6M=F",
    "/BTC": "BTC=F",
    "/MBT": "MBT=F",
    "/ETH": "ETH=F",
    "/VX": "^VIX",
}

# Cash indices trade options under one name and chart under another.
_INDICES: dict[str, str] = {
    "SPX": "^GSPC",
    "SPXW": "^GSPC",
    "XSP": "^GSPC",
    "NDX": "^NDX",
    "NDXP": "^NDX",
    "XND": "^NDX",
    "RUT": "^RUT",
    "MRUT": "^RUT",
    "VIX": "^VIX",
    "DJX": "^DJI",
}


def yahoo_symbol(underlying: str | None) -> str | None:
    """The chart that answers a trend question about this product, or None."""
    root = product_root(underlying)
    if not root:
        return None
    if root.startswith("/"):
        return _FUTURES.get(root)
    if root in _INDICES:
        return _INDICES[root]
    # BRK/B on the broker is BRK-B on the chart.
    return root.replace("/", "-").replace(".", "-")


@dataclass(frozen=True, slots=True)
class Bar:
    """One trading day. Floats on purpose: these feed averages and oscillators,
    not anyone's cost basis, and nothing here is ever shown as money."""

    day: date
    open: float
    high: float
    low: float
    close: float


Fetcher = Callable[[str, str], Awaitable[Mapping[str, Any]]]


def parse_chart(payload: Mapping[str, Any]) -> list[Bar]:
    """Yahoo's chart JSON as daily bars, oldest first.

    Days with no close are dropped rather than filled: a holiday is not a flat
    day, and a forward-filled bar would drag every average towards it.
    """
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return []
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    offset = int((result.get("meta") or {}).get("gmtoffset") or 0)
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []

    bars: dict[date, Bar] = {}
    for i, stamp in enumerate(stamps):
        try:
            close = closes[i]
        except IndexError:
            break
        if close is None:
            continue
        high = highs[i] if i < len(highs) and highs[i] is not None else close
        low = lows[i] if i < len(lows) and lows[i] is not None else close
        open_ = opens[i] if i < len(opens) and opens[i] is not None else close
        # The exchange's own calendar day, not UTC's: a bar stamped at the
        # 9:30 open in New York belongs to that New York date.
        day = datetime.fromtimestamp(int(stamp) + offset, UTC).date()
        bars[day] = Bar(day=day, open=float(open_), high=float(high), low=float(low), close=float(close))
    return [bars[d] for d in sorted(bars)]


def range_for(earliest: date | None, today: date) -> str:
    """The shortest Yahoo range that reaches back past ``earliest``.

    Indicators need a warm-up before their first honest value — a 21-day EMA
    seeded on day one is mostly its seed — so the window reaches 150 days
    further back than the first date anyone will ask about.
    """
    if earliest is None:
        return "1y"
    span = (today - earliest).days + 150
    for label, days in (("1y", 365), ("2y", 730), ("5y", 1826)):
        if span <= days:
            return label
    return "10y"


class PriceHistory:
    """Daily bars per product, cached, fetched a few at a time.

    A product Yahoo does not know, or a request that fails, is simply absent
    from the answer and named in :attr:`errors` — one bad symbol must not take
    the whole page down, and a missing chart must read as missing, not flat.
    """

    def __init__(self, fetch: Fetcher | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch or _http_fetch
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, list[Bar]]] = {}
        self._lock = asyncio.Lock()
        self.errors: dict[str, str] = {}

    async def daily(self, underlyings: Sequence[str], range_: str = "2y") -> dict[str, list[Bar]]:
        """Bars keyed by the product root asked for ("SPY", "/ZB")."""
        wanted: dict[str, str] = {}
        for underlying in underlyings:
            root = product_root(underlying)
            symbol = yahoo_symbol(root)
            if symbol:
                wanted[root] = symbol
            else:
                self.errors[root] = "no chart for this product"

        now = self._clock()
        out: dict[str, list[Bar]] = {}
        missing: dict[str, str] = {}
        for root, symbol in wanted.items():
            hit = self._cache.get((symbol, range_))
            if hit and now - hit[0] < CACHE_SECONDS:
                out[root] = hit[1]
            else:
                missing[root] = symbol

        if missing:
            async with self._lock:
                gate = asyncio.Semaphore(CONCURRENCY)

                async def one(root: str, symbol: str) -> None:
                    async with gate:
                        try:
                            bars = parse_chart(await self._fetch(symbol, range_))
                        except Exception as exc:  # noqa: BLE001 - reported, never raised
                            logger.info("No price history for %s (%s): %s", root, symbol, exc)
                            self.errors[root] = str(exc) or exc.__class__.__name__
                            stale = self._cache.get((symbol, range_))
                            if stale:
                                out[root] = stale[1]
                            return
                        if not bars:
                            self.errors[root] = "no bars returned"
                            return
                        self._cache[(symbol, range_)] = (self._clock(), bars)
                        self.errors.pop(root, None)
                        out[root] = bars

                await asyncio.gather(*(one(r, s) for r, s in missing.items()))
        return out


async def _http_fetch(symbol: str, range_: str) -> Mapping[str, Any]:
    params = {"range": range_, "interval": "1d", "includePrePost": "false"}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}) as client:
        for attempt in range(2):
            response = await client.get(CHART_URL.format(symbol=symbol), params=params)
            if response.status_code == 429 and attempt == 0:
                # Politeness, once. A second refusal is an answer.
                await asyncio.sleep(1.5)
                continue
            response.raise_for_status()
            return response.json()
    response.raise_for_status()
    return response.json()
