"""IV − HV: how much the market is paying for an expiry against what moves.

For one expiry of one product: the implied volatility tastytrade publishes
for that expiration, minus the volatility the product has actually realised
over the same number of days. A 45-day option is compared with 45 days of
moves, not with a fixed 30, so a weekly and a quarterly are each measured
against their own horizon.

Positive means the options price in more movement than has been happening —
premium is rich, the seller's edge. Negative means they price in less.
Expressed in volatility points as a fraction: 0.031 is "IV 3.1 points over HV".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

__all__ = ["ExpiryIV", "Gap", "expiry_iv", "gap", "realized_vol", "window_days"]

TRADING_DAYS = 252
MIN_RETURNS = 5


@dataclass(frozen=True, slots=True)
class ExpiryIV:
    """One expiration's implied volatility, as tastytrade's market metrics give it."""

    expiration: date
    iv: Decimal
    root: str = ""  # the option root, e.g. "OZS", "MS4E" — tells two chains apart


@dataclass(frozen=True, slots=True)
class Gap:
    expiration: date
    dte: int
    iv: Decimal
    hv: Decimal
    days: int  # trading days of returns the HV was measured over

    @property
    def value(self) -> Decimal:
        return self.iv - self.hv


def window_days(dte: int) -> int:
    """Calendar days to expiry as trading days of history, never fewer than five."""
    return max(MIN_RETURNS, round(dte * TRADING_DAYS / 365))


def realized_vol(closes: Sequence[float], days: int) -> Decimal | None:
    """Annualised close-to-close volatility over the last ``days`` daily returns."""
    if days < 2 or len(closes) < days + 1:
        return None
    window = closes[-(days + 1):]
    returns = [math.log(b / a) for a, b in zip(window, window[1:], strict=False) if a > 0 and b > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return Decimal(str(round(math.sqrt(var) * math.sqrt(TRADING_DAYS), 6)))


def expiry_iv(
    published: Iterable[ExpiryIV], expiration: date, leg_symbols: Sequence[str] = ()
) -> Decimal | None:
    """The IV for this expiration. When two chains share the date (an /MES
    weekly and its end-of-month), the one whose root is in the leg's own
    symbol wins; otherwise the first."""
    same = [e for e in published if e.expiration == expiration and e.iv > 0]
    if not same:
        return None
    for entry in same:
        if entry.root and any(entry.root in s for s in leg_symbols):
            return entry.iv
    return same[0].iv


def gap(
    published: Iterable[ExpiryIV],
    closes: Sequence[float],
    expiration: date,
    today: date,
    leg_symbols: Sequence[str] = (),
) -> Gap | None:
    """IV of this expiry against HV over its own horizon; None if either is unknown."""
    dte = (expiration - today).days
    if dte < 1:
        return None
    iv = expiry_iv(published, expiration, leg_symbols)
    days = window_days(dte)
    hv = realized_vol(closes, days)
    if iv is None or hv is None:
        return None
    return Gap(expiration=expiration, dte=dte, iv=iv, hv=hv, days=days)
