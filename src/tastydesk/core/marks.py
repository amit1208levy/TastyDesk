"""Live pricing: attach marks and greeks to the legs of open strategies.

Two sources, deliberately layered, because they fail in different ways.

REST (``/market-data/by-type``) is the baseline. One batched call returns
mark/bid/ask for options, equities, indices and futures, it works at 3am when
no websocket is up, and it never half-arrives. Every :meth:`MarkService.refresh`
uses it.

DXLink (``Greeks`` events) is the only source of per-option delta, gamma, theta,
vega and IV. The app needs delta to judge how much trouble a short strike is in,
so the stream is worth the complication — but it is a live feed, and outside
market hours it may deliver nothing at all. Every wait on it is time-boxed, and
whatever did not arrive stays ``None``.

The one rule that outranks everything here
------------------------------------------
A missing mark stays ``None``. Never the previous close, never zero, never a
number this module computed to fill a hole. Downstream already knows how to say
"partially quoted" (:attr:`StrategyPnL.fully_quoted`); a fabricated price would
instead produce a confident, wrong P&L — the single failure this application
exists to prevent. The only fallback allowed is the broker's own ``mid`` for the
same instant, because that is the same quote under another name, not a guess.

One exception, and it only ever replaces a price, never fills a hole: an
option whose market is too wide to take a midpoint from is priced from its
own volatility and its underlying (:func:`estimate_mark`), held inside its bid
and ask, and marked ``mark_estimated`` so the screen says "low volume
estimate" beside it. A midpoint between two people who are not trading is
itself a guess, and a worse one — it once put this app $1,100 away from the
broker on a single soybean call.

Stale data has two different costs, so it gets two different rules:

* Marks are re-authored on every refresh. A symbol that came back empty has its
  mark/bid/ask cleared, because an hour-old price silently feeding ``open_pnl``
  is exactly the lie described above.
* Greeks are merged, never cleared. Their absence means "the stream was quiet
  inside the time box", not "this option has no delta", and a slightly old delta
  is far more useful to the risk module than no delta at all.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks
from tastytrade.instruments import Option
from tastytrade.market_data import get_market_data_by_type
from tastytrade.metrics import get_market_metrics

from tastydesk.core.models import Leg, OptionType, Strategy, UnderlyingQuote
from tastydesk.core.occ import parse_option_symbol, product_root
from tastydesk.core.scenario import black_scholes, leg_underlying

logger = logging.getLogger(__name__)

__all__ = [
    "MarkService",
    "TastyClient",
    "WIDE_MARKET",
    "estimate_mark",
    "is_wide",
    "streamer_symbol_for",
    "MARKET_DATA_BATCH_LIMIT",
    "DEFAULT_GREEKS_TIMEOUT",
]

# The by-type endpoint takes at most 100 symbols across all instrument types.
MARKET_DATA_BATCH_LIMIT = 100

# How long a one-shot greeks snapshot waits before giving up. Short on purpose:
# a dashboard refresh must not hang until the market opens.
DEFAULT_GREEKS_TIMEOUT = 6.0

# instrument type -> the keyword get_market_data_by_type wants it under.
_BUCKETS = {
    "equity": "equities",
    "equity option": "options",
    "index": "indices",
    "future": "futures",
    "future option": "future_options",
    "cryptocurrency": "cryptocurrencies",
}

# A market wider than this, as a share of its midpoint, is too wide to take a
# midpoint from. A liquid option trades a tick or two wide — a few percent at
# most. A soybean option quoted 23.50 / 36.00 is 42% wide, and there the
# broker's screen and its API had already parted company by $580.
WIDE_MARKET = Decimal("0.20")

_DAYS_PER_YEAR = Decimal(365)

# Cash-settled index products quote under their own instrument type; asking for
# them as equities returns nothing. This list only seeds the guess — a symbol
# that comes back unquoted is retried in the other bucket.
_INDEX_SYMBOLS = frozenset(
    {"SPX", "SPXW", "XSP", "NDX", "NDXP", "RUT", "RUTW", "DJX", "OEX", "XEO", "VIX", "VIX9D", "VXN", "RVX"}
)


class TastyClient(Protocol):
    """The slice of the account client this module needs.

    Only a session is strictly required — everything else is reached through the
    SDK's own module-level helpers. A client that would rather own its HTTP
    traffic (for caching, rate limiting or tests) may additionally expose
    ``get_market_data_by_type(**buckets)`` and ``get_market_metrics(symbols)``;
    :class:`MarkService` prefers those when they exist.
    """

    async def get_session(self) -> Any: ...  # pragma: no cover - structural typing only


OnUpdate = Callable[[Strategy], None | Awaitable[None]]
StreamerFactory = Callable[[Any], Any]


def _dec(value: Any) -> Decimal | None:
    """Coerce whatever the SDK handed us into Decimal, or None. Never guesses.

    The tastytrade models are inconsistent by field: ``implied_volatility_index``
    is a Decimal while ``implied_volatility_index_rank`` is a *string* of the
    same fraction, and dxfeed greeks arrive as floats. Floats go through ``str``
    so 0.35 stays 0.35 instead of picking up binary noise.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, (int, str)):
        try:
            return Decimal(str(value).strip())
        except InvalidOperation:
            return None
    return None


def _records(value: Any) -> list[Any]:
    """Normalise a client response into a list of records.

    The SDK's module-level helpers return lists, but our own
    :class:`tastydesk.core.client.TastyClient` returns dicts keyed by symbol —
    that is the whole point of its batching. Iterating a dict hands back its
    *keys*: bare strings whose ``.symbol`` and ``.mark`` do not exist, so every
    record would be silently discarded and an entire portfolio would come back
    unpriced and volatility-blind. Take the values when given a mapping.
    """
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.values())
    return list(value)


def _norm(symbol: str) -> str:
    """Key for matching a response to a request.

    OCC symbols carry padding spaces (``SPY   251219P00580000``) and different
    corners of the API disagree about how many. Compare without them.
    """
    return symbol.replace(" ", "").upper()


def _instrument_key(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _bucket_for(leg: Leg) -> str:
    """Which request bucket a leg belongs in."""
    bucket = _BUCKETS.get(_instrument_key(leg.instrument_type))
    if bucket:
        return bucket
    # An unrecognised instrument type must not cost us a quote: fall back to
    # what the symbol itself says. Futures symbols start with "/", and futures
    # options with "./".
    symbol = (leg.symbol or "").strip()
    if parse_option_symbol(symbol):
        return "future_options" if symbol.startswith("./") else "options"
    return "futures" if symbol.startswith("/") else "equities"


def streamer_symbol_for(occ: str) -> str | None:
    """OCC -> dxfeed streamer symbol, or None if the SDK cannot convert it.

    ``SPY   251219P00580000`` becomes ``.SPY251219P580``. Futures options use a
    different scheme the helper may reject; one odd symbol must not cost the
    whole portfolio its greeks, so failures return None.

    The SDK signals "I could not parse that" by returning an empty string rather
    than raising (an OCC symbol whose root is not space-padded does this), and an
    empty streamer symbol is exactly as unusable as an exception — subscribing to
    it would ask the feed for a quote on nothing. Both answers become None.
    """
    try:
        return Option.occ_to_streamer_symbol(occ) or None
    except Exception:  # noqa: BLE001 - any SDK parse failure is just "no greeks"
        logger.debug("No streamer symbol for %s", occ, exc_info=True)
        return None


def _chunk(
    buckets: dict[str, list[str]], limit: int = MARKET_DATA_BATCH_LIMIT
) -> Iterator[dict[str, list[str]]]:
    """Split a by-type request into calls of at most ``limit`` symbols total."""
    batch: dict[str, list[str]] = {}
    count = 0
    for bucket, symbols in buckets.items():
        for symbol in symbols:
            batch.setdefault(bucket, []).append(symbol)
            count += 1
            if count == limit:
                yield batch
                batch, count = {}, 0
    if batch:
        yield batch


def _apply_greeks(leg: Leg, event: Any) -> None:
    """Merge one Greeks event onto a leg. dxfeed calls IV ``volatility``."""
    leg.delta = _dec(getattr(event, "delta", None))
    leg.gamma = _dec(getattr(event, "gamma", None))
    leg.theta = _dec(getattr(event, "theta", None))
    leg.vega = _dec(getattr(event, "vega", None))
    leg.iv = _dec(getattr(event, "volatility", None))


def _pick_mark(data: Any) -> Decimal | None:
    """Which of the broker's two prices to believe, when they disagree.

    tastytrade sends a ``mid`` and a ``mark`` for every option, and on a
    liquid contract they are the same number. On a thin one they are not, and
    each has been badly wrong once in this book:

    * a soybean option quoted 23.50 bid, 36.00 offered: mid 29.75, which is
      what the platform showed, and mark 35.56 — $580 away on one position;
    * a January soybean call after the close, with the bid collapsed to 6.00
      against a 29.00 offer: mid 17.50, and mark 28.56 — while the contract
      last traded at 28.50 and settled at 28.25. The mid put $1,100 of profit on
      a leg the platform showed up $231.

    Neither field is reliably the right one, so neither wins by rule. When they
    agree, either will do. When they do not, the one that is consistent with
    where the contract actually traded is the one to trust: the last trade, or
    the settlement if nothing has traded. That price is used only to choose
    between the broker's own two figures, never as the mark itself — a last
    trade can be hours old, and a mark copied from it would be stale on purpose.
    """
    mid = _dec(getattr(data, "mid", None))
    mark = _dec(getattr(data, "mark", None))
    if mid is None:
        return mark
    if mark is None:
        return mid
    larger = max(abs(mid), abs(mark))
    if larger == 0 or abs(mid - mark) <= larger * Decimal("0.05"):
        return mid
    anchor = _dec(getattr(data, "last", None)) or _dec(getattr(data, "close", None))
    if anchor is None or anchor <= 0:
        return mid
    return mid if abs(mid - anchor) <= abs(mark - anchor) else mark


def is_wide(leg: Leg) -> bool:
    """Whether this option's market is too thin to price it from.

    Nobody bidding, nobody offering, or a gap between them wider than
    :data:`WIDE_MARKET` of the midpoint. A quote that did not report its bid
    and ask at all is not judged: that is missing information, not a thin
    market. Outright futures and shares never are: their markets are deep
    enough that the midpoint is the price.
    """
    if not leg.is_option:
        return False
    bid, ask = leg.bid, leg.ask
    if bid is None or ask is None:
        return False
    if bid <= 0 or ask <= 0 or ask < bid:
        return True
    mid = (bid + ask) / 2
    return (ask - bid) > mid * WIDE_MARKET


def estimate_mark(leg: Leg, spot: Decimal | None, today: date) -> Decimal | None:
    """A model price for an option whose market is too wide to trust.

    When nobody is really trading a contract, the midpoint is not a price,
    it is the halfway point between two people who are not trading. The
    broker's platform does not show that halfway point either — on a /ZS put
    with both its fields at 5.00 the platform printed 4.81, a price from a
    model. This is the same idea: Black-76 from the contract's own implied
    volatility and the price of the future it is written on.

    The result is held inside the bid and ask where there are both, because
    a price nobody would sell below or buy above is not a price either. The
    market still bounds the answer; the model only decides where inside it.

    Futures options only. Black-76 at zero rate is the right model for an
    option on a future, near enough; for an option on a stock it leaves out
    the interest and dividends the feed's volatility was solved with, and put
    a BBY call 20 cents under its own midpoint. Stock options keep the
    broker's figure, which matched the platform on every one checked.

    ``None`` when there is nothing to model from — no volatility, no
    underlying price, no expiry — and the caller keeps the broker's figure.
    """
    if not leg.is_option or not leg.symbol.strip().startswith("./"):
        return None
    if leg.strike is None or spot is None or spot <= 0:
        return None
    if leg.iv is None or leg.iv <= 0:
        return None
    left = leg.dte(today)
    if left is None or left < 0:
        return None
    right = leg.option_type or OptionType.CALL
    price = black_scholes(right, spot, leg.strike, Decimal(left) / _DAYS_PER_YEAR, leg.iv)
    if leg.bid is not None and leg.bid > 0:
        price = max(price, leg.bid)
    if leg.ask is not None and leg.ask > 0:
        price = min(price, leg.ask)
    return price.quantize(Decimal("0.0001"))


def _underlying_symbol(leg: Leg) -> str | None:
    """The contract an option leg is priced off, as the quote endpoint names it."""
    if not leg.is_option:
        return None
    symbol = leg_underlying(leg)
    return symbol or None


class MarkService:
    """Fills in the live half of every :class:`Leg`.

    ``streamer_factory`` exists so tests (and any future replay mode) can hand in
    something other than a real websocket; it takes a session and returns an
    async context manager with ``subscribe``/``listen``.
    """

    def __init__(
        self,
        client: TastyClient,
        *,
        streamer_factory: StreamerFactory | None = None,
        greeks_timeout: float = DEFAULT_GREEKS_TIMEOUT,
    ) -> None:
        self._client = client
        self._streamer_factory = streamer_factory or DXLinkStreamer
        self._greeks_timeout = greeks_timeout

    # ------------------------------------------------------------------ marks

    async def refresh(
        self,
        strategies: Sequence[Strategy],
        *,
        include_greeks: bool = True,
        greeks_timeout: float | None = None,
    ) -> None:
        """Price every leg of ``strategies`` in place.

        Callers pass the strategies they care about (normally the open ones).
        Legs whose symbol the broker did not quote come back with mark/bid/ask
        set to None — see the module docstring on why that is not softened.
        """
        legs_by_symbol = self._index_legs(strategies)
        if not legs_by_symbol:
            return

        buckets: dict[str, list[str]] = {}
        for symbol, legs in legs_by_symbol.items():
            buckets.setdefault(_bucket_for(legs[0]), []).append(symbol)

        quotes = await self._market_data(buckets)

        quoted = 0
        for symbol, legs in legs_by_symbol.items():
            data = quotes.get(_norm(symbol))
            for leg in legs:
                self._apply_quote(leg, data)
            if data is not None and _dec(getattr(data, "mark", None)) is not None:
                quoted += 1
        if quoted < len(legs_by_symbol):
            logger.info("Quoted %d of %d symbols; the rest stay unpriced", quoted, len(legs_by_symbol))

        await self._estimate_thin(legs_by_symbol)

        if include_greeks:
            await self._snapshot_greeks(
                legs_by_symbol,
                timeout=self._greeks_timeout if greeks_timeout is None else greeks_timeout,
            )

    async def _estimate_thin(self, legs_by_symbol: dict[str, list[Leg]]) -> None:
        """Replace the midpoint with a model price where the market is too wide.

        The underlyings are only asked for when some leg needs one, so a
        book in a liquid market costs no extra call. And only where a model
        can be run: a thin option with no volatility or no underlying price
        keeps the broker's figure, which is still the best number available —
        just not a good one.
        """
        thin = [
            leg
            for legs in legs_by_symbol.values()
            for leg in legs
            if leg.mark is not None and is_wide(leg) and _underlying_symbol(leg)
        ]
        if not thin:
            return
        buckets: dict[str, list[str]] = {}
        for symbol in sorted({_underlying_symbol(leg) for leg in thin}):
            buckets.setdefault(self._underlying_bucket(symbol), []).append(symbol)
        try:
            quotes = await self._market_data(buckets)
        except Exception:  # noqa: BLE001 - the broker's figure still stands
            logger.warning("No underlying prices for thin options", exc_info=True)
            return
        today = date.today()
        for leg in thin:
            data = quotes.get(_norm(_underlying_symbol(leg) or ""))
            spot = None
            if data is not None:
                spot = _dec(getattr(data, "mid", None)) or _dec(getattr(data, "mark", None))
            estimate = estimate_mark(leg, spot, today)
            if estimate is not None:
                leg.mark = estimate
                leg.mark_estimated = True

    @staticmethod
    def _index_legs(strategies: Sequence[Strategy]) -> dict[str, list[Leg]]:
        """Symbol -> every leg holding it. The dedupe the API batch needs.

        The same short SPY put can sit in three different trades; it is one
        symbol to request and three legs to write back to.
        """
        legs_by_symbol: dict[str, list[Leg]] = {}
        for strategy in strategies:
            for leg in strategy.legs:
                if leg.symbol:
                    legs_by_symbol.setdefault(leg.symbol, []).append(leg)
        return legs_by_symbol

    @staticmethod
    def _apply_quote(leg: Leg, data: Any | None) -> None:
        leg.mark_estimated = False
        if data is None:
            # Clearing, not keeping: a stale price feeding open_pnl is worse
            # than an honest "not quoted".
            leg.mark = leg.bid = leg.ask = None
            leg.delta = leg.gamma = leg.theta = leg.vega = leg.iv = None
            return
        leg.bid = _dec(getattr(data, "bid", None))
        leg.ask = _dec(getattr(data, "ask", None))
        # The broker's own mid, then its mark. Never last, never close, never
        # (bid+ask)/2 of our own making.
        #
        # It used to be the other way round, on the assumption that a field
        # called "mark" is the mark. Checked against the positions endpoint —
        # which is what the tastytrade platform itself prints — mid matched all
        # nineteen open positions and mark matched eighteen. The one it missed
        # was a soybean option quoted 23.50 bid, 36.00 ask: mid said 29.75, the
        # platform said 29.75, and "mark" said 35.5625, which on a 50x contract
        # put this app $580 away from the broker's screen on a single position.
        # A wide market is exactly where a mark matters most and exactly where
        # that field goes its own way.
        leg.mark = _pick_mark(data)

        # Greeks come back on the same response as the price. That matters
        # because the DXLink streamer needs an API quote token, which
        # tastytrade refuses to issue to some accounts — and without delta
        # there is no short-strike risk signal at all. Whatever the streamer
        # later supplies will overwrite these; until then these are what the
        # risk scoring runs on.
        for field in ("delta", "gamma", "theta", "vega"):
            value = _dec(getattr(data, field, None))
            if value is not None:
                setattr(leg, field, value)
        iv = _dec(getattr(data, "iv", None))
        if iv is not None:
            leg.iv = iv

    # ------------------------------------------------------------- underlyings

    async def underlying_quotes(self, symbols: Iterable[str]) -> dict[str, UnderlyingQuote]:
        """Price plus volatility context for each underlying, keyed by symbol.

        The risk module reads three things from here that nothing else provides:
        IV rank (is this worth selling premium against), the ex-dividend date (a
        short ITM call gets assigned the day before one) and the next earnings
        date (the one scheduled event that reprices an underlying overnight).
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = (raw or "").strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                wanted.append(symbol)
        if not wanted:
            return {}

        quotes = {symbol: UnderlyingQuote(symbol=symbol) for symbol in wanted}

        buckets: dict[str, list[str]] = {}
        for symbol in wanted:
            buckets.setdefault(self._underlying_bucket(symbol), []).append(symbol)
        data = await self._market_data(buckets)

        # An index requested as an equity (or the reverse) simply returns
        # nothing, so retry the misses in the other bucket before giving up.
        retry: dict[str, list[str]] = {}
        for bucket, batch in buckets.items():
            if bucket not in ("equities", "indices"):
                continue
            other = "indices" if bucket == "equities" else "equities"
            for symbol in batch:
                if _norm(symbol) not in data:
                    retry.setdefault(other, []).append(symbol)
        if retry:
            data.update(await self._market_data(retry))

        for symbol, quote in quotes.items():
            found = data.get(_norm(symbol))
            if found is not None:
                quote.last = _dec(getattr(found, "last", None))
                quote.mark = _dec(getattr(found, "mark", None))
                if quote.mark is None:
                    quote.mark = _dec(getattr(found, "mid", None))

        # tastytrade answers a metrics request for /CLZ6 with a row keyed /CL:
        # volatility, beta and earnings are properties of the product, not of
        # the contract month. Keying the result by the symbol we asked for
        # silently dropped every futures underlying — which in this book was
        # most of it, leaving IV rank blank on ten of thirteen underlyings and
        # taking the entry-IV slice down with it.
        by_root: dict[str, list[UnderlyingQuote]] = {}
        for symbol, quote in quotes.items():
            by_root.setdefault(product_root(symbol), []).append(quote)

        for metric in await self._metrics(wanted):
            key = str(getattr(metric, "symbol", "")).strip().upper()
            # The exact match *and* the whole product, not one or the other.
            # A book holding an outright /ZB contract as well as options on
            # /ZBZ6 puts both symbols in this dict, and the metrics row keyed
            # "/ZB" then landed only on the outright — leaving IV rank, beta
            # and every beta-weighted delta blank on the position that had the
            # options in it.
            targets = {id(q): q for q in by_root.get(product_root(key), [])}
            if key in quotes:
                targets[id(quotes[key])] = quotes[key]
            for quote in targets.values():
                self._apply_metric(quote, metric)

        return quotes

    @staticmethod
    def _apply_metric(quote: UnderlyingQuote, metric: Any) -> None:
        quote.iv = _dec(getattr(metric, "implied_volatility_index", None))
        # Already a fraction: 0.35 means IVR 35. Scaling it here would be
        # the classic double-divide that turns a 35 rank into 0.35.
        quote.iv_rank = _dec(getattr(metric, "implied_volatility_index_rank", None))
        if quote.iv_rank is None:
            # Same scale, different vendor calculation; tastytrade fills one
            # or the other depending on the underlying.
            quote.iv_rank = _dec(getattr(metric, "tw_implied_volatility_index_rank", None))
        quote.iv_percentile = _dec(getattr(metric, "implied_volatility_percentile", None))
        quote.iv_rank_tw = _dec(getattr(metric, "tw_implied_volatility_index_rank", None))
        updated = getattr(metric, "implied_volatility_updated_at", None)
        quote.iv_updated_at = updated if isinstance(updated, datetime) else None
        quote.ex_dividend_date = _as_date(getattr(metric, "dividend_ex_date", None))
        quote.earnings_date = _next_earnings(getattr(metric, "earnings", None))
        # Beta against SPY, as tastytrade publishes it. The only honest way
        # to add a /ZB delta to an XLE delta, and absent for nothing in this
        # book once the lookup falls back to the product root.
        quote.beta = _dec(getattr(metric, "beta", None))

    @staticmethod
    def _underlying_bucket(symbol: str) -> str:
        if symbol.startswith("/"):
            return "futures"
        if symbol.startswith("$") or symbol in _INDEX_SYMBOLS:
            return "indices"
        return "equities"

    # ----------------------------------------------------------------- greeks

    async def _snapshot_greeks(self, legs_by_symbol: dict[str, list[Leg]], *, timeout: float) -> None:
        """One time-boxed pass over the stream, merging whatever shows up."""
        targets = self._streamer_targets(legs_by_symbol)
        if not targets:
            return
        events = await self._collect_greeks(list(targets), timeout)
        for streamer_symbol, event in events.items():
            for leg in targets.get(streamer_symbol, ()):
                _apply_greeks(leg, event)
        missing = len(targets) - len(events)
        if missing:
            # Normal outside market hours. Those legs keep whatever greeks they
            # already had, and None if they had none.
            logger.info("Greeks arrived for %d of %d options", len(events), len(targets))

    @staticmethod
    def _streamer_targets(legs_by_symbol: dict[str, list[Leg]]) -> dict[str, list[Leg]]:
        targets: dict[str, list[Leg]] = {}
        for symbol, legs in legs_by_symbol.items():
            option_legs = [leg for leg in legs if leg.is_option or parse_option_symbol(symbol)]
            if not option_legs:
                continue
            streamer = streamer_symbol_for(symbol)
            if streamer:
                targets.setdefault(streamer, []).extend(option_legs)
        return targets

    async def _collect_greeks(self, streamer_symbols: list[str], timeout: float) -> dict[str, Any]:
        """Listen until every symbol has reported or the clock runs out.

        ``out`` is filled in place so a timeout still returns the partial haul —
        half the greeks is a better dashboard than none, and the untouched legs
        stay honest at None.
        """
        out: dict[str, Any] = {}
        try:
            await asyncio.wait_for(self._drain_greeks(streamer_symbols, out), timeout)
        except TimeoutError:
            logger.debug(
                "Greeks time box of %.1fs expired with %d of %d", timeout, len(out), len(streamer_symbols)
            )
        except Exception:  # noqa: BLE001 - a dead websocket must not kill a refresh
            logger.warning("Greeks stream unavailable; leaving greeks as they were", exc_info=True)
        return out

    async def _drain_greeks(self, streamer_symbols: list[str], out: dict[str, Any]) -> None:
        session = await self._session()
        async with self._streamer_factory(session) as streamer:
            await streamer.subscribe(Greeks, streamer_symbols)
            wanted = set(streamer_symbols)
            async for event in streamer.listen(Greeks):
                out[str(event.event_symbol)] = event
                if not wanted - out.keys():
                    return

    async def stream_greeks(self, strategies: Sequence[Strategy], on_update: OnUpdate) -> None:
        """Long-lived subscription for the live dashboard.

        Runs until cancelled or until the streamer stops yielding. ``on_update``
        is handed the *strategy* a changed leg belongs to, never the leg: the
        dashboard recomputes risk per strategy, and a single leg's numbers mean
        nothing on their own.
        """
        owners: dict[int, Strategy] = {}
        index: dict[str, list[tuple[int, Leg]]] = {}
        for strategy in strategies:
            owners[id(strategy)] = strategy
            for leg in strategy.legs:
                if not (leg.is_option or parse_option_symbol(leg.symbol)):
                    continue
                streamer = streamer_symbol_for(leg.symbol)
                if streamer:
                    index.setdefault(streamer, []).append((id(strategy), leg))
        if not index:
            logger.debug("No option legs to stream greeks for")
            return

        session = await self._session()
        async with self._streamer_factory(session) as streamer:
            await streamer.subscribe(Greeks, list(index))
            async for event in streamer.listen(Greeks):
                touched = index.get(str(event.event_symbol))
                if not touched:
                    continue
                changed: list[int] = []
                for owner_id, leg in touched:
                    _apply_greeks(leg, event)
                    if owner_id not in changed:
                        changed.append(owner_id)
                for owner_id in changed:
                    result = on_update(owners[owner_id])
                    if inspect.isawaitable(result):
                        await result

    # ------------------------------------------------------------ client glue

    async def _market_data(self, buckets: dict[str, list[str]]) -> dict[str, Any]:
        """Batched by-type fetch, indexed by normalised symbol."""
        found: dict[str, Any] = {}
        for batch in _chunk({k: v for k, v in buckets.items() if v}):
            for item in await self._fetch_market_data(batch):
                symbol = getattr(item, "symbol", None)
                if symbol:
                    found[_norm(str(symbol))] = item
        return found

    async def _fetch_market_data(self, batch: dict[str, list[str]]) -> list[Any]:
        fn = _client_method(self._client, "get_market_data_by_type", "market_data")
        if fn is not None:
            return _records(await fn(**batch))
        session = await self._session()
        return _records(await get_market_data_by_type(session, **batch))

    async def _metrics(self, symbols: Sequence[str]) -> list[Any]:
        fn = _client_method(self._client, "get_market_metrics", "market_metrics")
        out: list[Any] = []
        for start in range(0, len(symbols), MARKET_DATA_BATCH_LIMIT):
            batch = list(symbols[start : start + MARKET_DATA_BATCH_LIMIT])
            try:
                if fn is not None:
                    out.extend(_records(await fn(batch)))
                else:
                    session = await self._session()
                    out.extend(_records(await get_market_metrics(session, batch)))
            except Exception:  # noqa: BLE001 - metrics are context, prices are not
                logger.warning("Market metrics unavailable for %s", batch, exc_info=True)
        return out

    async def _session(self) -> Any:
        client = self._client
        for name in ("get_session", "session"):
            attr = getattr(client, name, None)
            if attr is None:
                continue
            value = attr() if callable(attr) else attr
            if inspect.isawaitable(value):
                value = await value
            if value is not None:
                return value
        raise RuntimeError(
            f"{type(client).__name__} exposes no tastytrade session (.get_session() or .session)"
        )


def _client_method(client: Any, *names: str) -> Callable[..., Awaitable[Any]] | None:
    for name in names:
        fn = getattr(client, name, None)
        if callable(fn):
            return fn
    return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    return None


def _next_earnings(earnings: Any) -> date | None:
    """The *upcoming* report date, or None.

    ``EarningsReport.expected_report_date`` is the field that means what we
    want. ``occurred_date`` is usually the last report already in the books, and
    handing a past date to the risk module would raise an earnings warning for
    an event that has been and gone — so it is only used when it is in the
    future, which is how the API sometimes reports a confirmed upcoming date.
    """
    if earnings is None:
        return None
    expected = _as_date(getattr(earnings, "expected_report_date", None))
    if expected is not None:
        return expected
    occurred = _as_date(getattr(earnings, "occurred_date", None))
    if occurred is not None and occurred >= date.today():
        return occurred
    return None
