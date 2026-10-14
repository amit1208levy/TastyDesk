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


def test_ivhv_is_its_own_indicator() -> None:
    gaps = [{"product": "/MES", "expiration": "2026-11-27", "iv": "0.15", "hv": "0.106", "gap": "0.044"}]
    script = tradingview.ivhv_script(gaps, datetime(2026, 10, 8, 15, 45))
    assert 'indicator("Tasty Desk IV-HV"' in script and script.isascii()
    assert '"MES" =>' in script and "IV-HV 27 Nov: +4.4  (IV 15.0 / HV 10.6)" in script
    assert "IV-HV" not in tradingview.pine_script([_leg()], datetime(2026, 10, 8))
    # "box" is a Pine namespace; a variable of that name does not compile.
    assert " box " not in script and " box " not in tradingview.pine_script([_leg()], datetime(2026, 10, 8))


def test_combined_has_both() -> None:
    gaps = [{"product": "SLV", "expiration": "2026-10-12", "iv": "0.31", "hv": "0.26", "gap": "0.05"}]
    script = tradingview.pine_script([_leg()], datetime(2026, 10, 11), gaps, name=tradingview.COMBINED_NAME)
    assert 'indicator("Tasty Desk positions + IV-HV"' in script
    assert "add(54.5," in script and "IV-HV 12 Oct: +5.0" in script and script.isascii()


def test_lines_are_made_once_and_only_moved_after() -> None:
    script = tradingview.pine_script([_leg()], datetime(2026, 10, 14))
    made, moved = script.split("if barstate.islast", 1)
    assert "line.new(" in made and "label.new(" in made
    # On the last bar, every realtime tick: move, never delete or rebuild.
    assert "line.new(" not in moved and "label.new(" not in moved and ".delete()" not in moved


def test_a_line_break_in_a_name_stays_inside_the_pine_string() -> None:
    script = tradingview.pine_script(
        [_leg(in_strategies=[{"name": 'Bad\n"name'}])], datetime(2026, 10, 14)
    )
    assert '(Bad \\"name)"' in script
    assert not any(line.startswith('"name') for line in script.splitlines())
