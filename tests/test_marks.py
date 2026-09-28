"""Tests for the live pricing layer.

Nothing here touches the network. The broker client and the DXLink streamer are
both faked, because what needs proving is our own policy, not the SDK's HTTP or
websocket code:

* a price we did not receive stays ``None`` -- never zero, never the open price,
  never last refresh's number. A fabricated mark produces a confident, wrong
  ``open_pnl``, which is the one failure this application exists to prevent;
* one symbol is requested once no matter how many strategies hold it;
* requests respect the 100-symbol batch limit of ``/market-data/by-type``;
* IV rank keeps the scale tastytrade sent it in;
* greeks that never arrived leave ``delta`` at ``None``. Outside market hours
  that is the normal case, and it must degrade honestly rather than invent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from tastytrade.instruments import Option

from tastydesk.core.marks import (
    MARKET_DATA_BATCH_LIMIT,
    MarkService,
    estimate_mark,
    is_wide,
    streamer_symbol_for,
)
from tastydesk.core.models import Direction, Leg, OptionType, Strategy, StrategyType
from tastydesk.core.models import RiskProfile as Profile
from tastydesk.core.occ import build_occ_symbol

OPENED = datetime(2026, 8, 20, 14, 30)
EXPIRY = date(2025, 12, 19)

# The verified baseline symbol from the project notes, in the padded OCC form
# the broker actually hands back.
SPY_580P = "SPY   251219P00580000"
SPY_575P = "SPY   251219P00575000"


# --------------------------------------------------------------------- fakes


@dataclass
class FakeQuote:
    """Stands in for tastytrade.market_data.MarketData.

    Only the fields marks.py reads exist; anything it asks for and does not find
    must be treated as "not quoted", which is what ``getattr(..., None)`` gives.
    """

    symbol: str
    mark: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    mid: Decimal | None = None
    last: Decimal | None = None
    iv: Decimal | None = None


@dataclass
class FakeEarnings:
    expected_report_date: date | None = None
    occurred_date: date | None = None


@dataclass
class FakeMetric:
    """Stands in for MarketMetricInfo.

    ``implied_volatility_index_rank`` is deliberately a *string* here: that is
    how the SDK reports it, and it is already a fraction (0.35 == IVR 35).
    """

    symbol: str
    implied_volatility_index: Decimal | None = None
    implied_volatility_index_rank: str | None = None
    tw_implied_volatility_index_rank: str | None = None
    implied_volatility_percentile: str | None = None
    dividend_ex_date: date | None = None
    earnings: FakeEarnings | None = None


class FakeSession:
    """Identity is all marks.py needs from a session."""


class FakeClient:
    """A broker client that answers from a table and records how it was asked.

    Quotes are looked up by the same space-insensitive key marks.py uses, so a
    test can hand back a symbol padded differently from the request and still
    expect it to land.
    """

    def __init__(
        self,
        quotes: Iterable[FakeQuote] = (),
        metrics: Iterable[FakeMetric] = (),
        *,
        only_bucket: dict[str, str] | None = None,
        metrics_error: Exception | None = None,
    ) -> None:
        self.session = FakeSession()
        self.quotes = {_key(q.symbol): q for q in quotes}
        self.metrics = {m.symbol: m for m in metrics}
        self.only_bucket = only_bucket or {}
        self.metrics_error = metrics_error
        self.calls: list[dict[str, list[str]]] = []
        self.metric_calls: list[list[str]] = []

    async def get_session(self) -> FakeSession:
        return self.session

    def _lookup(self, **buckets: list[str]) -> list[FakeQuote]:
        self.calls.append({k: list(v) for k, v in buckets.items()})
        out: list[FakeQuote] = []
        for bucket, symbols in buckets.items():
            for symbol in symbols:
                quote = self.quotes.get(_key(symbol))
                if quote is None:
                    continue
                # Some products only answer under one instrument type; asking in
                # the wrong bucket silently returns nothing, exactly like the API.
                required = self.only_bucket.get(_key(symbol))
                if required is not None and required != bucket:
                    continue
                out.append(quote)
        return out

    async def get_market_data_by_type(self, **buckets: list[str]) -> list[FakeQuote]:
        return self._lookup(**buckets)

    def _metrics_for(self, symbols: Sequence[str]) -> list[FakeMetric]:
        if self.metrics_error is not None:
            raise self.metrics_error
        return [self.metrics[s] for s in symbols if s in self.metrics]

    async def get_market_metrics(self, symbols: Sequence[str]) -> list[FakeMetric]:
        self.metric_calls.append(list(symbols))
        return self._metrics_for(symbols)

    # convenience for assertions -----------------------------------------
    @property
    def requested(self) -> list[str]:
        """Every symbol asked for, across every call and every bucket."""
        return [s for call in self.calls for symbols in call.values() for s in symbols]


class MappingClient(FakeClient):
    """Shaped like the real :class:`tastydesk.core.client.TastyClient`.

    Its ``market_metrics`` returns a **dict** keyed by symbol, not a list, and it
    has neither ``get_market_metrics`` nor ``get_market_data_by_type``. Iterating
    such a dict yields bare symbol strings, so a consumer that does not unwrap it
    loses every record.
    """

    # Names the real client does not expose, so the fallback names are used.
    get_market_metrics = None
    get_market_data_by_type = None

    async def market_metrics(self, symbols: Sequence[str]) -> dict[str, FakeMetric]:
        self.metric_calls.append(list(symbols))
        return {m.symbol: m for m in self._metrics_for(symbols)}

    async def market_data(self, **buckets: list[str]) -> dict[str, FakeQuote]:
        return {q.symbol: q for q in self._lookup(**buckets)}


@dataclass
class FakeGreeks:
    """A dxfeed Greeks event. IV arrives under the name ``volatility``."""

    event_symbol: str
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    volatility: float | None = None


class FakeStreamer:
    """Async context manager with the two methods marks.py calls."""

    def __init__(
        self,
        session: Any,
        events: Sequence[FakeGreeks],
        *,
        stall: bool = False,
        fail: bool = False,
    ) -> None:
        self.session = session
        self.events = list(events)
        self.stall = stall
        self.fail = fail
        self.subscribed: list[list[str]] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeStreamer:
        if self.fail:
            raise ConnectionError("dxlink refused the connection")
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def subscribe(self, event_type: type, symbols: Sequence[str]) -> None:
        self.subscribed.append(list(symbols))

    async def listen(self, event_type: type) -> AsyncIterator[FakeGreeks]:
        for event in self.events:
            yield event
        if self.stall:
            # A live feed that has gone quiet: the caller's time box must end it.
            await asyncio.sleep(3600)


@dataclass
class FakeStreamerFactory:
    events: Sequence[FakeGreeks] = ()
    stall: bool = False
    fail: bool = False
    made: list[FakeStreamer] = field(default_factory=list)

    def __call__(self, session: Any) -> FakeStreamer:
        streamer = FakeStreamer(session, self.events, stall=self.stall, fail=self.fail)
        self.made.append(streamer)
        return streamer


# ------------------------------------------------------------------ builders


def _key(symbol: str) -> str:
    return symbol.replace(" ", "").upper()


def option_leg(
    symbol: str = SPY_580P,
    *,
    direction: Direction = Direction.SHORT,
    option_type: OptionType = OptionType.PUT,
    strike: str = "580",
    open_price: str = "2.00",
    quantity: str = "1",
    instrument_type: str = "Equity Option",
    underlying: str = "SPY",
) -> Leg:
    return Leg(
        symbol=symbol,
        instrument_type=instrument_type,
        underlying=underlying,
        direction=direction,
        quantity=Decimal(quantity),
        option_type=option_type,
        strike=Decimal(strike),
        expiration=EXPIRY,
        open_price=Decimal(open_price),
    )


def equity_leg(symbol: str = "SPY", *, quantity: str = "100") -> Leg:
    return Leg(
        symbol=symbol,
        instrument_type="Equity",
        underlying=symbol,
        direction=Direction.LONG,
        quantity=Decimal(quantity),
        multiplier=Decimal(1),
    )


def strategy(*legs: Leg, sid: str = "s-1", underlying: str = "SPY") -> Strategy:
    return Strategy(
        id=sid,
        account_number="5WX00000",
        underlying=underlying,
        strategy_type=StrategyType.PUT_CREDIT_SPREAD,
        risk_profile=Profile.DEFINED,
        legs=list(legs),
        opened_at=OPENED,
        net_credit=Decimal(100),
    )


def service(client: FakeClient, **kwargs: Any) -> MarkService:
    kwargs.setdefault("streamer_factory", FakeStreamerFactory())
    return MarkService(client, **kwargs)


# ------------------------------------------------------------ marks land right


async def test_marks_land_on_the_legs_that_asked_for_them() -> None:
    """Every leg gets its own symbol's quote, not its neighbour's."""
    short, long_ = option_leg(SPY_580P), option_leg(SPY_575P, direction=Direction.LONG, strike="575")
    client = FakeClient(
        quotes=[
            FakeQuote(SPY_580P, mark=Decimal("8.00"), bid=Decimal("7.90"), ask=Decimal("8.10")),
            FakeQuote(SPY_575P, mark=Decimal("5.50"), bid=Decimal("5.40"), ask=Decimal("5.60")),
        ]
    )

    await service(client).refresh([strategy(short, long_)], include_greeks=False)

    assert short.mark == Decimal("8.00")
    assert short.bid == Decimal("7.90")
    assert short.ask == Decimal("8.10")
    assert long_.mark == Decimal("5.50")


async def test_marks_reproduce_the_verified_baseline_pnl() -> None:
    """The 5-wide SPY put credit spread from the project notes, priced live.

    This is the end-to-end check that pricing feeds P&L with the right signs:
    cost_to_close -250, open_pnl -150 against a +100 credit.
    """
    short = option_leg(SPY_580P, open_price="2.00")
    long_ = option_leg(SPY_575P, direction=Direction.LONG, strike="575", open_price="1.00")
    client = FakeClient(
        quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00")), FakeQuote(SPY_575P, mark=Decimal("5.50"))]
    )

    await service(client).refresh([strategy(short, long_)], include_greeks=False)

    cost_to_close = short.close_cash_flow + long_.close_cash_flow
    assert cost_to_close == Decimal("-250")
    assert Decimal(100) + cost_to_close == Decimal("-150")


async def test_a_differently_padded_symbol_still_matches() -> None:
    """OCC padding varies between endpoints; matching must ignore it."""
    leg = option_leg(SPY_580P)
    client = FakeClient(quotes=[FakeQuote("SPY251219P00580000", mark=Decimal("8.00"))])

    await service(client).refresh([strategy(leg)], include_greeks=False)

    assert leg.mark == Decimal("8.00")


async def test_legs_go_into_the_bucket_their_instrument_type_names() -> None:
    legs = [option_leg(SPY_580P), equity_leg("SPY")]
    client = FakeClient()

    await service(client).refresh([strategy(*legs)], include_greeks=False)

    (call,) = client.calls
    assert call["options"] == [SPY_580P]
    assert call["equities"] == ["SPY"]


async def test_no_legs_means_no_broker_call() -> None:
    client = FakeClient()

    await service(client).refresh([], include_greeks=False)

    assert client.calls == []


# ------------------------------------------- THE RULE: a missing price is None


async def test_a_symbol_the_broker_did_not_quote_stays_none() -> None:
    """The cardinal rule of this module, asserted three ways.

    Not None-ish: actually None. Not zero (which would read as a worthless
    option and book the entire credit as profit), and not the open price (which
    would read as a flat trade). Both of those are confident lies.
    """
    leg = option_leg(SPY_580P, open_price="2.00")
    client = FakeClient(quotes=[])  # the broker returned nothing for this symbol

    await service(client).refresh([strategy(leg)], include_greeks=False)

    assert leg.mark is None
    assert leg.mark is not Decimal(0)
    assert leg.mark != Decimal(0)
    assert leg.mark != leg.open_price
    assert leg.bid is None and leg.ask is None
    # ...and the honest consequence downstream: no closing value to compute with.
    assert leg.close_cash_flow is None


async def test_one_missing_quote_does_not_poison_its_neighbours() -> None:
    quoted, unquoted = option_leg(SPY_580P), option_leg(SPY_575P, strike="575")
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await service(client).refresh([strategy(quoted, unquoted)], include_greeks=False)

    assert quoted.mark == Decimal("8.00")
    assert unquoted.mark is None


async def test_a_stale_mark_is_cleared_rather_than_kept() -> None:
    """Last refresh's price silently feeding open_pnl is the lie, not the gap."""
    leg = option_leg(SPY_580P)
    leg.mark, leg.bid, leg.ask = Decimal("8.00"), Decimal("7.90"), Decimal("8.10")
    client = FakeClient(quotes=[])

    await service(client).refresh([strategy(leg)], include_greeks=False)

    assert leg.mark is None
    assert leg.bid is None
    assert leg.ask is None


async def test_a_quote_without_a_mark_is_still_not_a_guess() -> None:
    """Bid and ask alone must not be averaged into a mark by us."""
    leg = option_leg(SPY_580P)
    client = FakeClient(quotes=[FakeQuote(SPY_580P, bid=Decimal("7.90"), ask=Decimal("8.10"))])

    await service(client).refresh([strategy(leg)], include_greeks=False)

    assert leg.mark is None
    assert leg.bid == Decimal("7.90")
    assert leg.ask == Decimal("8.10")


async def test_the_brokers_own_mid_is_the_only_allowed_fallback() -> None:
    """Same quote under another name, so it is a price, not an invention."""
    leg = option_leg(SPY_580P)
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=None, mid=Decimal("8.05"))])

    await service(client).refresh([strategy(leg)], include_greeks=False)

    assert leg.mark == Decimal("8.05")


async def test_a_genuine_zero_mark_is_kept_as_zero() -> None:
    """A worthless option really is 0.00; only *absence* becomes None."""
    leg = option_leg(SPY_580P)
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("0"))])

    await service(client).refresh([strategy(leg)], include_greeks=False)

    assert leg.mark == Decimal(0)
    assert leg.mark is not None


# ----------------------------------------------------------- dedupe and batch


async def test_the_same_option_in_two_strategies_is_requested_once() -> None:
    """One symbol, one slot in the batch -- and both legs written back."""
    first, second = option_leg(SPY_580P), option_leg(SPY_580P)
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await service(client).refresh(
        [strategy(first, sid="s-1"), strategy(second, sid="s-2")],
        include_greeks=False,
    )

    assert client.requested == [SPY_580P]
    assert first.mark == Decimal("8.00")
    assert second.mark == Decimal("8.00")


async def test_requests_chunk_at_the_documented_batch_limit() -> None:
    """150 symbols -> 2 calls, neither over the limit, and all 150 come back."""
    assert MARKET_DATA_BATCH_LIMIT == 100
    legs = [
        option_leg(build_occ_symbol("SPY", EXPIRY, "P", Decimal(strike)), strike=str(strike))
        for strike in range(400, 550)
    ]
    assert len(legs) == 150
    client = FakeClient(quotes=[FakeQuote(leg.symbol, mark=Decimal("1.25")) for leg in legs])

    await service(client).refresh([strategy(*legs)], include_greeks=False)

    assert len(client.calls) == 2
    sizes = [sum(len(v) for v in call.values()) for call in client.calls]
    assert sizes == [100, 50]
    assert all(size <= MARKET_DATA_BATCH_LIMIT for size in sizes)
    # The merged result covers every symbol: no leg left unpriced by chunking.
    assert len(client.requested) == 150
    assert set(client.requested) == {leg.symbol for leg in legs}
    assert all(leg.mark == Decimal("1.25") for leg in legs)


async def test_a_batch_boundary_does_not_drop_the_symbol_that_straddles_it() -> None:
    """Exactly one over the limit: the overflow symbol still gets its quote."""
    legs = [
        option_leg(build_occ_symbol("SPY", EXPIRY, "P", Decimal(strike)), strike=str(strike))
        for strike in range(400, 501)
    ]
    assert len(legs) == 101
    client = FakeClient(quotes=[FakeQuote(leg.symbol, mark=Decimal("2.00")) for leg in legs])

    await service(client).refresh([strategy(*legs)], include_greeks=False)

    assert len(client.calls) == 2
    assert legs[-1].mark == Decimal("2.00")
    assert all(leg.mark is not None for leg in legs)


# ------------------------------------------------------- symbol conversion


def test_occ_to_streamer_symbol_round_trips() -> None:
    """The conversion the greeks subscription depends on, both ways."""
    streamer = streamer_symbol_for(SPY_580P)

    assert streamer == ".SPY251219P580"
    assert Option.streamer_symbol_to_occ(streamer) == SPY_580P


def test_a_symbol_the_sdk_cannot_convert_yields_none() -> None:
    """One odd symbol must cost only its own greeks, never raise.

    The SDK answers an unparseable symbol with an empty string rather than an
    exception, which is just as unusable as a failure -- both must read None.
    """
    assert streamer_symbol_for("not an option at all") is None
    assert streamer_symbol_for("SPY251219P00580000") is None  # unpadded root


# ------------------------------------------------------- underlying quotes


async def test_iv_rank_keeps_the_scale_the_sdk_sent() -> None:
    """0.35 means IVR 35. Not 35, not 0.0035 -- no rescaling in either direction."""
    client = FakeClient(
        quotes=[FakeQuote("SPY", last=Decimal("585.10"), mark=Decimal("585.12"))],
        metrics=[
            FakeMetric(
                symbol="SPY",
                implied_volatility_index=Decimal("0.184"),
                implied_volatility_index_rank="0.35",
                implied_volatility_percentile="0.42",
            )
        ],
    )

    quotes = await service(client).underlying_quotes(["SPY"])

    spy = quotes["SPY"]
    assert spy.iv_rank == Decimal("0.35")
    assert spy.iv_rank != Decimal(35)
    assert spy.iv_rank != Decimal("0.0035")
    assert spy.iv == Decimal("0.184")
    assert spy.iv_percentile == Decimal("0.42")
    assert spy.last == Decimal("585.10")
    assert spy.mark == Decimal("585.12")


async def test_iv_rank_falls_back_to_the_tw_calculation_at_the_same_scale() -> None:
    client = FakeClient(
        metrics=[FakeMetric(symbol="SPY", tw_implied_volatility_index_rank="0.28")],
    )

    quotes = await service(client).underlying_quotes(["SPY"])

    assert quotes["SPY"].iv_rank == Decimal("0.28")


async def test_metrics_returned_as_a_mapping_are_not_dropped() -> None:
    """The real TastyClient hands back dict[symbol, metric], not a list.

    Iterating that dict yields bare strings, which have no ``.symbol`` -- so a
    naive loop silently loses IV rank, earnings and ex-dividend dates, and the
    risk module then sees a portfolio with no volatility context at all.
    """
    client = MappingClient(
        quotes=[FakeQuote("SPY", mark=Decimal("585.12"))],
        metrics=[
            FakeMetric(
                symbol="SPY",
                implied_volatility_index_rank="0.35",
                dividend_ex_date=date(2026, 9, 19),
                earnings=FakeEarnings(expected_report_date=date(2026, 10, 29)),
            )
        ],
    )

    quotes = await service(client).underlying_quotes(["SPY"])

    spy = quotes["SPY"]
    assert spy.mark == Decimal("585.12")  # market data as a mapping too
    assert spy.iv_rank == Decimal("0.35")
    assert spy.ex_dividend_date == date(2026, 9, 19)
    assert spy.earnings_date == date(2026, 10, 29)


async def test_an_unquoted_underlying_has_no_price_either() -> None:
    client = FakeClient(quotes=[], metrics=[])

    quotes = await service(client).underlying_quotes(["SPY"])

    assert quotes["SPY"].last is None
    assert quotes["SPY"].mark is None
    assert quotes["SPY"].iv_rank is None


async def test_underlyings_are_deduped_and_normalised_before_asking() -> None:
    client = FakeClient(quotes=[FakeQuote("SPY", mark=Decimal("585"))])

    quotes = await service(client).underlying_quotes(["SPY", "spy", " SPY ", ""])

    assert client.requested == ["SPY"]
    assert list(quotes) == ["SPY"]


async def test_an_index_guessed_as_an_equity_is_retried_in_the_other_bucket() -> None:
    """Cash-settled products answer under one instrument type only."""
    client = FakeClient(
        quotes=[FakeQuote("XND", mark=Decimal("201.50"))],
        only_bucket={"XND": "indices"},
    )

    quotes = await service(client).underlying_quotes(["XND"])

    assert client.calls[0]["equities"] == ["XND"]
    assert client.calls[1]["indices"] == ["XND"]
    assert quotes["XND"].mark == Decimal("201.50")


async def test_missing_metrics_do_not_cost_us_the_prices() -> None:
    """Metrics are context; marks are the product. One failing must not take both."""
    client = FakeClient(
        quotes=[FakeQuote("SPY", mark=Decimal("585.12"))],
        metrics_error=RuntimeError("metrics endpoint is down"),
    )

    quotes = await service(client).underlying_quotes(["SPY"])

    assert quotes["SPY"].mark == Decimal("585.12")
    assert quotes["SPY"].iv_rank is None


async def test_a_past_earnings_date_is_not_reported_as_upcoming() -> None:
    client = FakeClient(
        metrics=[
            FakeMetric(symbol="SPY", earnings=FakeEarnings(occurred_date=date(2020, 1, 30))),
        ]
    )

    quotes = await service(client).underlying_quotes(["SPY"])

    assert quotes["SPY"].earnings_date is None


# --------------------------------------------------------------------- greeks


async def test_greeks_merge_onto_the_legs_that_own_them() -> None:
    short, long_ = option_leg(SPY_580P), option_leg(SPY_575P, direction=Direction.LONG, strike="575")
    factory = FakeStreamerFactory(
        events=[
            FakeGreeks(".SPY251219P580", delta=-0.62, gamma=0.011, theta=-0.24, vega=0.33, volatility=0.35),
            FakeGreeks(".SPY251219P575", delta=-0.48),
        ]
    )
    client = FakeClient(
        quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00")), FakeQuote(SPY_575P, mark=Decimal("5.50"))]
    )

    await MarkService(client, streamer_factory=factory).refresh([strategy(short, long_)])

    assert short.delta == Decimal("-0.62")
    assert short.theta == Decimal("-0.24")
    # A float that went through str(), so 0.35 stays 0.35 with no binary noise.
    assert short.iv == Decimal("0.35")
    assert long_.delta == Decimal("-0.48")
    assert sorted(factory.made[0].subscribed[0]) == [".SPY251219P575", ".SPY251219P580"]


async def test_a_leg_that_never_received_greeks_keeps_delta_none() -> None:
    """Routine outside market hours: report nothing rather than a stale guess."""
    short, long_ = option_leg(SPY_580P), option_leg(SPY_575P, direction=Direction.LONG, strike="575")
    factory = FakeStreamerFactory(events=[FakeGreeks(".SPY251219P580", delta=-0.62)])
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await MarkService(client, streamer_factory=factory, greeks_timeout=0.2).refresh([strategy(short, long_)])

    assert short.delta == Decimal("-0.62")
    assert long_.delta is None
    assert long_.gamma is None and long_.theta is None and long_.vega is None and long_.iv is None


async def test_a_silent_stream_leaves_yesterdays_greeks_alone() -> None:
    """Greeks are merged, never cleared: an old delta beats no delta."""
    leg = option_leg(SPY_580P)
    leg.delta = Decimal("-0.55")
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await MarkService(client, streamer_factory=FakeStreamerFactory(events=[])).refresh([strategy(leg)])

    assert leg.delta == Decimal("-0.55")
    assert leg.mark == Decimal("8.00")


async def test_the_greeks_time_box_still_returns_the_partial_haul() -> None:
    short, long_ = option_leg(SPY_580P), option_leg(SPY_575P, direction=Direction.LONG, strike="575")
    factory = FakeStreamerFactory(events=[FakeGreeks(".SPY251219P580", delta=-0.62)], stall=True)
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await MarkService(client, streamer_factory=factory, greeks_timeout=0.05).refresh([strategy(short, long_)])

    assert short.delta == Decimal("-0.62")
    assert long_.delta is None


async def test_a_dead_stream_does_not_cost_us_the_marks() -> None:
    leg = option_leg(SPY_580P)
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await MarkService(client, streamer_factory=FakeStreamerFactory(fail=True)).refresh([strategy(leg)])

    assert leg.mark == Decimal("8.00")
    assert leg.delta is None


async def test_include_greeks_false_never_opens_a_stream() -> None:
    factory = FakeStreamerFactory(events=[FakeGreeks(".SPY251219P580", delta=-0.62)])
    client = FakeClient(quotes=[FakeQuote(SPY_580P, mark=Decimal("8.00"))])

    await MarkService(client, streamer_factory=factory).refresh(
        [strategy(option_leg(SPY_580P))], include_greeks=False
    )

    assert factory.made == []


async def test_equity_only_portfolios_do_not_open_a_stream() -> None:
    """Shares have no greeks; subscribing to nothing is pure latency."""
    factory = FakeStreamerFactory()
    client = FakeClient(quotes=[FakeQuote("SPY", mark=Decimal("585"))])

    await MarkService(client, streamer_factory=factory).refresh([strategy(equity_leg("SPY"))])

    assert factory.made == []


async def test_stream_greeks_hands_back_the_strategy_not_the_leg() -> None:
    """Risk is recomputed per strategy; a single leg's numbers mean nothing."""
    leg = option_leg(SPY_580P)
    owner = strategy(leg)
    factory = FakeStreamerFactory(events=[FakeGreeks(".SPY251219P580", delta=-0.62)])
    seen: list[Strategy] = []

    await MarkService(FakeClient(), streamer_factory=factory).stream_greeks([owner], lambda s: seen.append(s))

    assert seen == [owner]
    assert leg.delta == Decimal("-0.62")


async def test_stream_greeks_awaits_an_async_callback() -> None:
    leg = option_leg(SPY_580P)
    owner = strategy(leg)
    factory = FakeStreamerFactory(events=[FakeGreeks(".SPY251219P580", delta=-0.62)])
    seen: list[Strategy] = []

    async def on_update(changed: Strategy) -> None:
        seen.append(changed)

    await MarkService(FakeClient(), streamer_factory=factory).stream_greeks([owner], on_update)

    assert seen == [owner]


async def test_stream_greeks_notifies_each_owner_of_a_shared_option_once() -> None:
    first, second = option_leg(SPY_580P), option_leg(SPY_580P)
    one, two = strategy(first, sid="s-1"), strategy(second, sid="s-2")
    factory = FakeStreamerFactory(events=[FakeGreeks(".SPY251219P580", delta=-0.62)])
    seen: list[str] = []

    await MarkService(FakeClient(), streamer_factory=factory).stream_greeks(
        [one, two], lambda s: seen.append(s.id)
    )

    assert seen == ["s-1", "s-2"]
    assert first.delta == second.delta == Decimal("-0.62")


# --------------------------------------------------------------- client glue


async def test_a_client_with_no_session_says_so_plainly() -> None:
    """Better a named error than a refresh that quietly prices nothing."""

    class Sessionless:
        pass

    with pytest.raises(RuntimeError, match="no tastytrade session"):
        await MarkService(Sessionless()).underlying_quotes(["SPY"])  # type: ignore[arg-type]


def test_the_mark_is_the_brokers_mid_not_its_mark_field() -> None:
    """Checked against the platform: mid is what tastytrade prints.

    A soybean option quoted 23.50 bid, 36.00 ask came back with mid 29.75 and
    a "mark" of 35.5625. The positions endpoint -- which is the number on the
    user's own screen -- said 29.75. Across nineteen open positions mid agreed
    with the platform on all of them and "mark" on eighteen, and the one it
    missed was worth $580 on a 50x contract. A wide market is exactly where a
    mark matters and exactly where that field goes its own way.
    """
    from tastydesk.core.marks import MarkService

    class Quote:
        bid = Decimal("23.5")
        ask = Decimal("36.0")
        mid = Decimal("29.75")
        mark = Decimal("35.562499996")
        # Trading around the mid, which is why the platform agreed with it.
        last = Decimal("29.5")

    leg = Leg(
        symbol="./ZSF7 OZSF7 261224C1380",
        instrument_type="Future Option",
        underlying="/ZSF7",
        direction=Direction.SHORT,
        quantity=Decimal(2),
        multiplier=Decimal(50),
    )
    MarkService._apply_quote(leg, Quote())
    assert leg.mark == Decimal("29.75")


def test_after_the_close_a_collapsed_bid_does_not_set_the_mark() -> None:
    """The same contract, the other way round.

    After the close the January call was quoted 6.00 bid against 29.00 offered:
    mid 17.50, mark 28.56, last trade 28.50, settlement 28.25. The mid put
    $1,100 of profit on a leg the platform showed up $231. When the broker's two
    figures disagree, the one consistent with where the contract traded wins.
    """
    from tastydesk.core.marks import MarkService

    class Quote:
        bid = Decimal("6.0")
        ask = Decimal("29.0")
        mid = Decimal("17.5")
        mark = Decimal("28.562500111")
        last = Decimal("28.5")
        close = Decimal("28.25")

    leg = Leg(
        symbol="./ZSF7 OZSF7 261224C1380",
        instrument_type="Future Option",
        underlying="/ZSF7",
        direction=Direction.SHORT,
        quantity=Decimal(2),
        multiplier=Decimal(50),
    )
    MarkService._apply_quote(leg, Quote())
    assert leg.mark == Decimal("28.562500111")


def test_when_mid_and_mark_agree_the_mid_is_used() -> None:
    from tastydesk.core.marks import MarkService

    class Quote:
        bid = Decimal("4.9")
        ask = Decimal("5.1")
        mid = Decimal("5.0")
        mark = Decimal("5.01")
        last = Decimal("3.0")  # irrelevant: the two agree

    leg = Leg(
        symbol="SPY",
        instrument_type="Equity Option",
        underlying="SPY",
        direction=Direction.SHORT,
        quantity=Decimal(1),
    )
    MarkService._apply_quote(leg, Quote())
    assert leg.mark == Decimal("5.0")


# --------------------------------------------------------- thin markets


ZS_PUT = "./ZSX6 OZSX6 261023P1240"


def zs_put(**kwargs: Any) -> Leg:
    leg = option_leg(
        ZS_PUT,
        strike="1240",
        open_price="4.00",
        quantity="2",
        instrument_type="Future Option",
        underlying="/ZS",
    )
    leg.multiplier = Decimal(50)
    leg.expiration = date.today() + timedelta(days=25)
    for name, value in kwargs.items():
        setattr(leg, name, value)
    return leg


def test_a_tight_market_is_not_wide() -> None:
    assert not is_wide(zs_put(bid=Decimal("6.375"), ask=Decimal("6.875")))


def test_a_wide_or_one_sided_market_is_wide() -> None:
    assert is_wide(zs_put(bid=Decimal("23.50"), ask=Decimal("36.00")))
    assert is_wide(zs_put(bid=Decimal("0"), ask=Decimal("0.10")))


def test_a_quote_without_bid_and_ask_is_not_judged() -> None:
    assert not is_wide(zs_put(bid=None, ask=None))


def test_the_estimate_stays_inside_the_market() -> None:
    """The model decides where inside the bid and ask, never outside it."""
    leg = zs_put(bid=Decimal("6.90"), ask=Decimal("9.00"), iv=Decimal("0.195"))
    assert estimate_mark(leg, Decimal("1299.25"), date.today()) == Decimal("6.90")


def test_nothing_to_model_from_means_no_estimate() -> None:
    leg = zs_put(bid=Decimal("3"), ask=Decimal("7"), iv=None)
    assert estimate_mark(leg, Decimal("1299.25"), date.today()) is None


def test_a_stock_option_is_never_modelled() -> None:
    leg = option_leg()
    leg.bid, leg.ask, leg.iv = Decimal("3.80"), Decimal("4.70"), Decimal("0.40")
    leg.expiration = date.today() + timedelta(days=100)
    assert estimate_mark(leg, Decimal("600"), date.today()) is None


async def test_a_thin_option_is_priced_from_its_underlying_and_flagged() -> None:
    """The /ZS put: a 3 x 7 market, a midpoint of 5, and a model price near 6.35."""
    leg = zs_put()
    client = FakeClient(
        quotes=[
            FakeQuote(ZS_PUT, mark=Decimal("5"), mid=Decimal("5"), bid=Decimal("3"), ask=Decimal("7"), iv=Decimal("0.195191")),
            FakeQuote("/ZSX6", mid=Decimal("1299.25"), mark=Decimal("1299.25")),
        ]
    )

    await service(client).refresh([strategy(leg, underlying="/ZSX6")], include_greeks=False)

    assert leg.mark_estimated
    assert Decimal("6.3") < leg.mark < Decimal("6.4")
    assert "/ZSX6" in client.requested


async def test_a_liquid_book_costs_no_extra_call_and_carries_no_flag() -> None:
    leg = zs_put()
    client = FakeClient(
        quotes=[FakeQuote(ZS_PUT, mark=Decimal("6.625"), mid=Decimal("6.625"), bid=Decimal("6.375"), ask=Decimal("6.875"), iv=Decimal("0.195"))]
    )

    await service(client).refresh([strategy(leg, underlying="/ZSX6")], include_greeks=False)

    assert not leg.mark_estimated
    assert leg.mark == Decimal("6.625")
    assert client.requested == [ZS_PUT]


async def test_a_thin_option_with_no_underlying_price_keeps_the_brokers_figure() -> None:
    leg = zs_put()
    client = FakeClient(
        quotes=[FakeQuote(ZS_PUT, mark=Decimal("5"), mid=Decimal("5"), bid=Decimal("3"), ask=Decimal("7"), iv=Decimal("0.195"))]
    )

    await service(client).refresh([strategy(leg, underlying="/ZSX6")], include_greeks=False)

    assert not leg.mark_estimated
    assert leg.mark == Decimal("5")
