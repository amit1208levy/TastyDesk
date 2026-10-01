"""Tom King's 2026 trading plan, as rules this book can be measured against.

The user asked for one thing above all: to trade more like Tom. That needs his
rules written down as numbers rather than as a feeling, and then every trade —
open and closed — held up against them. This module is that.

Where the rules come from
-------------------------
Tom King's *2026 Trading Plan v4* and his strategy sheets (11x Bear Trap,
FB/BA 11x, Call Cannon, Dynamic PMCC), plus the rules he states in his videos
where the plan is silent — the 21-DTE exit on a strangle is from the videos,
and is labelled that way. Nothing here is invented to fill a gap: a structure
Tom does not trade is called "not in Tom's plan", and where a check is run on it
anyway by analogy (a naked call held to the naked put's stop) that is said.

What it does not do
-------------------
It does not decide anything. Every output is a rule, the measurement, and the
difference between them, in words. Whether to act on a difference is the
reader's call, and the page says so.

Units
-----
Money is Decimal, as everywhere else in this app. Chart arithmetic — moving
averages, RSI, parabolic SAR — is float: those feed a yes/no about a trend and
are never shown as anyone's money.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from tastydesk.core.analytics import closed_strategies, max_profit_at_close
from tastydesk.core.models import (
    ZERO,
    Leg,
    OptionType,
    Strategy,
    StrategyPnL,
    StrategyRisk,
    StrategyType,
)
from tastydesk.core.occ import product_root
from tastydesk.core.prices import Bar, yahoo_symbol

__all__ = ["PLAN", "OpenPosition", "analyse", "playbook_for", "regime_series"]

_ONE = Decimal(1)
_HUNDRED = Decimal(100)


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Plan:
    """Tom King, *2026 Trading Plan v4*. Every number is his."""

    # §1 — 2–3% of net liq a month.
    monthly_income: tuple[Decimal, Decimal] = (Decimal("0.02"), Decimal("0.03"))
    # §3.1 — buying power used, as a share of net liq.
    bp_target: tuple[Decimal, Decimal] = (Decimal("0.40"), Decimal("0.50"))
    bp_elevated: Decimal = Decimal("0.60")  # 50–60%: limited new trades
    bp_alert: Decimal = Decimal("0.85")  # 60–85%: no new trades; above: reduce now
    # §3.3 — never more than 2% of the portfolio on one trade.
    max_loss: Decimal = Decimal("0.02")
    # §10.2 — an 11x on a single future or stock: 1%.
    max_loss_spec_11x: Decimal = Decimal("0.01")
    # §3.3 — no strategy above 40% of the maximum allowed buying power, which
    # §8 sets at 50% of the account: 20% of net liq per strategy.
    max_allowed_bp: Decimal = Decimal("0.50")
    strategy_share_of_bp: Decimal = Decimal("0.40")
    # §2 — delta within 0.2% of net liq, beta-weighted to SPY; theta at least
    # 0.3–0.4% of net liq a day; vega no more than 1–1.5x theta.
    delta_share: Decimal = Decimal("0.002")
    theta_target: tuple[Decimal, Decimal] = (Decimal("0.003"), Decimal("0.004"))
    vega_to_theta: Decimal = Decimal("1.5")
    # §8 — 11x 30%, Dynamic PMCC 40%, spec trades 30%.
    allocation: tuple[tuple[str, Decimal], ...] = (
        ("11x", Decimal("0.30")),
        ("PMCC", Decimal("0.40")),
        ("Spec", Decimal("0.30")),
    )

    @property
    def strategy_cap(self) -> Decimal:
        """Buying power one strategy may use, as a share of net liq."""
        return self.max_allowed_bp * self.strategy_share_of_bp


PLAN = Plan()

# §9.2: close the whole PMCC when the long call, net of the calls sold against
# it, is down 30% of what it cost.
PMCC_STOP = Decimal("0.30")

# Products Tom runs his core 11x campaign on (§9.1), and the ones his strangle
# and naked-put examples use for the S&P.
_INDEX_ROOTS = frozenset({"/ES", "/MES", "SPY", "SPX", "XSP", "SPXW"})


# --------------------------------------------------------------------------
# Which of Tom's playbooks a trade is
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Playbook:
    """How Tom runs one kind of trade.

    ``stop_multiple`` is in Tom's own convention: the price to buy the trade
    back at, as a multiple of the credit. A 2.5x stop on a $1,000 strangle closes
    it when it costs $2,500 to buy back — a $1,500 loss. ``max_loss_share`` caps
    that loss (or the defined max loss) as a share of net liq.
    """

    key: str
    name: str
    tier: str  # core | spec | hedge | outside
    bias: str  # bullish | neutral | bearish | hedge | none
    stop_multiple: Decimal | None = None
    profit_target: Decimal | None = None
    entry_dte: tuple[int, int] | None = None
    max_loss_share: Decimal = PLAN.max_loss
    # True when Tom has no rule for this structure and the check borrows the
    # nearest one he does have. Said on the page, never hidden.
    analog: bool = False
    source: str = ""


_STRANGLE = Playbook(
    "strangle",
    "Strangle",
    "spec",
    "neutral",
    stop_multiple=Decimal("2.5"),
    profit_target=Decimal("0.50"),
    entry_dte=(45, 90),
    # Max credit 1% of net liq at a 2.5x stop is a 1.5% loss.
    max_loss_share=Decimal("0.015"),
    source="Plan §10.1: 60 DTE, 9Δ call / 8Δ put, credit ≤ 1% of net liq, take 50%, stop 2.5× credit",
)
_NAKED_PUT = Playbook(
    "naked_put",
    "Naked put",
    "spec",
    "bullish",
    stop_multiple=Decimal("3"),
    profit_target=Decimal("0.50"),
    entry_dte=(30, 60),
    source="Plan §10.3: quality names in an uptrend, take 50%, stop 3× credit",
)
_ES_PUT = Playbook(
    "es_120",
    "120-DTE /ES put",
    "spec",
    "bullish",
    stop_multiple=Decimal("4"),
    profit_target=Decimal("0.40"),
    entry_dte=(100, 140),
    source="Plan §10.5: ~120 DTE, ~6Δ, take 40%, stop 4× credit",
)
_PUT_SPREAD = Playbook(
    "put_spread",
    "Put credit spread",
    "spec",
    "bullish",
    stop_multiple=Decimal("2.5"),
    profit_target=Decimal("0.50"),
    entry_dte=(4, 30),
    source="Plan §10.6 and the credit-spread video: take 50%, stop 2.5× credit",
)
_CONDOR = Playbook(
    "condor",
    "Iron condor",
    "spec",
    "neutral",
    stop_multiple=Decimal("2.5"),
    profit_target=Decimal("0.50"),
    entry_dte=(0, 60),
    source="Plan §10.4: Tom only runs condors at 0 DTE (take 25%, stop 2.5×)",
)
_ELEVEN_X = Playbook(
    "11x",
    "11x Bear Trap",
    "core",
    "bullish",
    stop_multiple=Decimal("3"),
    profit_target=Decimal("0.90"),
    entry_dte=(45, 65),
    source="Plan §9.1: put debit spread paid for by naked puts, 50–60 DTE, stop 2× credit",
)
_ELEVEN_X_SPEC = Playbook(
    "11x_spec",
    "11x on a single product",
    "spec",
    "bullish",
    stop_multiple=Decimal("3"),
    profit_target=Decimal("0.90"),
    entry_dte=(12, 65),
    max_loss_share=PLAN.max_loss_spec_11x,
    source="Plan §10.2: bullish asset and market only, max loss 1% of net liq",
)
_PMCC = Playbook(
    "pmcc",
    "PMCC / Call Cannon",
    "core",
    "bullish",
    source="Plan §9.2: 80Δ LEAP, short calls 7–30 DTE, close if down 30% of the LEAP",
)
_COVERED = Playbook(
    "covered",
    "Covered call",
    "core",
    "bullish",
    source="Tom's covered-call ETF income and DPMCC",
)
_HEDGE = Playbook(
    "hedge",
    "Long put (hedge)",
    "hedge",
    "hedge",
    source='Plan §3.2: "add a hedge using a long put" — debit, defined risk',
)
_PUT_DIAGONAL = Playbook(
    "put_diagonal",
    "Put diagonal (hedge)",
    "hedge",
    "hedge",
    source="Long puts with nearer puts sold against them. Tom's own hedge is a plain long put (§3.2)",
)
_LONG_CALL = Playbook(
    "long_call", "Long call", "spec", "bullish", source="Defined risk: the debit is the max loss"
)
_DEBIT = Playbook("debit", "Debit spread", "spec", "none", source="Defined risk: the debit is the max loss")
_NAKED_CALL = Playbook(
    "naked_call",
    "Naked call",
    "outside",
    "bearish",
    stop_multiple=Decimal("3"),
    profit_target=Decimal("0.50"),
    analog=True,
    source="Not in Tom's plan. Checked against his naked-put rules by analogy",
)
_CALL_SPREAD = Playbook(
    "call_spread",
    "Call credit spread",
    "outside",
    "bearish",
    stop_multiple=Decimal("2.5"),
    profit_target=Decimal("0.50"),
    analog=True,
    source="Not in Tom's plan. Checked against his put-spread rules by analogy",
)
_FUTURE = Playbook(
    "future",
    "Outright future",
    "outside",
    "none",
    source="Not in Tom's plan: no stop, and nothing caps the loss",
)
_SHARES = Playbook("shares", "Shares", "outside", "bullish", source="Tom replaces shares with a LEAP (DPMCC)")
_CUSTOM = Playbook("custom", "Custom", "outside", "none", source="A shape none of Tom's playbooks describe")


def _is_eleven_x(legs: Sequence[Leg]) -> bool:
    """A put debit spread financed by more naked puts below it.

    Puts only, more short contracts than long, and at least one short strike
    below every long strike — the naked puts sit under the spread. Covers the
    111, 112, 113, FB and BA variants alike, since they differ only in how many
    naked puts there are and where.
    """
    options = [leg for leg in legs if leg.is_option]
    if not options or any(leg.option_type is not OptionType.PUT for leg in options):
        return False
    if any(not leg.is_option for leg in legs):
        return False
    longs = [leg for leg in options if not leg.is_short and leg.strike is not None]
    shorts = [leg for leg in options if leg.is_short and leg.strike is not None]
    if not longs or not shorts:
        return False
    if sum((leg.quantity for leg in shorts), ZERO) <= sum((leg.quantity for leg in longs), ZERO):
        return False
    lowest_long = min(leg.strike for leg in longs if leg.strike is not None)
    return any(leg.strike is not None and leg.strike < lowest_long for leg in shorts)


def _is_call_diagonal(legs: Sequence[Leg]) -> bool:
    """A long call with a shorter-dated short call against it: PMCC or Call Cannon."""
    longs = [leg for leg in legs if leg.is_option and not leg.is_short]
    shorts = [leg for leg in legs if leg.is_option and leg.is_short]
    if not longs or not shorts:
        return False
    if any(leg.option_type is not OptionType.CALL for leg in longs + shorts):
        return False
    far = max((leg.expiration for leg in longs if leg.expiration), default=None)
    near = min((leg.expiration for leg in shorts if leg.expiration), default=None)
    return far is not None and near is not None and far > near


def _covered_puts(legs: Sequence[Leg]) -> bool:
    """Long puts at or above every short put, at least as many of them: a hedge
    that sells some of its own cost back, never a naked put in disguise."""
    if any(not leg.is_option or leg.option_type is not OptionType.PUT for leg in legs):
        return False
    longs = [leg for leg in legs if not leg.is_short and leg.strike is not None]
    shorts = [leg for leg in legs if leg.is_short and leg.strike is not None]
    if not longs or not shorts:
        return False
    if sum((leg.quantity for leg in longs), ZERO) < sum((leg.quantity for leg in shorts), ZERO):
        return False
    return min(leg.strike for leg in longs if leg.strike is not None) >= max(
        leg.strike for leg in shorts if leg.strike is not None
    )


def playbook_for(strategy: Strategy) -> Playbook:
    """The playbook of Tom's this trade is an instance of, or the nearest thing."""
    t = strategy.strategy_type
    root = product_root(strategy.underlying)
    legs = strategy.legs
    if _is_eleven_x(legs):
        return _ELEVEN_X if root in _INDEX_ROOTS else _ELEVEN_X_SPEC
    # Short options and nothing else, in a shape the classifier would not
    # name — puts and calls in two different months, seven puts against two
    # calls. Whatever the label, it is a strangle, a naked put or a naked call,
    # and Tom has rules for each.
    if legs and all(leg.is_option and leg.is_short for leg in legs) and t is StrategyType.CUSTOM:
        kinds = {leg.option_type for leg in legs}
        if kinds == {OptionType.PUT}:
            return _NAKED_PUT
        if kinds == {OptionType.CALL}:
            return _NAKED_CALL
        return _STRANGLE
    if t in (StrategyType.SHORT_STRANGLE, StrategyType.SHORT_STRADDLE, StrategyType.JADE_LIZARD):
        return _STRANGLE
    if t is StrategyType.NAKED_PUT:
        dte = strategy.front_entry_dte
        if root in {"/ES", "/MES"} and dte is not None and dte >= 100:
            return _ES_PUT
        return _NAKED_PUT
    if t is StrategyType.PUT_CREDIT_SPREAD:
        return _PUT_SPREAD
    if t in (StrategyType.IRON_CONDOR, StrategyType.IRON_FLY):
        return _CONDOR
    if t is StrategyType.NAKED_CALL:
        return _NAKED_CALL
    if t is StrategyType.CALL_CREDIT_SPREAD:
        return _CALL_SPREAD
    if t is StrategyType.COVERED_CALL:
        return _COVERED
    if t in (StrategyType.DIAGONAL, StrategyType.CALENDAR) and _is_call_diagonal(legs):
        return _PMCC
    if t in (StrategyType.DIAGONAL, StrategyType.CALENDAR) and _covered_puts(legs):
        return _PUT_DIAGONAL
    if t is StrategyType.LONG_PUT:
        return _HEDGE
    if t in (StrategyType.LONG_CALL,):
        return _LONG_CALL
    if t in (
        StrategyType.PUT_DEBIT_SPREAD,
        StrategyType.CALL_DEBIT_SPREAD,
        StrategyType.LONG_STRANGLE,
        StrategyType.LONG_STRADDLE,
    ):
        return _DEBIT
    if t is StrategyType.FUTURE:
        return _FUTURE
    if t is StrategyType.EQUITY:
        return _SHARES
    return _CUSTOM


def _leg_sign(leg: Leg) -> int:
    """Which way a leg leans, from its shape alone: +1 bullish, -1 bearish."""
    if leg.option_type is OptionType.PUT:
        return 1 if leg.is_short else -1
    if leg.option_type is OptionType.CALL:
        return -1 if leg.is_short else 1
    return -1 if leg.is_short else 1


def lean_of(legs: Sequence[Leg], *, live: bool) -> str:
    """bullish | bearish | neutral for a set of legs.

    With live deltas (an open position) the deltas decide: a strangle whose put
    is deep in the money leans bullish whatever its shape says. Without them (a
    closed trade) the shape decides. Within a quarter of the gross either way
    is neutral.
    """
    parts: list[Decimal] = []
    if live:
        for leg in legs:
            d = leg.position_delta
            if d is None:
                parts = []
                break
            parts.append(d)
    if not parts:
        parts = [Decimal(_leg_sign(leg)) * leg.quantity for leg in legs]
    net = sum(parts, ZERO)
    gross = sum((abs(p) for p in parts), ZERO)
    if gross == ZERO or abs(net) <= gross / 4:
        return "neutral"
    return "bullish" if net > ZERO else "bearish"


# --------------------------------------------------------------------------
# The chart: Tom's four regime tests
# --------------------------------------------------------------------------


def ema(values: Sequence[float], n: int) -> list[float | None]:
    """Exponential moving average, seeded with the simple average of the first n."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def rsi(closes: Sequence[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        change = closes[i] - closes[i - 1]
        gain += max(change, 0.0)
        loss += max(-change, 0.0)
    avg_gain, avg_loss = gain / n, loss / n

    def value() -> float:
        if avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    out[n] = value()
    for i in range(n + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(change, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-change, 0.0)) / n
        out[i] = value()
    return out


def psar(bars: Sequence[Bar], step: float = 0.02, cap: float = 0.2) -> list[tuple[float, bool] | None]:
    """Wilder's parabolic SAR: (level, True when the SAR sits under price)."""
    out: list[tuple[float, bool] | None] = [None] * len(bars)
    if len(bars) < 3:
        return out
    bull = bars[1].close >= bars[0].close
    sar = min(bars[0].low, bars[1].low) if bull else max(bars[0].high, bars[1].high)
    extreme = max(bars[0].high, bars[1].high) if bull else min(bars[0].low, bars[1].low)
    af = step
    out[1] = (sar, bull)
    for i in range(2, len(bars)):
        sar = sar + af * (extreme - sar)
        if bull:
            sar = min(sar, bars[i - 1].low, bars[i - 2].low)
            if bars[i].low < sar:
                bull, sar, extreme, af = False, extreme, bars[i].low, step
            elif bars[i].high > extreme:
                extreme, af = bars[i].high, min(af + step, cap)
        else:
            sar = max(sar, bars[i - 1].high, bars[i - 2].high)
            if bars[i].high > sar:
                bull, sar, extreme, af = True, extreme, bars[i].high, step
            elif bars[i].low < extreme:
                extreme, af = bars[i].low, min(af + step, cap)
        out[i] = (sar, bull)
    return out


@dataclass(frozen=True, slots=True)
class Condition:
    key: str
    label: str
    met: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Regime:
    """Tom's §7 regime for one chart on one day."""

    product: str
    chart: str
    as_of: date
    label: str  # Bullish | Neutral | Bearish
    bullish: int  # of 4 conditions
    conditions: tuple[Condition, ...]
    price: float
    ema8: float
    ema21: float
    ema50: float | None
    rsi: float
    sar: float
    uptrend: bool | None  # 21 EMA above the 50: Tom's put-selling trend test
    put_zone: str  # what Tom's §10.3 table says about selling a put here now
    focus: tuple[str, ...]


_FOCUS = {
    "Bullish": ("Standard 11x", "OTM DPMCC", "Naked puts"),
    "Neutral": ("ATM 11x", "ATM DPMCC", "Strangles"),
    "Bearish": ("ATM/ITM 11x", "ATM/ITM DPMCC", "Reduce buying power"),
}


@dataclass(slots=True)
class Series:
    """Every indicator for one product, computed once, looked up by day."""

    product: str
    chart: str
    days: list[date]
    bars: list[Bar]
    ema8: list[float | None]
    ema21: list[float | None]
    ema50: list[float | None]
    rsi: list[float | None]
    sar: list[tuple[float, bool] | None]
    bb_mid: list[float | None]
    bb_up: list[float | None]

    def last_before(self, day: date) -> int | None:
        """Index of the last completed bar before ``day`` — no peeking at the day itself."""
        i = bisect_left(self.days, day) - 1
        return i if i >= 0 else None

    def close_on_or_before(self, day: date) -> float | None:
        i = bisect_left(self.days, day + timedelta(days=1)) - 1
        return self.bars[i].close if i >= 0 else None

    def regime_at(self, i: int) -> Regime | None:
        e8, e21, r, s = self.ema8[i], self.ema21[i], self.rsi[i], self.sar[i]
        if e8 is None or e21 is None or r is None or s is None:
            return None
        bar = self.bars[i]
        price = bar.close
        sar_level, sar_bull = s
        conditions = (
            Condition("above_21", "Price above the 21 EMA", price > e21, f"{price:,.2f} vs {e21:,.2f}"),
            Condition("ema_cross", "8 EMA above the 21 EMA", e8 > e21, f"{e8:,.2f} vs {e21:,.2f}"),
            Condition("psar", "Parabolic SAR under price", sar_bull, f"SAR {sar_level:,.2f}"),
            Condition("rsi", "RSI above 50", r > 50, f"RSI {r:.0f}"),
        )
        count = sum(1 for c in conditions if c.met)
        label = "Bullish" if count >= 3 else "Neutral" if count == 2 else "Bearish"
        e50 = self.ema50[i]
        mid, up = self.bb_mid[i], self.bb_up[i]
        # §10.3: below the 21 EMA sell at 0.20+ delta, at the 21 EMA sell at
        # 0.10–0.16, near the top of the Bollinger band do not sell puts at all.
        # That table assumes his entry rule is already met — an uptrending
        # chart — so on a chart that is not trending up it says nothing but no.
        if label == "Bearish" or (e50 is not None and e21 < e50):
            zone = "No new puts: Tom sells puts only on a chart that is trending up"
        elif mid is not None and up is not None and price >= mid + 0.75 * (up - mid):
            zone = "Do not sell puts here (near the top of the Bollinger band)"
        elif price >= e21:
            zone = "Sell puts at 0.10–0.16 delta (at or above the 21 EMA)"
        else:
            zone = "Sell puts at 0.20+ delta (pulled back under the 21 EMA)"
        return Regime(
            product=self.product,
            chart=self.chart,
            as_of=bar.day,
            label=label,
            bullish=count,
            conditions=conditions,
            price=price,
            ema8=e8,
            ema21=e21,
            ema50=e50,
            rsi=r,
            sar=sar_level,
            uptrend=None if e50 is None else e21 > e50,
            put_zone=zone,
            focus=_FOCUS[label],
        )

    def latest(self) -> Regime | None:
        return self.regime_at(len(self.bars) - 1) if self.bars else None


def regime_series(product: str, bars: Sequence[Bar]) -> Series:
    closes = [b.close for b in bars]
    mid = sma(closes, 20)
    up: list[float | None] = [None] * len(closes)
    for i in range(19, len(closes)):
        window = closes[i - 19 : i + 1]
        m = mid[i]
        assert m is not None
        sd = (sum((c - m) ** 2 for c in window) / 20) ** 0.5
        up[i] = m + 2 * sd
    return Series(
        product=product,
        chart=yahoo_symbol(product) or product,
        days=[b.day for b in bars],
        bars=list(bars),
        ema8=ema(closes, 8),
        ema21=ema(closes, 21),
        ema50=ema(closes, 50),
        rsi=rsi(closes),
        sar=psar(bars),
        bb_mid=mid,
        bb_up=up,
    )


def fit(bias: str, regime: Regime | None, book: Playbook) -> str | None:
    """with | caution | against: does Tom's regime table back this trade?

    The core 11x is a campaign trade Tom places every two weeks "no matter
    what" (§9.1) — the regime only moves the spread — so it always fits. A
    hedge is not judged: insurance bought against the trend is the point of it.
    Everything else is judged on its own chart.
    """
    if book.key == "11x":
        return "with"
    if regime is None or book.tier == "hedge" or bias in ("none", "hedge"):
        return None
    label = regime.label
    if bias == "bullish":
        return {"Bullish": "with", "Neutral": "caution", "Bearish": "against"}[label]
    if bias == "bearish":
        return {"Bearish": "with", "Neutral": "caution", "Bullish": "against"}[label]
    return "with" if label == "Neutral" else "caution"


# --------------------------------------------------------------------------
# Adding to a loser
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entry:
    """One order's worth of new exposure: the legs a strategy opened at one moment."""

    strategy_id: str
    product: str
    at: datetime
    lean: str
    closed_at: datetime | None
    # Long puts and nothing else: insurance. Buying more of it as the market
    # rises is not averaging down on a bet, so it is never judged as one.
    hedge: bool = False


@dataclass(frozen=True, slots=True)
class AddToLoser:
    strategy_id: str
    product: str
    at: date
    earlier: date
    move: float  # the product's move from the earlier entry to the day before this one
    lean: str

    @property
    def text(self) -> str:
        direction = "fallen" if self.move < 0 else "risen"
        return (
            f"Added on {self.at:%b %d} while your {self.lean} {self.product} position from "
            f"{self.earlier:%b %d} was losing: {self.product} had {direction} "
            f"{abs(self.move) * 100:.1f}% since. Tom never adds to a losing position."
        )


_AGAINST_MOVE = 0.005  # half a percent against an earlier entry counts as losing


def _entries(strategy: Strategy, *, live: bool) -> list[Entry]:
    """The orders that built this strategy, minus the rolls.

    A roll is its own sin and is counted elsewhere; counting it again here
    would make one decision look like two.
    """
    roll_times = [r.at for r in strategy.rolls]
    groups: dict[datetime, list[Leg]] = defaultdict(list)
    for leg in strategy.legs:
        at = leg.opened_at or strategy.opened_at
        if any(abs((at - r).total_seconds()) < 120 for r in roll_times):
            continue
        # Legs filled within a couple of minutes are one order.
        key = next((k for k in groups if abs((k - at).total_seconds()) < 120), at)
        groups[key].append(leg)
    root = product_root(strategy.underlying)
    return [
        Entry(
            strategy.id,
            root,
            at,
            lean_of(legs, live=live and strategy.is_open),
            strategy.closed_at,
            hedge=all(leg.option_type is OptionType.PUT and not leg.is_short for leg in legs),
        )
        for at, legs in sorted(groups.items())
    ]


def adds_to_losers(strategies: Sequence[Strategy], series: Mapping[str, Series]) -> list[AddToLoser]:
    """Every entry made in the same direction as an open entry that was losing.

    Losing is judged on the product's own chart: an earlier bullish entry is
    losing when the product closed more than half a percent lower the day
    before the new one than on the day the earlier one went on. That is an
    estimate — a short put can be down a little and still be a winner on time
    decay — which is why the move is printed with every finding. Same-day adds
    are not judged at all: there is no completed bar between them.
    """
    by_product: dict[str, list[Entry]] = defaultdict(list)
    for s in strategies:
        for e in _entries(s, live=True):
            by_product[e.product].append(e)

    found: list[AddToLoser] = []
    for product, entries in by_product.items():
        chart = series.get(product)
        if chart is None:
            continue
        entries.sort(key=lambda e: e.at)
        for k, new in enumerate(entries):
            if new.lean == "neutral" or new.hedge:
                continue
            before = chart.last_before(new.at.date())
            if before is None:
                continue
            now_close = chart.bars[before].close
            for old in entries[:k]:
                if old.lean != new.lean or old.hedge:
                    continue
                if old.closed_at is not None and old.closed_at <= new.at:
                    continue
                if old.at.date() >= new.at.date():
                    continue
                then = chart.close_on_or_before(old.at.date())
                if not then:
                    continue
                move = (now_close - then) / then
                losing = move < -_AGAINST_MOVE if new.lean == "bullish" else move > _AGAINST_MOVE
                if losing:
                    found.append(
                        AddToLoser(new.strategy_id, product, new.at.date(), old.at.date(), move, new.lean)
                    )
                    break
    return found


# --------------------------------------------------------------------------
# The open book
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """One row of the Positions tab, as the service already measured it."""

    strategy: Strategy
    pnl: StrategyPnL
    risk: StrategyRisk
    name: str | None = None
    # The journal's own trades inside this row. One, unless the user named a
    # strategy and its open trades were merged into a single row.
    member_ids: tuple[str, ...] = ()
    # What the named strategy's closed trades banked since this position went
    # on — the calls already sold against a PMCC's LEAP, which Tom counts.
    banked: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class Flag:
    code: str
    level: str  # breach | watch | good | info
    text: str


@dataclass(slots=True)
class PositionCheck:
    id: str
    label: str
    title: str
    underlying: str
    product: str
    structure: str
    playbook: str
    playbook_name: str
    tier: str
    bucket: str
    lean: str
    opened: date
    dte: int | None
    dte_at_entry: int | None
    rolls: int
    credit: Decimal
    open_pnl: Decimal | None
    pct_of_credit: Decimal | None
    buying_power: Decimal | None
    bp_share: Decimal | None
    # What the trade ties up: buying power, or for a debit trade whatever it
    # cost if that is more. Tom's 30/40/30 split is of capital, and a PMCC
    # whose LEAP covers its short call uses no buying power at all.
    capital: Decimal
    # Size: what the trade can lose under Tom's own exit, against his cap.
    loss_at_stop: Decimal | None
    loss_at_stop_share: Decimal | None
    size_cap: Decimal
    size_basis: str
    # The stop, in money: close when buying it back costs this much.
    stop_multiple: Decimal | None
    stop_cost: Decimal | None
    cost_to_close: Decimal | None
    stop_progress: Decimal | None  # 0 at entry, 1 at Tom's stop
    target: Decimal | None
    target_progress: Decimal | None  # 1 = at Tom's profit target
    regime: str | None
    fit: str | None
    flags: list[Flag] = field(default_factory=list)
    severity: int = 0
    source: str = ""
    analog: bool = False


def _tidy(value: Decimal) -> Decimal:
    """Zero as plain 0. Decimal keeps the exponent of a division ("0E+1"),
    which is correct and reads like an error on the page."""
    return ZERO if value == ZERO else value


def _share(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or whole is None or whole == ZERO:
        return None
    return _tidy(part / whole)


def _money(v: Decimal) -> str:
    return f"${abs(v):,.0f}"


def _pct(v: Decimal | float, digits: int = 1) -> str:
    return f"{float(v) * 100:.{digits}f}%"


def check_position(
    pos: OpenPosition,
    *,
    net_liq: Decimal | None,
    regime: Regime | None,
    adds: Sequence[AddToLoser],
) -> PositionCheck:
    s, pnl = pos.strategy, pos.pnl
    book = playbook_for(s)
    lean = lean_of(s.legs, live=True)
    bias = book.bias if book.bias in ("bullish", "bearish", "neutral", "hedge") else lean
    credit = pnl.net_credit
    flags: list[Flag] = []

    # ---- size -----------------------------------------------------------
    # What the trade can lose under Tom's own exit for it. Outright futures
    # have no such number: nothing caps them and Tom has no stop for them.
    has_future = any(not leg.is_option for leg in s.legs)
    loss_at_stop: Decimal | None = None
    basis = ""
    if has_future:
        pass
    elif book.stop_multiple is not None and credit > ZERO:
        loss_at_stop = (book.stop_multiple - _ONE) * credit
        basis = f"loss at Tom's {book.stop_multiple:g}× stop"
    elif book.key == "pmcc" and credit < ZERO:
        loss_at_stop = PMCC_STOP * -credit
        basis = "loss at Tom's 30% PMCC stop"
    elif pnl.max_loss is not None:
        loss_at_stop = abs(pnl.max_loss)
        basis = "max loss"
    elif credit < ZERO and (all(not leg.is_short for leg in s.legs) or book.key == "put_diagonal"):
        loss_at_stop = -credit
        basis = "premium paid"
    size_share = _share(loss_at_stop, net_liq)
    cap = book.max_loss_share
    if size_share is not None:
        if size_share > cap * Decimal("1.25"):
            flags.append(
                Flag(
                    "size",
                    "breach",
                    f"Could lose {_money(loss_at_stop or ZERO)} ({_pct(size_share)} of net liq) at "
                    f"Tom's exit. He caps one trade at {_pct(cap, 1)}.",
                )
            )
        elif size_share > cap:
            flags.append(
                Flag("size", "watch", f"Slightly over Tom's {_pct(cap, 1)} size cap ({_pct(size_share)}).")
            )
    elif has_future:
        flags.append(
            Flag(
                "size",
                "breach",
                "Holds outright futures: nothing caps the loss and Tom has no stop for them, so his 2% "
                "cap can't be met.",
            )
        )
    elif credit > ZERO or book.key == "custom":
        flags.append(
            Flag(
                "size",
                "watch",
                "No defined max loss and no Tom stop for this shape, so it can't be sized his way.",
            )
        )

    # Already lost more than Tom lets a whole trade lose.
    if pnl.open_pnl is not None and net_liq and pnl.open_pnl < -PLAN.max_loss * net_liq:
        down = -pnl.open_pnl / net_liq
        flags.append(
            Flag(
                "drawdown",
                "breach",
                f"Down {_money(pnl.open_pnl)} — {_pct(down)} of net liq, "
                f"{down / PLAN.max_loss:.1f}× the 2% Tom allows a whole trade to lose.",
            )
        )

    # ---- stop -----------------------------------------------------------
    stop_cost = stop_progress = None
    if book.stop_multiple is not None and credit > ZERO and not has_future:
        stop_cost = book.stop_multiple * credit
        if pnl.pct_of_credit is not None:
            down = -pnl.pct_of_credit if pnl.pct_of_credit < ZERO else ZERO
            stop_progress = _tidy(down / (book.stop_multiple - _ONE))
            if stop_progress >= _ONE:
                flags.append(
                    Flag(
                        "stop",
                        "breach",
                        f"Past Tom's stop: it costs {_money(pnl.cost_to_close or ZERO)} to close against a "
                        f"{_money(stop_cost)} stop ({book.stop_multiple:g}× the credit). He would be out.",
                    )
                )
            elif stop_progress >= Decimal("0.75"):
                flags.append(
                    Flag(
                        "stop",
                        "watch",
                        f"{_pct(stop_progress, 0)} of the way to Tom's stop. "
                        f"His exit: close if it costs {_money(stop_cost)} to buy back.",
                    )
                )
    elif book.key == "pmcc" and credit < ZERO and pnl.open_pnl is not None:
        # §9.2: "close entire trade if loss on longs minus CC gains exceeds
        # 30%". The calls already sold and closed count, so they come in.
        limit = PMCC_STOP * -credit
        result = pnl.open_pnl + pos.banked
        stop_progress = _tidy((-result if result < ZERO else ZERO) / limit) if limit else None
        if stop_progress is not None and stop_progress >= _ONE:
            flags.append(
                Flag(
                    "stop",
                    "breach",
                    f"Down {_money(result)} after the calls already sold — over 30% of the "
                    f"{_money(-credit)} long call. Tom closes the whole PMCC there.",
                )
            )
        elif stop_progress is not None and stop_progress >= Decimal("0.75"):
            flags.append(
                Flag("stop", "watch", f"{_pct(stop_progress, 0)} of the way to Tom's 30% PMCC stop.")
            )

    # ---- target ---------------------------------------------------------
    target = book.profit_target if credit > ZERO else None
    target_progress = None
    if target is not None and pnl.pct_of_credit is not None:
        target_progress = pnl.pct_of_credit / target
        if target_progress >= _ONE:
            flags.append(
                Flag(
                    "target",
                    "good",
                    f"At Tom's {_pct(target, 0)} target ({_pct(pnl.pct_of_credit, 0)} of the credit). "
                    "He takes it and redeploys.",
                )
            )

    # ---- time -----------------------------------------------------------
    dte = pos.risk.dte
    if book.key == "strangle" and dte is not None and 0 <= dte <= 21:
        flags.append(Flag("dte", "watch", f"{dte} DTE. Tom's strangle videos exit at 21 DTE."))

    # ---- rolls ----------------------------------------------------------
    if s.roll_count and book.key not in ("pmcc", "covered", "11x", "11x_spec"):
        flags.append(
            Flag(
                "roll",
                "watch",
                f"Rolled {s.roll_count}×. Tom doesn't roll losers; he calls it "
                '"bananas" and takes the stop.',
            )
        )

    # ---- adding ---------------------------------------------------------
    for add in adds:
        flags.append(Flag("add", "breach", add.text))

    # ---- regime ---------------------------------------------------------
    verdict = fit(bias, regime, book)
    if verdict == "against" and regime is not None:
        flags.append(
            Flag(
                "regime",
                "watch",
                f"{bias.capitalize()} trade in a {regime.label.lower()} {regime.product} chart "
                f"({regime.bullish} of 4 bullish tests).",
            )
        )
    if book.key == "naked_put" and regime is not None and regime.put_zone.startswith("Do not"):
        flags.append(Flag("zone", "info", f"{regime.product}: {regime.put_zone.lower()}."))

    # ---- outside the plan -------------------------------------------------
    if book.tier == "outside" and not any(f.code == "size" and book.key == "future" for f in flags):
        flags.append(Flag("plan", "info", f"{book.name}: {book.source.rstrip('.')}."))

    severity = max(({"breach": 3, "watch": 2, "info": 1, "good": 1}[f.level] for f in flags), default=0)
    label = pos.name or f"{s.underlying} {s.strategy_type.value}"
    # In a sentence the user's own name for a row needs its product beside it:
    # "Strangle" alone could be any of three.
    title = (
        label
        if not pos.name or product_root(s.underlying) in pos.name.upper()
        else f"{label} ({s.underlying})"
    )
    debit = -credit if credit < ZERO else ZERO
    return PositionCheck(
        id=s.id,
        label=label,
        title=title,
        underlying=s.underlying,
        product=product_root(s.underlying),
        structure=s.strategy_type.value,
        playbook=book.key,
        playbook_name=book.name,
        tier=book.tier,
        bucket=_bucket_of(book),
        lean=lean,
        opened=s.opened_at.date(),
        dte=dte,
        dte_at_entry=s.front_entry_dte,
        rolls=s.roll_count,
        credit=credit,
        open_pnl=pnl.open_pnl,
        pct_of_credit=pnl.pct_of_credit,
        buying_power=s.buying_power_used,
        bp_share=_share(s.buying_power_used, net_liq),
        capital=max(s.buying_power_used or ZERO, debit),
        loss_at_stop=loss_at_stop,
        loss_at_stop_share=size_share,
        size_cap=cap,
        size_basis=basis,
        stop_multiple=book.stop_multiple,
        stop_cost=stop_cost,
        cost_to_close=pnl.cost_to_close,
        stop_progress=stop_progress,
        target=target,
        target_progress=target_progress,
        regime=regime.label if regime else None,
        fit=verdict,
        flags=flags,
        severity=severity,
        source=book.source,
        analog=book.analog,
    )


# --------------------------------------------------------------------------
# The book's dials
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gauge:
    key: str
    label: str
    value: Decimal | None
    display: str
    target: str
    low: Decimal | None
    high: Decimal | None
    scale_max: Decimal
    status: str  # ok | watch | breach | unknown
    text: str


def _bp_gauge(bp_used: Decimal | None, net_liq: Decimal | None) -> Gauge:
    share = _share(bp_used, net_liq)
    lo, hi = PLAN.bp_target
    if share is None:
        return Gauge(
            "bp", "Buying power used", None, "—", "40–50%", lo, hi, _ONE, "unknown", "Not measured yet."
        )
    if share <= hi:
        status = "ok"
        text = (
            "Inside Tom's 40–50% target."
            if share >= lo
            else f"Under Tom's 40–50% range: room for about {_money((lo - share) * (net_liq or ZERO))} more."
        )
    elif share <= PLAN.bp_elevated:
        status, text = "watch", "Elevated (50–60%). Tom: monitor closely, limited new trades."
    elif share <= PLAN.bp_alert:
        status, text = "breach", "Alert (60–85%). Tom: no new trades until back under 60%."
    else:
        status, text = "breach", "Critical (over 85%). Tom: reduce buying power now."
    return Gauge("bp", "Buying power used", share, _pct(share), "40–50%", lo, hi, _ONE, status, text)


def _delta_gauge(bwd: Decimal | None, net_liq: Decimal | None) -> Gauge:
    if bwd is None or not net_liq:
        return Gauge(
            "delta",
            "Beta-weighted delta",
            None,
            "—",
            "±0.2% of net liq",
            None,
            None,
            _ONE,
            "unknown",
            "Not every leg is quoted, so the book's delta is unknown.",
        )
    limit = PLAN.delta_share * net_liq
    ratio = abs(bwd) / limit if limit else None
    status = (
        "ok"
        if ratio is not None and ratio <= 1
        else "watch"
        if ratio is not None and ratio <= 2
        else "breach"
    )
    lean = "bullish" if bwd > 0 else "bearish" if bwd < 0 else "flat"
    text = (
        f"{bwd:+,.0f} SPY deltas, {lean}. Tom's limit is ±{limit:,.0f} (0.2% of net liq), "
        "neutral to slightly bullish."
    )
    return Gauge(
        "delta",
        "Beta-weighted delta",
        bwd,
        f"{bwd:+,.0f}",
        f"±{limit:,.0f}",
        -limit,
        limit,
        limit * 3,
        status,
        text,
    )


def _theta_gauge(theta: Decimal | None, net_liq: Decimal | None) -> Gauge:
    lo, hi = PLAN.theta_target
    share = _share(theta, net_liq)
    if theta is None or share is None or not net_liq:
        return Gauge(
            "theta",
            "Theta per day",
            None,
            "—",
            "0.3–0.4% of net liq",
            lo,
            hi,
            Decimal("0.008"),
            "unknown",
            "Not measured yet.",
        )
    status = "ok" if share >= lo else "watch" if share >= lo * Decimal("0.66") else "breach"
    text = (
        f"${theta:,.0f} a day is {_pct(share, 2)} of net liq. Tom wants at least "
        f"{_pct(lo, 1)}–{_pct(hi, 1)} (${lo * net_liq:,.0f}–${hi * net_liq:,.0f})."
    )
    return Gauge(
        "theta",
        "Theta per day",
        share,
        f"${theta:,.0f}",
        f"${lo * net_liq:,.0f}–${hi * net_liq:,.0f}",
        lo,
        hi,
        Decimal("0.008"),
        status,
        text,
    )


def _vega_gauge(vega: Decimal | None, theta: Decimal | None) -> Gauge:
    if vega is None or theta is None or theta <= ZERO:
        return Gauge(
            "vega",
            "Vega against theta",
            None,
            "—",
            "≤ 1.5× theta",
            None,
            PLAN.vega_to_theta,
            Decimal(6),
            "unknown",
            "Needs both vega and a positive theta.",
        )
    ratio = abs(vega) / theta
    status = "ok" if ratio <= PLAN.vega_to_theta else "watch" if ratio <= 3 else "breach"
    text = (
        f"A 1-point rise in volatility moves the book {_money(vega)}, {ratio:.1f}× a day's theta. "
        f"Tom keeps it at 1–1.5×."
    )
    return Gauge(
        "vega",
        "Vega against theta",
        ratio,
        f"{ratio:.1f}×",
        "≤ 1.5×",
        None,
        PLAN.vega_to_theta,
        Decimal(6),
        status,
        text,
    )


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


@dataclass(slots=True)
class HistoryRule:
    key: str
    title: str
    rule: str
    status: str  # ok | watch | breach | info
    headline: str
    kept_label: str
    broken_label: str
    kept: int = 0
    broken: int = 0
    pnl_kept: Decimal = ZERO
    pnl_broken: Decimal = ZERO
    win_rate_kept: float | None = None
    win_rate_broken: float | None = None
    note: str = ""


def _stats(trades: Sequence[Strategy]) -> tuple[int, Decimal, float | None]:
    n = len(trades)
    pnl = sum((t.realized_pnl for t in trades), ZERO)
    wins = sum(1 for t in trades if t.realized_pnl > ZERO)
    return n, pnl, (wins / n if n else None)


def _split_rule(
    key: str,
    title: str,
    rule: str,
    kept: Sequence[Strategy],
    broken: Sequence[Strategy],
    *,
    kept_label: str,
    broken_label: str,
    headline: str,
    note: str = "",
) -> HistoryRule:
    nk, pk, wk = _stats(kept)
    nb, pb, wb = _stats(broken)
    if nb == 0:
        status = "ok"
    elif pb < ZERO and (nk == 0 or pb / nb < (pk / nk if nk else ZERO)):
        status = "breach"
    else:
        status = "watch"
    return HistoryRule(
        key=key,
        title=title,
        rule=rule,
        status=status,
        headline=headline,
        kept_label=kept_label,
        broken_label=broken_label,
        kept=nk,
        broken=nb,
        pnl_kept=pk,
        pnl_broken=pb,
        win_rate_kept=wk,
        win_rate_broken=wb,
        note=note,
    )


def _net_liq_on(day: date, history: Mapping[date, Decimal], fallback: Decimal | None) -> Decimal | None:
    if history:
        days = sorted(history)
        i = bisect_left(days, day + timedelta(days=1)) - 1
        if i >= 0:
            return history[days[i]]
        return history[days[0]]
    return fallback


def history_rules(
    strategies: Sequence[Strategy],
    *,
    series: Mapping[str, Series],
    adds: Sequence[AddToLoser],
    net_liq: Decimal | None,
    net_liq_history: Mapping[date, Decimal],
    today: date,
) -> tuple[list[HistoryRule], dict[str, object]]:
    done = closed_strategies(strategies)
    out: list[HistoryRule] = []
    facts: dict[str, object] = {"trades": len(done)}
    if not done:
        return out, facts

    # 1. Size: no trade loses more than 2% of the account.
    big, rest = [], []
    for s in done:
        nlv = _net_liq_on(s.opened_at.date(), net_liq_history, net_liq)
        if nlv and s.realized_pnl < ZERO and -s.realized_pnl > PLAN.max_loss * nlv:
            big.append(s)
        else:
            rest.append(s)
    gross_loss = -sum((s.realized_pnl for s in done if s.realized_pnl < ZERO), ZERO)
    big_loss = -sum((s.realized_pnl for s in big), ZERO)
    out.append(
        _split_rule(
            "max_loss",
            "Never lose more than 2% on one trade",
            "Plan §3.3",
            rest,
            big,
            kept_label="Within 2%",
            broken_label="Lost more than 2%",
            headline=(
                f"{len(big)} trades lost more than 2% of the account — {_money(big_loss)}, "
                f"{_pct(big_loss / gross_loss, 0) if gross_loss else '0%'} of every dollar you have lost."
                if big
                else "No trade has lost more than 2% of the account."
            ),
            note="Measured against the account's value on the day each trade opened.",
        )
    )

    # 2. Stops: a loser closes at Tom's stop, not past it.
    within, past = [], []
    beyond = ZERO
    for s in done:
        book = playbook_for(s)
        credit = max_profit_at_close(s)
        if book.stop_multiple is None or credit is None or credit <= ZERO or s.realized_pnl >= ZERO:
            continue
        allowed = (book.stop_multiple - _ONE) * credit
        loss = -s.realized_pnl
        if loss > allowed * Decimal("1.1"):
            past.append(s)
            beyond += loss - allowed
        else:
            within.append(s)
    out.append(
        _split_rule(
            "stop",
            "Take the stop",
            "Strangles 2.5×, naked puts 3×, spreads 2.5× the credit",
            within,
            past,
            kept_label="Closed at or before the stop",
            broken_label="Ran past the stop",
            headline=(
                f"{len(past)} losers ran past Tom's stop. The part beyond the stop cost {_money(beyond)}."
                if past
                else "Every losing credit trade closed at or before Tom's stop."
            ),
            note="Loss beyond the stop assumes a fill exactly at the stop, so it is an upper bound on "
            "what the stop would have saved.",
        )
    )
    facts["loss_beyond_stops"] = beyond
    # Both sides are losers by construction, so a win rate would read 0% twice.
    out[-1].win_rate_kept = out[-1].win_rate_broken = None

    # 3. Rolling.
    rollable = [s for s in done if playbook_for(s).key not in ("pmcc", "covered", "11x", "11x_spec")]
    rolled = [s for s in rollable if s.roll_count > 0]
    unrolled = [s for s in rollable if s.roll_count == 0]
    _, rolled_pnl, _ = _stats(rolled)
    out.append(
        _split_rule(
            "rolling",
            "Don't roll losers",
            '"Rolling is bananas — just a loss you hope comes back"',
            unrolled,
            rolled,
            kept_label="Never rolled",
            broken_label="Rolled",
            headline=(
                f"Rolled trades: {len(rolled)}, "
                f"{'+' if rolled_pnl >= 0 else '-'}{_money(rolled_pnl)} in total."
                if rolled
                else "You have not rolled a trade."
            ),
        )
    )

    # 4. Adding to a loser.
    added_ids = {a.strategy_id for a in adds}
    added = [s for s in done if s.id in added_ids]
    clean = [s for s in done if s.id not in added_ids]
    _, added_pnl, _ = _stats(added)
    out.append(
        _split_rule(
            "adding",
            "Never add to a losing position",
            "Plan §4",
            clean,
            added,
            kept_label="Fresh trades",
            broken_label="Added to a loser",
            headline=(
                f"{len(added)} trades were added while an earlier one in the same direction was losing: "
                f"{'+' if added_pnl >= 0 else '-'}{_money(added_pnl)}."
                if added
                else "No trade was added on top of a losing one."
            ),
            note="Losing is read off the product's daily chart (over 0.5% against the earlier entry), "
            "so it is an estimate. Same-day adds are not judged.",
        )
    )

    # 5. Profit target: take it, don't ride it.
    taken, ridden = [], []
    for s in done:
        book = playbook_for(s)
        credit = max_profit_at_close(s)
        if book.profit_target is None or credit is None or credit <= ZERO or s.realized_pnl <= ZERO:
            continue
        if s.realized_pnl / credit > book.profit_target + Decimal("0.15"):
            ridden.append(s)
        else:
            taken.append(s)
    out.append(
        _split_rule(
            "target",
            "Take profit at the target",
            "50% on strangles and naked puts; 90% on an 11x",
            taken,
            ridden,
            kept_label="Taken at the target",
            broken_label="Held past it",
            headline=(
                f"{len(ridden)} of {len(taken) + len(ridden)} winners were held past Tom's target."
                if ridden
                else "Winners were taken at Tom's target."
            ),
            note="Holding past the target often banks more on the trades that work. Tom takes the target "
            "anyway: it frees buying power and gets out before the risk comes back.",
        )
    )
    out[-1].status = "watch" if ridden else "ok"
    # Both sides are winners by construction.
    out[-1].win_rate_kept = out[-1].win_rate_broken = None

    # 6. Trade with the regime.
    with_, against = [], []
    for s in done:
        book = playbook_for(s)
        chart = series.get(product_root(s.underlying))
        if chart is None:
            continue
        i = chart.last_before(s.opened_at.date())
        regime = chart.regime_at(i) if i is not None else None
        bias = (
            book.bias
            if book.bias in ("bullish", "bearish", "neutral", "hedge")
            else lean_of(s.legs, live=False)
        )
        verdict = fit(bias, regime, book)
        if verdict == "with":
            with_.append(s)
        elif verdict == "against":
            against.append(s)
    out.append(
        _split_rule(
            "regime",
            "Trade with the regime",
            "Plan §7: the chart decides the strategy",
            with_,
            against,
            kept_label="With the chart",
            broken_label="Against the chart",
            headline=(
                f"{len(against)} trades went against their own chart's regime at entry."
                if against
                else "Every judged trade went with its chart's regime."
            ),
            note="Regime is Tom's four tests on the product's daily chart, the day before the trade.",
        )
    )

    # 7. Staying inside Tom's playbooks.
    his = [s for s in done if playbook_for(s).tier != "outside"]
    not_his = [s for s in done if playbook_for(s).tier == "outside"]
    _, outside_pnl, _ = _stats(not_his)
    out.append(
        _split_rule(
            "playbooks",
            "Trade only Tom's playbooks",
            '"Keep it simple. Simplify to multiply!" (Plan §4)',
            his,
            not_his,
            kept_label="His playbooks",
            broken_label="Outside them",
            headline=(
                f"{len(not_his)} trades were shapes Tom doesn't trade — outright futures, naked calls, "
                f"custom combinations: {'+' if outside_pnl >= 0 else '-'}{_money(outside_pnl)}."
                if not_his
                else "Every trade was one of Tom's playbooks."
            ),
        )
    )

    # 8. Entry DTE.
    inside, outside = [], []
    for s in done:
        book = playbook_for(s)
        dte = s.dte_at_entry if s.dte_at_entry is not None else s.front_entry_dte
        if book.entry_dte is None or dte is None:
            continue
        lo, hi = book.entry_dte
        (inside if lo <= dte <= hi else outside).append(s)
    out.append(
        _split_rule(
            "entry_dte",
            "Enter at Tom's DTE",
            "Strangles 45–90, naked puts 30–60, 11x 45–65",
            inside,
            outside,
            kept_label="Inside Tom's window",
            broken_label="Outside it",
            headline=(
                f"{len(outside)} of {len(inside) + len(outside)} premium trades opened outside "
                "Tom's DTE window."
            ),
        )
    )
    out[-1].status = "info" if not outside else out[-1].status

    # Fees and income: facts for the page rather than rules with two sides.
    fees = -sum((s.fees for s in done), ZERO)
    gross = sum((s.realized_pnl for s in done), ZERO)
    # From the first trade ever opened: that is when the account started
    # working, and a month with nothing closed in it is still a month.
    first = min(s.opened_at.date() for s in strategies)
    months = max(Decimal((today - first).days) / Decimal("30.4375"), _ONE)
    facts.update(
        {
            "fees": fees,
            "realized": gross,
            "months": months,
            "per_month": gross / months,
            "first_trade": first,
        }
    )
    return out, facts


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fix:
    title: str
    detail: str
    section: str


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    label: str
    value: str
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Bucket:
    key: str
    label: str
    tom: Decimal | None
    yours: Decimal | None
    buying_power: Decimal


@dataclass(slots=True)
class Score:
    kept: int
    judged: int
    items: list[ChecklistItem]


@dataclass(slots=True)
class TomReport:
    as_of: datetime
    net_liq: Decimal | None
    score: Score
    fixes: list[Fix]
    gauges: list[Gauge]
    market: Regime | None
    products: list[Regime]
    positions: list[PositionCheck]
    strategy_bp: list[dict[str, object]]
    allocation: list[Bucket]
    history: list[HistoryRule]
    history_facts: dict[str, object]
    sizing: list[dict[str, str]]
    checklist: list[ChecklistItem]
    reduction: list[dict[str, object]]
    price_errors: dict[str, str]
    sources: list[str]


def _bucket_of(book: Playbook) -> str:
    if book.key in ("11x", "11x_spec") and book.tier == "core":
        return "11x"
    if book.key in ("pmcc", "covered"):
        return "PMCC"
    if book.tier == "hedge":
        return "Hedge"
    if book.tier == "outside":
        return "Outside"
    return "Spec"


def analyse(
    *,
    as_of: datetime,
    today: date,
    net_liq: Decimal | None,
    bp_used: Decimal | None,
    theta: Decimal | None,
    vega: Decimal | None,
    beta_weighted_delta: Decimal | None,
    realized_ytd: Decimal | None,
    open_pnl: Decimal | None,
    positions: Sequence[OpenPosition],
    strategies: Sequence[Strategy],
    bars: Mapping[str, Sequence[Bar]],
    net_liq_history: Mapping[date, Decimal] | None = None,
    price_errors: Mapping[str, str] | None = None,
) -> TomReport:
    series = {product: regime_series(product, b) for product, b in bars.items() if len(b) >= 60}
    market_chart = series.get("SPY")
    market = market_chart.latest() if market_chart else None

    adds = adds_to_losers(strategies, series)
    adds_by_id: dict[str, list[AddToLoser]] = defaultdict(list)
    for a in adds:
        adds_by_id[a.strategy_id].append(a)

    # Open positions. A merged row (a strategy the user named) carries the
    # adds of every trade inside it.
    checks: list[PositionCheck] = []
    for pos in positions:
        product = product_root(pos.strategy.underlying)
        chart = series.get(product)
        regime = chart.latest() if chart else None
        members = pos.member_ids or (pos.strategy.id,)
        mine = [a for sid in members for a in adds_by_id.get(sid, [])]
        checks.append(check_position(pos, net_liq=net_liq, regime=regime, adds=mine))
    checks.sort(key=lambda c: (c.severity, c.loss_at_stop_share or ZERO), reverse=True)

    products = sorted(
        {c.product for c in checks if c.product in series},
        key=lambda p: -sum((c.buying_power or ZERO) for c in checks if c.product == p),
    )
    product_regimes = [r for r in (series[p].latest() for p in products) if r is not None]

    # Buying power by playbook against Tom's 20%-of-net-liq cap per strategy.
    by_book: dict[str, Decimal] = defaultdict(lambda: ZERO)
    names: dict[str, str] = {}
    for c in checks:
        by_book[c.playbook] += c.buying_power or ZERO
        names[c.playbook] = c.playbook_name
    cap = PLAN.strategy_cap * net_liq if net_liq else None
    strategy_bp = [
        {
            "playbook": k,
            "name": names[k],
            "buying_power": v,
            "share": _share(v, net_liq),
            "cap": cap,
            "over": bool(cap and v > cap),
        }
        for k, v in sorted(by_book.items(), key=lambda kv: -kv[1])
        if v > ZERO
    ]

    # Allocation: Tom's 30/40/30 against where the buying power sits now.
    total_bp = sum((c.capital for c in checks), ZERO)
    buckets: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for c in checks:
        buckets[c.bucket] += c.capital
    tom_alloc = dict(PLAN.allocation)
    labels = {
        "11x": "11x Bear Trap",
        "PMCC": "Dynamic PMCC",
        "Spec": "Spec trades",
        "Hedge": "Hedges",
        "Outside": "Not in Tom's plan",
    }
    allocation = [
        Bucket(
            k,
            labels[k],
            tom_alloc.get(k),
            _share(buckets.get(k, ZERO), total_bp) if total_bp else None,
            buckets.get(k, ZERO),
        )
        for k in ("11x", "PMCC", "Spec", "Hedge", "Outside")
    ]

    gauges = [
        _bp_gauge(bp_used, net_liq),
        _delta_gauge(beta_weighted_delta, net_liq),
        _theta_gauge(theta, net_liq),
        _vega_gauge(vega, theta),
    ]

    history, facts = history_rules(
        strategies,
        series=series,
        adds=adds,
        net_liq=net_liq,
        net_liq_history=net_liq_history or {},
        today=today,
    )

    # ---- today's scorecard ------------------------------------------------
    def count(code: str, level: str = "breach") -> list[PositionCheck]:
        return [c for c in checks if any(f.code == code and f.level == level for f in c.flags)]

    oversize = count("size")
    past_stop = count("stop")
    at_target = [c for c in checks if any(f.code == "target" for f in c.flags)]
    added = count("add")
    against = [c for c in checks if c.fit == "against"]
    rolled_open = [c for c in checks if c.rolls and c.playbook not in ("pmcc", "covered")]
    outside = [c for c in checks if c.tier == "outside"]
    over_strategies = [s for s in strategy_bp if s["over"]]
    core_share = (buckets.get("11x", ZERO) + buckets.get("PMCC", ZERO)) / total_bp if total_bp else None

    def names_of(items: Sequence[PositionCheck], limit: int = 3) -> str:
        shown = ", ".join(c.title for c in items[:limit])
        return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")

    items: list[ChecklistItem] = [ChecklistItem(g.label, g.display, g.status, g.text) for g in gauges]
    items += [
        ChecklistItem(
            "Every trade within Tom's size cap",
            f"{len(oversize)} over" if oversize else "All within",
            "breach" if oversize else "ok",
            names_of(oversize) if oversize else "Each open trade's loss at Tom's exit is under his cap.",
        ),
        ChecklistItem(
            "Nothing past Tom's stop",
            f"{len(past_stop)} past" if past_stop else "None",
            "breach" if past_stop else "ok",
            names_of(past_stop) if past_stop else "No open trade has reached Tom's stop.",
        ),
        ChecklistItem(
            "No strategy over 20% of net liq",
            f"{len(over_strategies)} over" if over_strategies else "All within",
            "breach" if over_strategies else "ok",
            ", ".join(str(s["name"]) for s in over_strategies)
            if over_strategies
            else "Tom: never more than 40% of the allowed buying power in one strategy.",
        ),
        ChecklistItem(
            "Never added to a loser",
            f"{len(added)} positions" if added else "Clean",
            "breach" if added else "ok",
            names_of(added) if added else "No open position was added to while it was losing.",
        ),
        ChecklistItem(
            "No rolled positions",
            f"{len(rolled_open)} rolled" if rolled_open else "None",
            "watch" if rolled_open else "ok",
            names_of(rolled_open) if rolled_open else "Nothing open has been rolled.",
        ),
        ChecklistItem(
            "Trades fit their chart",
            f"{len(against)} against" if against else "All fit",
            "watch" if against else "ok",
            names_of(against) if against else "No open trade fights its own chart's regime.",
        ),
        ChecklistItem(
            "Only Tom's strategies",
            f"{len(outside)} outside" if outside else "All his",
            "watch" if outside else "ok",
            names_of(outside) if outside else "Every open trade is one of Tom's playbooks.",
        ),
        ChecklistItem(
            "Core trades carry the book",
            _pct(core_share, 0) if core_share is not None else "—",
            "unknown" if core_share is None else "ok" if core_share >= Decimal("0.5") else "watch",
            "Tom keeps 70% in core trades (11x and Dynamic PMCC) and 30% in spec trades.",
        ),
    ]
    judged = [i for i in items if i.status != "unknown"]
    score = Score(kept=sum(1 for i in judged if i.status == "ok"), judged=len(judged), items=items)

    # ---- the three biggest gaps ------------------------------------------------
    # In the order money is at stake: a position already down more than a
    # whole trade may lose, then size, then what the book as a whole is doing,
    # then the habits the history shows.
    fixes: list[Fix] = []
    hurt = sorted(
        (c for c in checks if any(f.code == "drawdown" for f in c.flags)), key=lambda c: c.open_pnl or ZERO
    )
    if hurt:
        worst = hurt[0]
        fixes.append(
            Fix(
                f"{worst.title} is down {_money(worst.open_pnl or ZERO)}",
                next(f.text for f in worst.flags if f.code == "drawdown")
                + (
                    " " + next(f.text for f in worst.flags if f.code == "size")
                    if any(f.code == "size" and f.level == "breach" for f in worst.flags)
                    else ""
                ),
                "positions",
            )
        )
    if past_stop:
        fixes.append(
            Fix(
                f"{len(past_stop)} past Tom's stop",
                next(f.text for f in past_stop[0].flags if f.code == "stop"),
                "positions",
            )
        )
    sized = [c for c in oversize if c.loss_at_stop_share is not None]
    if sized:
        worst = max(sized, key=lambda c: (c.loss_at_stop_share or ZERO) / c.size_cap)
        others = len(sized) - 1
        fixes.append(
            Fix(
                f"{len(sized)} positions are bigger than Tom's cap"
                if others
                else f"{worst.title} is too big",
                f"Largest: {worst.title} could lose {_money(worst.loss_at_stop or ZERO)} at Tom's exit — "
                f"{_pct(worst.loss_at_stop_share or ZERO)} of net liq against his {_pct(worst.size_cap, 1)}. "
                f"At your size Tom's whole-trade limit is {_money(PLAN.max_loss * (net_liq or ZERO))}.",
                "positions",
            )
        )
    if added:
        fixes.append(
            Fix(
                "Added to a losing position",
                next(f.text for f in added[0].flags if f.code == "add"),
                "positions",
            )
        )
    for item in over_strategies:
        fixes.append(
            Fix(
                f"{item['name']} trades use {_pct(item['share'] or ZERO, 0)} of net liq",
                f"Tom keeps one strategy under {_pct(PLAN.strategy_cap, 0)} of net liq (40% of the 50% he "
                "allows in total).",
                "book",
            )
        )
    for g in gauges:
        if g.status == "breach":
            fixes.append(Fix(f"{g.label}: {g.display} (Tom: {g.target})", g.text, "book"))
    for rule in history:
        if rule.status == "breach" and rule.pnl_broken < ZERO:
            fixes.append(Fix(rule.title, rule.headline, "history"))
    if core_share is not None and core_share < Decimal("0.5"):
        fixes.append(
            Fix(
                f"Core trades are {_pct(core_share, 0)} of your capital",
                "Tom's income comes from a campaign of 11x trades on the S&P and Dynamic PMCCs (70%); spec "
                "trades like futures strangles are the other 30%.",
                "plan",
            )
        )
    fixes = fixes[:3]

    # ---- Tom's plan at this account's size -----------------------------------
    sizing: list[dict[str, str]] = []
    if net_liq:
        lo, hi = PLAN.monthly_income
        t_lo, t_hi = PLAN.theta_target
        b_lo, b_hi = PLAN.bp_target
        sizing = [
            {
                "label": "Max loss on one trade",
                "value": _money(PLAN.max_loss * net_liq),
                "note": "2% of net liq",
            },
            {
                "label": "Max loss on a spec 11x",
                "value": _money(PLAN.max_loss_spec_11x * net_liq),
                "note": "1% of net liq",
            },
            {
                "label": "Strangle credit, at most",
                "value": _money(Decimal("0.01") * net_liq),
                "note": "1% of net liq, stop at 2.5×",
            },
            {
                "label": "Buying power to use",
                "value": f"{_money(b_lo * net_liq)}–{_money(b_hi * net_liq)}",
                "note": "40–50% of net liq",
            },
            {
                "label": "Per strategy, at most",
                "value": _money(PLAN.strategy_cap * net_liq),
                "note": "40% of the allowed buying power",
            },
            {
                "label": "Theta to aim for",
                "value": f"{_money(t_lo * net_liq)}–{_money(t_hi * net_liq)} a day",
                "note": "0.3–0.4% of net liq",
            },
            {
                "label": "Delta limit",
                "value": f"±{PLAN.delta_share * net_liq:,.0f}",
                "note": "SPY deltas, 0.2% of net liq",
            },
            {
                "label": "Monthly income goal",
                "value": f"{_money(lo * net_liq)}–{_money(hi * net_liq)}",
                "note": "2–3% of net liq",
            },
        ]

    # ---- Tom's nightly review, filled in --------------------------------------
    def in_trouble(c: PositionCheck) -> bool:
        return (c.stop_progress is not None and c.stop_progress >= _ONE) or any(
            f.code == "drawdown" for f in c.flags
        )

    near_stop = [
        c
        for c in checks
        if in_trouble(c) or (c.stop_progress is not None and c.stop_progress >= Decimal("0.75"))
    ]
    bp_share = _share(bp_used, net_liq)
    if bp_share is None:
        room = ChecklistItem("Does buying power allow new trades?", "—", "unknown")
    elif bp_share <= PLAN.bp_target[1]:
        room = ChecklistItem("Does buying power allow new trades?", "Yes", "ok", "Fill core trades first.")
    elif bp_share <= PLAN.bp_elevated:
        room = ChecklistItem(
            "Does buying power allow new trades?", "Limited", "watch", "50–60%: limited new trades."
        )
    else:
        room = ChecklistItem(
            "Does buying power allow new trades?", "No", "breach", "Over 60%: no new trades until back under."
        )
    checklist = [
        ChecklistItem(
            "Check delta, theta, buying power, net liq, realized",
            f"{'—' if beta_weighted_delta is None else f'{beta_weighted_delta:+,.0f}'} Δ · "
            f"{'—' if theta is None else f'${theta:,.0f}'} θ · "
            f"{_pct(bp_share, 0) if bp_share is not None else '—'} BP",
            "info",
            (f"Net liq {_money(net_liq)}" if net_liq else "")
            + (
                f" · realized this year {'+' if realized_ytd >= 0 else '-'}{_money(realized_ytd)}"
                if realized_ytd is not None
                else ""
            )
            + (f" · open {'+' if open_pnl >= 0 else '-'}{_money(open_pnl)}" if open_pnl is not None else ""),
        ),
        ChecklistItem(
            "Any trades to take off at their profit target?",
            f"{len(at_target)}" if at_target else "None",
            "watch" if at_target else "ok",
            names_of(at_target) if at_target else "",
        ),
        ChecklistItem(
            "Anything in trouble or near its stop?",
            f"{len(near_stop)}" if near_stop else "None",
            "breach" if any(in_trouble(c) for c in near_stop) else "watch" if near_stop else "ok",
            names_of(near_stop) if near_stop else "",
        ),
        room,
        ChecklistItem(
            "Where is the market regime?",
            market.label if market else "—",
            "info" if market else "unknown",
            ("Tom focuses on: " + ", ".join(market.focus)) if market else "No chart for SPY right now.",
        ),
        ChecklistItem(
            "Core trades filled first?",
            "Yes" if buckets.get("11x") and buckets.get("PMCC") else "No",
            "ok" if buckets.get("11x") and buckets.get("PMCC") else "watch",
            "Tom places an 11x every two weeks and keeps Dynamic PMCCs running before any spec trade.",
        ),
    ]

    # ---- §3.2: how Tom brings buying power down, in his order -----------------
    reduction: list[dict[str, object]] = []
    if bp_share is not None and bp_share > PLAN.bp_target[1]:
        winners = sorted(
            (c for c in checks if (c.open_pnl or ZERO) > ZERO), key=lambda c: -(c.buying_power or ZERO)
        )
        elevens = [c for c in checks if c.playbook in ("11x", "11x_spec")]
        small_losers = sorted(
            (c for c in checks if (c.open_pnl or ZERO) < ZERO), key=lambda c: -(c.open_pnl or ZERO)
        )
        reduction = [
            {"step": "Close winning trades", "positions": [c.title for c in winners[:4]]},
            {"step": "Close the short puts of an 11x", "positions": [c.title for c in elevens[:4]]},
            {"step": "Close smaller losers", "positions": [c.title for c in small_losers[:4]]},
            {"step": "Add a hedge with a long put", "positions": []},
        ]

    return TomReport(
        as_of=as_of,
        net_liq=net_liq,
        score=score,
        fixes=fixes,
        gauges=gauges,
        market=market,
        products=product_regimes,
        positions=checks,
        strategy_bp=strategy_bp,
        allocation=allocation,
        history=history,
        history_facts=facts,
        sizing=sizing,
        checklist=checklist,
        reduction=reduction,
        price_errors=dict(price_errors or {}),
        sources=[
            "Tom King, 2026 Trading Plan v4",
            "11x Bear Trap campaign and spec sheets; FB11x and BA11x",
            "Dynamic PMCC and Call Cannon sheets",
            "Tom King video summaries (strangle 21-DTE exit, rolling)",
            "Daily prices: Yahoo Finance (front-month continuous for futures)",
        ],
    )
