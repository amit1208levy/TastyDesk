"""The positions as a TradingView indicator: one line per strike, keyed by root."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from tastydesk import tradingview


def _leg(**kw):
    base = {"product": "SLV", "underlying": "SLV", "side": "Short", "quantity": "1.0",
            "right": "C", "strike": "54.5", "expiration": "2026-10-12", "open_price": "1.18",
            "in_strategies": [{"name": "DMPCC"}]}
    base.update(kw)
    return base


def test_same_strike_legs_merge_into_one_line() -> None:
    found = tradingview.levels([_leg(), _leg(open_price="1.19")])
    assert found == {"SLV": [(Decimal("54.5"), "-2 54.5C  12 Oct  (DMPCC)", "#e5484d")]}


def test_futures_are_keyed_by_root_and_drawn_at_their_average_entry() -> None:
    fut = {"product": "/ZB", "underlying": "/ZBZ6", "side": "Long", "quantity": "1.0",
           "right": None, "strike": None, "open_price": "106.5", "in_strategies": ["Bull ZB"]}
    found = tradingview.levels([fut, {**fut, "open_price": "105.5"}])
    assert found["ZB"] == [(Decimal("106"), "+2 /ZBZ6 @ 106  (Bull ZB)", "#3e8ef7")]


def test_script_names_each_root() -> None:
    script = tradingview.pine_script([_leg()], datetime(2026, 10, 6, 16, 45))
    assert script.startswith("//@version=6")
    assert '"SLV" =>' in script and "add(54.5," in script
    assert script.isascii()
