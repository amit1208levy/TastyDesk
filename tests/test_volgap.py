"""IV − HV: an expiry's implied vol against the moves of the same horizon."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal as D

from tastydesk.core import volgap


def test_window_matches_the_horizon_in_trading_days() -> None:
    assert volgap.window_days(45) == 31
    assert volgap.window_days(3) == 5  # never fewer than five returns


def test_realized_vol_of_alternating_moves() -> None:
    # +1%, -1%, +1%... : daily stdev ~1%, so about 16% a year.
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    hv = volgap.realized_vol(closes, 20)
    assert hv is not None
    expected = math.log(1.01) * math.sqrt(20 / 19) * math.sqrt(252)
    assert abs(float(hv) - expected) < 1e-4


def test_two_chains_on_one_day_pick_the_legs_own() -> None:
    day = date(2026, 11, 27)
    published = [volgap.ExpiryIV(day, D("0.20"), "MS2A"), volgap.ExpiryIV(day, D("0.16"), "MS4E")]
    assert volgap.expiry_iv(published, day, ["./MESZ6MS4EX6 261127P7500"]) == D("0.16")
    assert volgap.expiry_iv(published, day) == D("0.20")


def test_gap_is_iv_minus_hv_and_none_when_unknown() -> None:
    closes = [100.0 + (i % 2) for i in range(60)]
    today = date(2026, 10, 8)
    expiry = date(2026, 11, 20)
    found = volgap.gap([volgap.ExpiryIV(expiry, D("0.25"))], closes, expiry, today)
    assert found is not None and found.value == found.iv - found.hv and found.dte == 43
    assert volgap.gap([], closes, expiry, today) is None
