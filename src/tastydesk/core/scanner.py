"""Tom's scanner: the market read through the entry rules of his 2026 plan.

Each setup in Tom King's *2026 Trading Plan v4* has an entry checklist — the
chart (regime, trend, pullback, RSI, MACD, Bollinger band), the volatility (IV
rank), the calendar (earnings, how long since the last one), and then a recipe:
which expiry, which delta, how wide, where the stop and the target sit. This
module runs those checklists over a fixed list of products, and for the ones
that pass it writes the recipe out with real strikes, sized to this account and
checked against the book it would join.

What it is and is not
---------------------
It is a filter with Tom's rules in it. A candidate here means "this product
passes the checks his plan writes down today", nothing more: the plan's own
fundamentals test (rising revenue and earnings) is reduced to a market-cap
floor, the weekly trend is read off the daily chart's 21 and 50 EMAs, and the
market is a fixed list rather than everything listed. All of that is said on
the page. The decision stays with the person reading it.

Pure functions only. The service fetches the charts, metrics, chains and
quotes and hands them in; everything here is arithmetic on what it is given.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from tastydesk.core.models import ZERO, OptionType
from tastydesk.core.prices import Bar
from tastydesk.core.scenario import bs_delta
from tastydesk.core.tom import PLAN, PMCC_STOP, Regime, ema, regime_series, sma

__all__ = [
    "Candidate",
    "OptionQuote",
    "ScanResult",
    "UNIVERSE",
    "pick_expiration",
    "pick_strike",
    "screen",
    "technicals",
]

_ONE = Decimal(1)

# --------------------------------------------------------------------------
# The market Tom trades
# --------------------------------------------------------------------------

# Tom's own tickers, setup by setup, and where he names them. The scanner looks
# at nothing else: a setup on a product Tom has not named is not his trade.
TOM_TICKERS: dict[str, tuple[tuple[str, ...], str]] = {
    "11x": (
        ("SPX", "/ES", "SPY", "/MES"),
        "Plan §9.1 and the 11x campaign sheet: /ES, /MES, SPY or SPX, by account size",
    ),
    "es_120": (("/ES", "/MES"), "Plan §10.5: /ES, or /MES when one /ES lot is over his cap"),
    "spx_pcs": (("SPX",), "Plan §10.6: SPX"),
    "naked_put": (("SPY", "NVDA", "PLTR", "MSTR"), "His videos: mainly SPY; PLTR, NVDA and MSTR"),
    "pmcc": (("SPY", "QQQ", "GLD", "AMZN"), "His videos: SPY, QQQ and GLD first; the AMZN example"),
    "strangle": (
        ("/ES", "/CL", "/GC", "/ZB", "/6E", "/6A", "/6J", "/ZC", "/ZS", "/ZW"),
        "His videos: the S&P, oil, gold, bonds, currencies and grains",
    ),
}

# Broad funds pass the quality test by being broad.
ETFS = frozenset({"SPY", "QQQ", "GLD"})

# The 11x's put spread on each of Tom's instruments, in points: 50 on the S&P
# futures and the index, 5 on SPY (a tenth of the index).
ELEVEN_X_WIDTH: dict[str, Decimal] = {
    "SPX": Decimal(50),
    "/ES": Decimal(50),
    "SPY": Decimal(5),
    "/MES": Decimal(50),
}

# One point of the future, in dollars — what an option's price is multiplied by.
MULTIPLIER: dict[str, Decimal] = {
    "/ES": Decimal(50),
    "/MES": Decimal(5),
    "/NQ": Decimal(20),
    "/MNQ": Decimal(2),
    "/RTY": Decimal(50),
    "/M2K": Decimal(5),
    "/CL": Decimal(1000),
    "/MCL": Decimal(100),
    "/GC": Decimal(100),
    "/MGC": Decimal(10),
    "/SI": Decimal(5000),
    "/SIL": Decimal(1000),
    "/ZB": Decimal(1000),
    "/ZN": Decimal(1000),
    "/6E": Decimal(125000),
    "/6A": Decimal(100000),
    "/6J": Decimal(12500000),
    "/ZC": Decimal(50),
    "/ZS": Decimal(50),
    "/ZW": Decimal(50),
    "/NG": Decimal(10000),
    "/HG": Decimal(25000),
}

# Futures that move together. Tom spreads strangles across products that do
# not (§10.1, "futures that don't move together"), so a second position in a
# group counts as the same bet twice.
GROUPS: dict[str, str] = {
    "/ES": "US stocks", "/MES": "US stocks", "/NQ": "US stocks", "/MNQ": "US stocks", "/RTY": "US stocks",
    "/M2K": "US stocks", "/YM": "US stocks", "SPY": "US stocks", "SPX": "US stocks", "QQQ": "US stocks",
    "IWM": "US stocks", "/ZB": "Rates", "/ZN": "Rates", "/ZF": "Rates", "/UB": "Rates", "TLT": "Rates",
    "/CL": "Energy", "/MCL": "Energy", "/NG": "Natural gas", "/GC": "Metals", "/MGC": "Metals",
    "/SI": "Metals", "/SIL": "Metals", "/HG": "Copper", "/ZC": "Grains", "/ZS": "Grains", "/ZW": "Grains",
    "/6E": "Currencies", "/6A": "Currencies", "/6J": "Currencies", "/6B": "Currencies", "/6C": "Currencies",
}  # fmt: skip

# The smaller contract on the same thing, for when one full-size lot is more
# than Tom's cap allows at this account's size.
MICRO = {"/ES": "/MES", "/NQ": "/MNQ", "/RTY": "/M2K", "/CL": "/MCL", "/GC": "/MGC", "/SI": "/SIL"}

# Every product the scanner charts: Tom's tickers, plus SPY for the market.
UNIVERSE = tuple(dict.fromkeys(["SPY", *(s for symbols, _ in TOM_TICKERS.values() for s in symbols)]))


def option_multiplier(symbol: str) -> Decimal:
    """Dollars per point of an option on this product."""
    return MULTIPLIER.get(symbol, Decimal(100))


_QUALITY_CAP = Decimal(10_000_000_000)  # §10.3 videos: names over $10B


# --------------------------------------------------------------------------
# The chart
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Technicals:
    symbol: str
    as_of: date
    price: float
    ema21: float
    ema50: float | None
    sma200: float | None
    rsi: float
    rsi_before: float | None  # three sessions ago
    macd_hist: float | None
    macd_hist_before: float | None
    bb_low: float | None
    bb_mid: float | None
    bb_up: float | None
    regime: Regime

    @property
    def rsi_rising(self) -> bool:
        return self.rsi_before is not None and self.rsi > self.rsi_before

    @property
    def macd_rising(self) -> bool:
        return (
            self.macd_hist is not None
            and self.macd_hist_before is not None
            and self.macd_hist > self.macd_hist_before
        )

    @property
    def uptrend(self) -> bool:
        return self.ema50 is not None and self.ema21 > self.ema50

    @property
    def near_top(self) -> bool:
        if self.bb_up is None or self.bb_mid is None:
            return False
        return self.price >= self.bb_mid + 0.75 * (self.bb_up - self.bb_mid)

    @property
    def pullback(self) -> bool:
        """Back to the 21 EMA, between the 21 and 50, or down at the lower band."""
        if self.price <= self.ema21 * 1.01:
            return True
        return self.bb_low is not None and self.price <= self.bb_low * 1.02

    @property
    def calm(self) -> bool:
        """Sideways rather than running: Tom's regime reads neutral, or the RSI is
        between 40 and 60 with price hugging the 21 EMA and the 21 flat on the 50.

        Distances alone would not do: a 2% move is nothing in oil and a rout in
        bonds, so the RSI is what keeps a sliding chart from reading as calm.
        """
        if self.regime.label == "Neutral":
            return True
        if self.ema50 is None or not 40 <= self.rsi <= 60:
            return False
        return abs(self.price / self.ema21 - 1) < 0.015 and abs(self.ema21 / self.ema50 - 1) < 0.015


def technicals(symbol: str, bars: Sequence[Bar]) -> Technicals | None:
    """Everything Tom's checklists read off a daily chart. None under 60 bars."""
    if len(bars) < 60:
        return None
    series = regime_series(symbol, bars)
    regime = series.latest()
    if regime is None:
        return None
    closes = [b.close for b in bars]
    fast, slow = ema(closes, 12), ema(closes, 26)
    macd = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow, strict=True)]
    valid = [m for m in macd if m is not None]
    signal = ema(valid, 9)
    hist = [m - s for m, s in zip(valid, signal, strict=True) if s is not None]
    sma200 = sma(closes, 200)[-1]
    rsis = series.rsi
    return Technicals(
        symbol=symbol,
        as_of=bars[-1].day,
        price=closes[-1],
        ema21=regime.ema21,
        ema50=regime.ema50,
        sma200=sma200,
        rsi=regime.rsi,
        rsi_before=rsis[-4] if len(rsis) >= 4 else None,
        macd_hist=hist[-1] if hist else None,
        macd_hist_before=hist[-2] if len(hist) >= 2 else None,
        bb_low=(2 * series.bb_mid[-1] - series.bb_up[-1])
        if series.bb_mid[-1] is not None and series.bb_up[-1] is not None
        else None,
        bb_mid=series.bb_mid[-1],
        bb_up=series.bb_up[-1],
        regime=regime,
    )


# --------------------------------------------------------------------------
# What a setup needs from the option chain
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegSpec:
    """One leg to find: by delta, or a fixed distance below another leg."""

    action: str  # Sell | Buy
    right: OptionType
    quantity: int
    delta: float | None = None  # absolute, e.g. 0.08
    below: tuple[int, Decimal] | None = None  # (index of the leg, points under it)
    expiry: str = "near"  # near | far: which of the setup's two expiries


@dataclass(frozen=True, slots=True)
class Order:
    """The recipe: which expiries and which legs, from Tom's plan."""

    dte: int
    window: tuple[int, int]
    legs: tuple[LegSpec, ...]
    far_dte: int | None = None
    far_window: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class OptionQuote:
    symbol: str
    right: OptionType
    strike: Decimal
    expiry: date
    dte: int
    mark: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    delta: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    estimated: bool = False


def pick_expiration(
    expirations: Sequence[tuple[date, int, str]], target: int, window: tuple[int, int]
) -> tuple[date, int] | None:
    """The expiry nearest ``target`` days inside ``window``; monthlies win ties."""
    inside = [(d, dte, kind) for d, dte, kind in expirations if window[0] <= dte <= window[1]]
    if not inside:
        return None
    best = min(inside, key=lambda e: (abs(e[1] - target), e[2].lower() != "regular"))
    return best[0], best[1]


def pick_strike(quotes: Sequence[OptionQuote], right: OptionType, target: float) -> OptionQuote | None:
    """The quoted option whose delta is nearest the target, on the right side."""
    usable = [
        q for q in quotes if q.right is right and q.delta is not None and q.mark is not None and q.mark > ZERO
    ]
    if not usable:
        return None
    return min(usable, key=lambda q: abs(abs(float(q.delta or 0)) - target))


def candidate_strikes(
    strikes: Sequence[Decimal],
    right: OptionType,
    spot: Decimal,
    years: Decimal,
    iv: Decimal,
    target: float,
    count: int = 12,
) -> list[Decimal]:
    """The listed strikes a flat-volatility model puts nearest the target delta.

    Only a shortlist to quote: the real delta comes back with the quote and is
    what the strike is finally chosen on. Skew moves the true delta of an
    out-of-the-money put up, so the shortlist leans a little further out.
    """
    # Only an out-of-the-money put carries the skew worth leaning for.
    aim = target * 0.85 if right is OptionType.PUT and target < 0.5 else target
    scored = []
    for k in strikes:
        d = abs(float(bs_delta(right, spot, k, years, iv)))
        scored.append((abs(d - aim), k))
    scored.sort()
    return sorted(k for _, k in scored[:count])


# --------------------------------------------------------------------------
# The setups
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    label: str
    ok: bool | None
    detail: str
    hard: bool = False


@dataclass(frozen=True, slots=True)
class Setup:
    key: str
    name: str
    playbook: str  # the Tom playbook key it lands in once open (tom.playbook_for)
    tier: str
    source: str
    stop_multiple: Decimal | None  # buy back at this multiple of the credit
    profit_target: Decimal | None
    loss_cap: Decimal  # share of net liq the loss at the stop may reach


NAKED_PUT = Setup(
    "naked_put", "Naked put", "naked_put", "spec", "Plan §10.3", Decimal(3), Decimal("0.50"), PLAN.max_loss
)
STRANGLE = Setup(
    "strangle",
    "60-DTE strangle",
    "strangle",
    "spec",
    "Plan §10.1",
    Decimal("2.5"),
    Decimal("0.50"),
    Decimal("0.015"),
)
ELEVEN_X = Setup(
    "11x", "11x Bear Trap (112)", "11x", "core", "Plan §9.1", None, Decimal("0.90"), PLAN.max_loss
)
ES_PUT = Setup(
    "es_120", "120-DTE /MES put", "es_120", "spec", "Plan §10.5", Decimal(4), Decimal("0.40"), PLAN.max_loss
)
SPX_PCS = Setup(
    "spx_pcs",
    "5-DTE SPX put spread",
    "put_spread",
    "spec",
    "Plan §10.6",
    Decimal("2.5"),
    Decimal("0.50"),
    PLAN.max_loss,
)
PMCC = Setup("pmcc", "Dynamic PMCC", "pmcc", "core", "Plan §9.2", None, None, PLAN.max_loss)

SETUPS = {s.key: s for s in (NAKED_PUT, STRANGLE, ELEVEN_X, ES_PUT, SPX_PCS, PMCC)}


@dataclass(frozen=True, slots=True)
class Metric:
    """What the scanner needs from tastytrade's market metrics for one product."""

    iv_rank: Decimal | None = None
    iv: Decimal | None = None
    iv_minus_hv: Decimal | None = None
    market_cap: Decimal | None = None
    earnings: date | None = None
    beta: Decimal | None = None
    liquidity: int | None = None


@dataclass(slots=True)
class Candidate:
    symbol: str
    setup: str
    setup_name: str
    tier: str
    source: str
    status: str  # ready | almost | watch
    headline: str
    price: float
    rsi: float
    regime: str
    iv_rank: Decimal | None
    earnings: date | None
    checks: list[Check]
    order: Order | None = None
    plan: Plan | None = None
    fit: Fit | None = None
    score: float = 0.0
    # Smaller contracts on the same idea, tried in order when this one is too
    # big for Tom's caps at this account's size, with the recipe for each.
    ladder: list[str] = field(default_factory=list)
    orders: dict[str, Order] = field(default_factory=dict)
    tom_list: str = ""
    alternatives: list[str] = field(default_factory=list)


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _rank_text(m: Metric) -> str:
    return "no IV rank" if m.iv_rank is None else f"IV rank {float(m.iv_rank) * 100:.0f}"


def _earnings_check(m: Metric, today: date, days: int) -> Check:
    if m.earnings is None:
        return Check("No earnings before expiry", True, "no report date published", hard=True)
    gap = (m.earnings - today).days
    clear = gap < 0 or gap > days
    return Check(
        "No earnings before expiry",
        clear,
        f"reports {m.earnings:%b %d}" + ("" if clear else f", inside a {days}-day trade"),
        hard=True,
    )


def _status(checks: Sequence[Check]) -> tuple[str, float]:
    hard = [c for c in checks if c.hard]
    soft = [c for c in checks if not c.hard and c.ok is not None]
    if any(c.ok is False for c in hard):
        return "watch", 0.0
    passed = sum(1 for c in soft if c.ok)
    score = passed / len(soft) if soft else 1.0
    misses = len(soft) - passed
    return ("ready" if misses == 0 else "almost" if misses <= 1 else "watch"), score


def _naked_put(t: Technicals, m: Metric, market: Regime | None, today: date, etf: bool) -> Candidate:
    quality = etf or (m.market_cap is not None and m.market_cap >= _QUALITY_CAP)
    cap_text = (
        "broad ETF"
        if etf
        else ("market cap unknown" if m.market_cap is None else f"${m.market_cap / Decimal(1e9):,.0f}B")
    )
    checks = [
        Check("Quality name", quality, cap_text, hard=True),
        Check("Trending up", t.uptrend, f"21 EMA {'above' if t.uptrend else 'below'} the 50", hard=True),
        Check(
            "Not stretched",
            not t.near_top,
            "near the top of the Bollinger band"
            if t.near_top
            else f"{(t.price / t.ema21 - 1) * 100:+.1f}% from the 21 EMA",
            hard=True,
        ),
        _earnings_check(m, today, 45),
        Check("Pulled back", t.pullback, "at the 21 EMA or lower band" if t.pullback else "above the 21 EMA"),
        Check(
            "RSI under 50 and rising",
            t.rsi < 50 and t.rsi_rising,
            f"RSI {t.rsi:.0f}" + (", rising" if t.rsi_rising else ", falling"),
        ),
        Check("MACD turning up", t.macd_rising, "histogram rising" if t.macd_rising else "histogram falling"),
        Check("IV rank over 30", None if m.iv_rank is None else m.iv_rank >= Decimal("0.30"), _rank_text(m)),
        Check(
            "Market not bearish",
            None if market is None else market.label != "Bearish",
            "no SPY chart" if market is None else f"SPY {market.label.lower()}",
        ),
    ]
    delta = 0.20 if t.price < t.ema21 else 0.13
    status, score = _status(checks)
    return Candidate(
        symbol=t.symbol,
        setup=NAKED_PUT.key,
        setup_name=NAKED_PUT.name,
        tier=NAKED_PUT.tier,
        source=NAKED_PUT.source,
        status=status,
        headline=(
            f"Pulled back in an uptrend: Tom sells a {delta:.2f}-delta put about 45 days out."
            if t.pullback
            else f"Uptrend, not stretched: Tom sells a {delta:.2f}-delta put about 45 days out."
        ),
        price=t.price,
        rsi=t.rsi,
        regime=t.regime.label,
        iv_rank=m.iv_rank,
        earnings=m.earnings,
        checks=checks,
        order=Order(45, (30, 60), (LegSpec("Sell", OptionType.PUT, 1, delta=delta),)),
        score=score,
    )


def _pmcc(t: Technicals, m: Metric, market: Regime | None, today: date, etf: bool) -> Candidate:
    quality = etf or (m.market_cap is not None and m.market_cap >= _QUALITY_CAP)
    checks = [
        Check(
            "Quality name",
            quality,
            "broad ETF" if etf else "market cap over $10B" if quality else "under $10B or unknown",
            hard=True,
        ),
        Check("Trending up", t.uptrend, f"21 EMA {'above' if t.uptrend else 'below'} the 50", hard=True),
        Check(
            "RSI under 50 and rising",
            t.rsi < 50 and t.rsi_rising,
            f"RSI {t.rsi:.0f}" + (", rising" if t.rsi_rising else ", falling"),
        ),
        Check(
            "Entering on a pullback",
            t.pullback,
            "at the 21 EMA or lower band" if t.pullback else "above the 21 EMA",
        ),
        _earnings_check(m, today, 14)
        if m.earnings
        else Check("No earnings before the short call", True, "no report date published"),
    ]
    # The short call's delta follows the trend (§9.2 table).
    if t.regime.label == "Bullish":
        short, where = 0.35, "out of the money (strong uptrend)"
    elif t.regime.label == "Neutral":
        short, where = 0.50, "at the money (sideways)"
    else:
        short, where = 0.65, "in the money (pulling back)"
    status, score = _status(checks)
    return Candidate(
        symbol=t.symbol,
        setup=PMCC.key,
        setup_name=PMCC.name,
        tier=PMCC.tier,
        source=PMCC.source,
        status=status,
        headline=f"80-delta LEAP with a {short:.2f}-delta call sold against it, {where}.",
        price=t.price,
        rsi=t.rsi,
        regime=t.regime.label,
        iv_rank=m.iv_rank,
        earnings=m.earnings,
        checks=checks,
        order=Order(
            14,
            (7, 30),
            (
                LegSpec("Buy", OptionType.CALL, 1, delta=0.80, expiry="far"),
                LegSpec("Sell", OptionType.CALL, 1, delta=short),
            ),
            far_dte=270,
            far_window=(180, 400),
        ),
        score=score,
    )


def _strangle(t: Technicals, m: Metric, held: set[str]) -> Candidate:
    group = GROUPS.get(t.symbol, t.symbol)
    overlap = sorted(p for p in held if GROUPS.get(p, p) == group)
    checks = [
        Check(
            "Nothing on that moves with it",
            not overlap,
            f"you hold {', '.join(overlap)} ({group.lower()})" if overlap else f"no {group.lower()} on yet",
            hard=True,
        ),
        Check(
            "Calm, sideways chart",
            t.calm,
            f"{t.regime.label.lower()}, RSI {t.rsi:.0f}, "
            f"{(t.price / t.ema21 - 1) * 100:+.1f}% from the 21 EMA",
        ),
        Check("IV rank over 25", None if m.iv_rank is None else m.iv_rank >= Decimal("0.25"), _rank_text(m)),
        Check(
            "Implied over realized",
            None if m.iv_minus_hv is None else m.iv_minus_hv > 0,
            "no IV/HV" if m.iv_minus_hv is None else f"IV − HV {float(m.iv_minus_hv):+.1f} pts",
        ),
    ]
    status, score = _status(checks)
    return Candidate(
        symbol=t.symbol,
        setup=STRANGLE.key,
        setup_name=STRANGLE.name,
        tier=STRANGLE.tier,
        source=STRANGLE.source,
        status=status,
        headline=(
            "Tom's spec strangle: 60 days, an 8-delta put and a 9-delta call, credit up to 1% of net liq."
        ),
        price=t.price,
        rsi=t.rsi,
        regime=t.regime.label,
        iv_rank=m.iv_rank,
        earnings=None,
        checks=checks,
        order=Order(
            60,
            (45, 75),
            (LegSpec("Sell", OptionType.PUT, 1, delta=0.08), LegSpec("Sell", OptionType.CALL, 1, delta=0.09)),
        ),
        score=score,
    )


def _eleven_x_order(symbol: str, long_delta: float) -> Order:
    return Order(
        57,
        (45, 65),
        (
            LegSpec("Buy", OptionType.PUT, 1, delta=long_delta),
            LegSpec("Sell", OptionType.PUT, 1, below=(0, ELEVEN_X_WIDTH[symbol])),
            LegSpec("Sell", OptionType.PUT, 2, delta=0.05),
        ),
    )


def _eleven_x(
    charts: Mapping[str, Technicals],
    market: Regime | None,
    last: date | None,
    today: date,
    net_liq: Decimal | None,
) -> Candidate | None:
    """The core campaign trade, on the biggest of Tom's instruments this account
    can carry: one lot's trap — the spread's width times its multiplier, which
    is the least it can lose at his stop — must fit inside 2% of net liq."""
    ladder = list(TOM_TICKERS["11x"][0])
    cap = PLAN.max_loss * net_liq if net_liq else None
    fits = [s for s in ladder if cap is None or ELEVEN_X_WIDTH[s] * option_multiplier(s) <= cap]
    too_big = [s for s in ladder if s not in fits]
    chosen = fits or [ladder[-1]]
    symbol = chosen[0]
    t = charts.get(symbol) or charts.get("SPY") or charts.get("/ES")
    if t is None:
        return None
    label = market.label if market else t.regime.label
    atm = label != "Bullish"
    since = None if last is None else (today - last).days
    checks = [
        Check(
            "Due in the campaign",
            since is None or since >= 14,
            "no 11x on yet" if since is None else f"last one {since} days ago; Tom places one every 2 weeks",
        ),
        Check(
            "Spread placed for the regime",
            True,
            "ATM spread: the trap is the goal" if atm else "OTM spread: the tail is the goal",
        ),
    ]
    status, score = _status(checks)
    long_delta = 0.45 if atm else 0.25
    width = ELEVEN_X_WIDTH[symbol]
    alternatives = []
    if too_big:
        alternatives.append(
            "Too big at your size: "
            + ", ".join(f"{s} (a ${ELEVEN_X_WIDTH[s] * option_multiplier(s):,.0f} trap)" for s in too_big)
            + f" against Tom's 2% (${cap:,.0f})."
        )
    return Candidate(
        symbol=symbol,
        setup=ELEVEN_X.key,
        setup_name=ELEVEN_X.name,
        tier=ELEVEN_X.tier,
        source=ELEVEN_X.source,
        status=status,
        headline=(
            f"Tom's core campaign trade. Market {label.lower()}: "
            + ("buy an at-the-money put spread" if atm else "buy an out-of-the-money put spread")
            + f", {width:g} points wide, and pay for it with two 5-delta puts, 50–60 days out."
        ),
        price=t.price,
        rsi=t.rsi,
        regime=t.regime.label,
        iv_rank=None,
        earnings=None,
        checks=checks,
        order=_eleven_x_order(symbol, long_delta),
        score=score,
        ladder=chosen[1:],
        orders={s: _eleven_x_order(s, long_delta) for s in chosen},
        tom_list=TOM_TICKERS["11x"][1],
        alternatives=alternatives,
    )


def _es_put(t: Technicals, market: Regime | None, last: date | None, today: date) -> Candidate:
    since = None if last is None else (today - last).days
    checks = [
        Check(
            "Due this month",
            since is None or since >= 28,
            "none on yet" if since is None else f"last one {since} days ago; Tom sells one a month",
        ),
        Check(
            "Market not bearish",
            None if market is None else market.label != "Bearish",
            "no SPY chart" if market is None else f"SPY {market.label.lower()}",
        ),
    ]
    status, score = _status(checks)
    order = Order(120, (100, 140), (LegSpec("Sell", OptionType.PUT, 1, delta=0.06),))
    return Candidate(
        symbol="/ES",
        setup=ES_PUT.key,
        setup_name=ES_PUT.name,
        tier=ES_PUT.tier,
        source=ES_PUT.source,
        status=status,
        headline=(
            "One 6-delta put on the S&P futures about 120 days out, each month; "
            "take 40%, stop at 4× the credit."
        ),
        price=t.price,
        rsi=t.rsi,
        regime=t.regime.label,
        iv_rank=None,
        earnings=None,
        checks=checks,
        order=order,
        score=score,
        ladder=["/MES"],
        orders={"/ES": order, "/MES": order},
        tom_list=TOM_TICKERS["es_120"][1],
    )


def _spx_pcs(t: Technicals, today: date) -> Candidate:
    monday = today.weekday() == 0
    checks = [
        Check("RSI between 40 and 60", 40 <= t.rsi <= 60, f"RSI {t.rsi:.0f}", hard=True),
        Check("Entered on a Monday", monday, "today is Monday" if monday else "Tom enters these on Mondays"),
    ]
    status, score = _status(checks)
    return Candidate(
        symbol="SPX",
        setup=SPX_PCS.key,
        setup_name=SPX_PCS.name,
        tier=SPX_PCS.tier,
        source=SPX_PCS.source,
        status=status,
        headline="A 9-delta SPX put, 20 points wide, sold for the week; take 50%, stop at 2.5×.",
        price=t.price,
        rsi=t.rsi,
        regime=t.regime.label,
        iv_rank=None,
        earnings=None,
        checks=checks,
        order=Order(
            5,
            (3, 7),
            (
                LegSpec("Sell", OptionType.PUT, 1, delta=0.09),
                LegSpec("Buy", OptionType.PUT, 1, below=(0, Decimal(20))),
            ),
        ),
        score=score,
        tom_list=TOM_TICKERS["spx_pcs"][1],
    )


def screen(
    charts: Mapping[str, Technicals],
    metrics: Mapping[str, Metric],
    *,
    held: set[str],
    last_opened: Mapping[str, date],
    today: date,
    net_liq: Decimal | None = None,
) -> list[Candidate]:
    """Every one of Tom's setups on the tickers he names for it, checked."""
    market = charts["SPY"].regime if "SPY" in charts else None
    out: list[Candidate] = []
    for symbol in TOM_TICKERS["naked_put"][0]:
        if (t := charts.get(symbol)) is not None:
            c = _naked_put(t, metrics.get(symbol, Metric()), market, today, symbol in ETFS)
            c.tom_list = TOM_TICKERS["naked_put"][1]
            out.append(c)
    for symbol in TOM_TICKERS["pmcc"][0]:
        if (t := charts.get(symbol)) is not None:
            c = _pmcc(t, metrics.get(symbol, Metric()), market, today, symbol in ETFS)
            c.tom_list = TOM_TICKERS["pmcc"][1]
            out.append(c)
    for symbol in TOM_TICKERS["strangle"][0]:
        if (t := charts.get(symbol)) is not None:
            c = _strangle(t, metrics.get(symbol, Metric()), held)
            c.tom_list = TOM_TICKERS["strangle"][1]
            if symbol in MICRO and c.order is not None:
                c.ladder = [MICRO[symbol]]
                c.orders = {symbol: c.order, MICRO[symbol]: c.order}
            out.append(c)
    if (eleven := _eleven_x(charts, market, last_opened.get("11x"), today, net_liq)) is not None:
        out.append(eleven)
    if (es := charts.get("/ES")) is not None:
        out.append(_es_put(es, market, last_opened.get("es_120"), today))
    if "SPX" in charts:
        out.append(_spx_pcs(charts["SPX"], today))
    order = {"ready": 0, "almost": 1, "watch": 2}
    out.sort(key=lambda c: (order[c.status], -c.score, -(float(c.iv_rank or 0))))
    # One strangle per group: the best-ranked keeps its place, and the rest say
    # which one outranked them rather than offering the same bet twice.
    leaders: dict[str, Candidate] = {}
    for c in out:
        if c.setup != STRANGLE.key or c.status == "watch":
            continue
        group = GROUPS.get(c.symbol, c.symbol)
        leader = leaders.setdefault(group, c)
        if leader is not c:
            c.checks.append(
                Check("Best in its group", False, f"{leader.symbol} ranks higher in {group.lower()}")
            )
            c.status, c.score = _status(c.checks)
    out.sort(key=lambda c: (order[c.status], -c.score, -(float(c.iv_rank or 0))))
    return out


# --------------------------------------------------------------------------
# The trade: legs, size, and how it sits in the book
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegPlan:
    action: str
    quantity: int
    right: str
    strike: Decimal
    expiry: date
    dte: int
    delta: Decimal | None
    mark: Decimal | None
    symbol: str
    estimated: bool = False


@dataclass(slots=True)
class Plan:
    legs: list[LegPlan]
    multiplier: Decimal
    credit: Decimal  # per lot, dollars; negative is a debit
    loss_at_stop: Decimal | None  # per lot
    stop: str
    target: str
    lots: int
    lots_reason: str
    bp_per_lot: Decimal | None
    bp_basis: str
    delta_per_lot: Decimal | None  # beta-weighted SPY deltas
    theta_per_lot: Decimal | None  # dollars a day
    vega_per_lot: Decimal | None  # dollars per IV point
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Fit:
    bp_after_share: Decimal | None
    bp_ok: bool | None
    strategy_after_share: Decimal | None
    strategy_ok: bool | None
    delta_after: Decimal | None
    delta_limit: Decimal | None
    delta_ok: bool | None
    theta_after: Decimal | None
    theta_target: Decimal | None
    vega_ratio_after: Decimal | None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Book:
    """The account the trade would join."""

    net_liq: Decimal
    bp_used: Decimal
    theta: Decimal | None
    vega: Decimal | None
    beta_weighted_delta: Decimal | None
    strategy_bp: Mapping[str, Decimal]
    spy_price: Decimal | None


def _floor(v: Decimal) -> int:
    return int(v.to_integral_value(rounding=ROUND_FLOOR)) if v > 0 else 0


def reg_t_put_bp(spot: Decimal, strike: Decimal, premium: Decimal) -> Decimal:
    """Buying power for one naked equity put, the standard way: the larger of
    20% of the stock less the out-of-the-money amount, or 10% of the strike,
    plus the premium — per share, times 100."""
    otm = max(spot - strike, ZERO)
    per_share = max(Decimal("0.20") * spot - otm, Decimal("0.10") * strike) + premium
    return per_share * 100


def build_plan(
    candidate: Candidate,
    legs: Sequence[tuple[LegSpec, OptionQuote]],
    *,
    spot: Decimal,
    multiplier: Decimal,
    beta: Decimal | None,
    book: Book,
    bp_per_lot_hint: Decimal | None = None,
) -> tuple[Plan, Fit]:
    """The recipe filled in with real strikes, sized to Tom's caps and the book."""
    setup = SETUPS[candidate.setup]
    nlv = book.net_liq

    def signed(spec: LegSpec) -> int:
        return -spec.quantity if spec.action == "Sell" else spec.quantity

    credit = sum((-(q.mark or ZERO) * signed(s) * multiplier for s, q in legs), ZERO)
    plan_legs = [
        LegPlan(
            s.action,
            s.quantity,
            q.right.value,
            q.strike,
            q.expiry,
            q.dte,
            q.delta,
            q.mark,
            q.symbol,
            q.estimated,
        )
        for s, q in legs
    ]
    notes: list[str] = []

    # ---- the loss at Tom's exit, per lot -----------------------------------
    stop = target = ""
    loss: Decimal | None
    if setup.key == "11x":
        width = abs(legs[0][1].strike - legs[1][1].strike) * multiplier
        trap = width + credit
        loss = trap
        stop = f"Out at a loss of 1× the trap ({trap:,.0f}) or 2× the credit, whichever comes first"
        target = (
            f"90% of the trap ({Decimal('0.9') * trap:,.0f}) near expiry, or 90% of the credit in the tail"
        )
        notes.append(f"Trap pays up to ${trap:,.0f} a lot if the S&P settles between the spread's strikes.")
    elif setup.key == "pmcc":
        cost = -credit
        loss = PMCC_STOP * cost
        stop = f"Close everything if the LEAP, net of calls sold, is down 30% (${loss:,.0f} a lot)"
        target = "Close the short call at 80–90% of its extrinsic; the whole trade at +50–100%"
        long_q, short_q = legs[0][1], legs[1][1]
        extrinsic = (short_q.mark or ZERO) - max(spot - short_q.strike, ZERO)
        weeks = max(Decimal(short_q.dte) / 7, Decimal("0.5"))
        weekly = extrinsic / weeks / spot if spot else ZERO
        notes.append(f"Short call extrinsic {weekly * 100:.2f}% of the stock a week; Tom aims for 0.75–1%.")
        if long_q.mark:
            notes.append(f"LEAP costs ${long_q.mark * multiplier:,.0f} a lot.")
    else:
        assert setup.stop_multiple is not None and setup.profit_target is not None
        loss = (setup.stop_multiple - _ONE) * credit if credit > ZERO else None
        buy_back = setup.stop_multiple * credit
        stop = f"Buy back at {setup.stop_multiple:g}× the credit: ${buy_back:,.0f} a lot"
        keep = credit * (_ONE - setup.profit_target)
        target = f"Take {setup.profit_target * 100:.0f}%: buy back at ${keep:,.0f} a lot"
        if setup.key == "spx_pcs":
            width = abs(legs[0][1].strike - legs[1][1].strike) * multiplier
            notes.append(
                f"Defined risk: at most ${width - credit:,.0f} a lot if it expires through both strikes."
            )

    # ---- size ---------------------------------------------------------------
    caps: list[tuple[int, str]] = []
    if loss is not None and loss > ZERO:
        caps.append(
            (_floor(setup.loss_cap * nlv / loss), f"{setup.loss_cap * 100:.1f}% of net liq at Tom's exit")
        )
    if setup.key == "strangle" and credit > ZERO:
        caps.append((_floor(Decimal("0.01") * nlv / credit), "credit up to 1% of net liq"))
    if setup.key == "spx_pcs":
        # A defined-risk spread risks its width, whatever the stop says: Tom's
        # 2% is a cap on what the trade can lose, so the width is what counts.
        worst = abs(legs[0][1].strike - legs[1][1].strike) * multiplier - max(credit, ZERO)
        if worst > ZERO:
            caps.append((_floor(setup.loss_cap * nlv / worst), "max loss up to 2% of net liq"))

    bp_per_lot: Decimal | None = None
    basis = "not known before the order ticket"
    if setup.key == "naked_put" and len(legs) == 1:
        bp_per_lot = reg_t_put_bp(spot, legs[0][1].strike, legs[0][1].mark or ZERO)
        basis = "standard naked-put formula (estimate)"
    elif setup.key in ("pmcc",):
        bp_per_lot = -credit if credit < ZERO else None
        basis = "the debit paid"
    elif setup.key == "spx_pcs":
        bp_per_lot = abs(legs[0][1].strike - legs[1][1].strike) * multiplier - max(credit, ZERO)
        basis = "the spread's max loss"
    elif bp_per_lot_hint is not None:
        bp_per_lot = bp_per_lot_hint
        basis = "what this product has used in your own trades"

    room = PLAN.bp_target[1] * nlv - book.bp_used
    if bp_per_lot and bp_per_lot > ZERO:
        caps.append((_floor(room / bp_per_lot), "buying power up to 50% of net liq"))
        strategy_room = PLAN.strategy_cap * nlv - book.strategy_bp.get(setup.playbook, ZERO)
        caps.append((_floor(strategy_room / bp_per_lot), "20% of net liq per strategy"))

    if caps:
        lots, reason = min(caps, key=lambda c: c[0])
    else:
        lots, reason = 1, "no cap could be measured"
    if lots < 1 and setup.key == "strangle" and candidate.symbol in MICRO:
        notes.append(
            f"One {candidate.symbol} lot is bigger than Tom's cap for this account; the micro "
            f"{MICRO[candidate.symbol]} is a tenth of the size."
        )

    # ---- greeks per lot -------------------------------------------------------
    def total(field_: str) -> Decimal | None:
        values = [getattr(q, field_) for _, q in legs]
        if any(v is None for v in values):
            return None
        return sum((v * signed(s) * multiplier for (s, _), v in zip(legs, values, strict=True)), ZERO)

    raw_delta = total("delta")
    bwd = None
    if raw_delta is not None and beta is not None and book.spy_price:
        bwd = raw_delta * spot * beta / book.spy_price
    theta = total("theta")
    vega = total("vega")

    plan = Plan(
        legs=plan_legs,
        multiplier=multiplier,
        credit=credit,
        loss_at_stop=loss,
        stop=stop,
        target=target,
        lots=max(lots, 0),
        lots_reason=reason,
        bp_per_lot=bp_per_lot,
        bp_basis=basis,
        delta_per_lot=bwd,
        theta_per_lot=theta,
        vega_per_lot=vega,
        notes=notes,
    )
    return plan, fit_into(plan, setup, book)


def fit_into(plan: Plan, setup: Setup, book: Book) -> Fit:
    """How the book would read with this trade on, against Tom's limits."""
    nlv = book.net_liq
    lots = Decimal(max(plan.lots, 1))
    notes: list[str] = []
    bp_after_share = bp_ok = strategy_share = strategy_ok = None
    if plan.bp_per_lot is not None:
        bp_after = book.bp_used + plan.bp_per_lot * lots
        bp_after_share = bp_after / nlv
        bp_ok = bp_after_share <= PLAN.bp_target[1]
        strategy_after = book.strategy_bp.get(setup.playbook, ZERO) + plan.bp_per_lot * lots
        strategy_share = strategy_after / nlv
        strategy_ok = strategy_share <= PLAN.strategy_cap
    else:
        notes.append("Buying power for this product is only known on the order ticket.")
    limit = PLAN.delta_share * nlv
    delta_after = delta_ok = None
    if plan.delta_per_lot is not None and book.beta_weighted_delta is not None:
        delta_after = book.beta_weighted_delta + plan.delta_per_lot * lots
        delta_ok = abs(delta_after) <= limit or abs(delta_after) < abs(book.beta_weighted_delta)
    theta_after = (
        None if plan.theta_per_lot is None or book.theta is None else book.theta + plan.theta_per_lot * lots
    )
    vega_ratio = None
    if theta_after and theta_after > ZERO and plan.vega_per_lot is not None and book.vega is not None:
        vega_ratio = abs(book.vega + plan.vega_per_lot * lots) / theta_after
    return Fit(
        bp_after_share=bp_after_share,
        bp_ok=bp_ok,
        strategy_after_share=strategy_share,
        strategy_ok=strategy_ok,
        delta_after=delta_after,
        delta_limit=limit,
        delta_ok=delta_ok,
        theta_after=theta_after,
        theta_target=PLAN.theta_target[0] * nlv,
        vega_ratio_after=vega_ratio,
        notes=notes,
    )


def settle(candidate: Candidate) -> None:
    """Fold the size and the fit into the candidate's status."""
    plan, fit = candidate.plan, candidate.fit
    if plan is None:
        return
    if plan.lots < 1 and candidate.status == "ready":
        candidate.status = "almost"
    if fit is not None and (fit.bp_ok is False or fit.strategy_ok is False) and candidate.status == "ready":
        candidate.status = "almost"


@dataclass(slots=True)
class ScanResult:
    as_of: datetime
    market: Regime | None
    net_liq: Decimal | None
    bp_share: Decimal | None
    bp_room: Decimal | None
    theta: Decimal | None
    theta_target: Decimal | None
    delta: Decimal | None
    delta_limit: Decimal | None
    vega_ratio: Decimal | None
    candidates: list[Candidate]
    scanned: int
    missing: list[str]
    seconds: float


def years_to(dte: int) -> Decimal:
    return Decimal(max(dte, 1)) / Decimal(365)


def nearest_strike(strikes: Sequence[Decimal], at_or_below: Decimal) -> Decimal | None:
    under = [k for k in strikes if k <= at_or_below]
    return max(under) if under else None


def estimated_quote(
    right: OptionType, strike: Decimal, expiry: date, dte: int, spot: Decimal, iv: Decimal, symbol: str
) -> OptionQuote:
    """A model quote, said to be one, for a strike the broker sent no greeks for."""
    from tastydesk.core.scenario import black_scholes

    years = years_to(dte)
    return OptionQuote(
        symbol=symbol,
        right=right,
        strike=strike,
        expiry=expiry,
        dte=dte,
        mark=black_scholes(right, spot, strike, years, iv),
        bid=None,
        ask=None,
        delta=bs_delta(right, spot, strike, years, iv),
        theta=None,
        vega=None,
        estimated=True,
    )
