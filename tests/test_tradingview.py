"""The positions as a TradingView indicator: one line per strike, keyed by root."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from tastydesk import tradingview


def _leg(**kw):
    base = {
        "product": "SLV",
        "underlying": "SLV",
        "side": "Short",
        "quantity": "1.0",
        "right": "C",
        "strike": "54.5",
        "expiration": "2026-10-12",
        "open_price": "1.18",
        "in_strategies": [{"name": "DMPCC"}],
    }
    base.update(kw)
    return base


def test_same_strike_legs_merge_into_one_line() -> None:
    found = tradingview.levels([_leg(), _leg(open_price="1.19")])
    assert found == {"SLV": [(Decimal("54.5"), "-2 54.5C  12 Oct  (DMPCC)", "#e5484d")]}


def test_futures_are_keyed_by_root_and_drawn_at_their_average_entry() -> None:
    fut = {
        "product": "/ZB",
        "underlying": "/ZBZ6",
        "side": "Long",
        "quantity": "1.0",
        "right": None,
        "strike": None,
        "open_price": "106.5",
        "in_strategies": ["Bull ZB"],
    }
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


def test_strangle_version_carries_toms_checks_and_the_short_strikes() -> None:
    put = _leg(right="P", strike="45", in_strategies=[])
    script = tradingview.pine_script(
        [_leg(), put], datetime(2026, 10, 14), name=tradingview.STRANGLE_NAME, strangle=True
    )
    assert 'indicator("Tasty Desk positions strangle"' in script and script.isascii()
    assert 'shortAt("C", 54.5)' in script and 'shortAt("P", 45.0)' in script
    assert "request.security(syminfo.tickerid, stf," in script and "km + atrs * kr" in script
    plain = tradingview.pine_script([_leg()], datetime(2026, 10, 14))
    assert "request.security" not in plain and 'shortAt("C"' not in plain


def test_legs_at_one_price_share_one_label() -> None:
    """Two 7500 puts of a /MES campaign, two weeks apart: two labels at one
    price printed on top of each other and one of them could not be seen."""
    legs = [
        {
            "trade_id": f"T{i}",
            "underlying": "/MESZ6",
            "product": "/MES",
            "symbol": f"./MESZ6MS4EX6{d}P7500",
            "side": "Long",
            "right": "P",
            "strike": "7500",
            "expiration": f"2026-{d[2:4]}-{d[4:6]}",
            "quantity": "1.0",
            "open_price": "54.75",
        }
        for i, d in enumerate(("261127", "261211"))
    ]
    out = tradingview.pine_script(legs, datetime(2026, 10, 20, 17, 0))
    adds = [line for line in out.splitlines() if "add(7500" in line]
    assert len(adds) == 1
    assert "27 Nov" in adds[0] and "11 Dec" in adds[0] and "\\n" in adds[0]


def test_positions_alone_and_toms_technicals_apart() -> None:
    """27 Oct 2026: the positions indicator draws only the positions; Tom's
    technicals are their own indicator, RSI in its own pane and the rest on price."""
    plain = tradingview.pine_script([_leg()], datetime(2026, 10, 27), tools=False)
    assert "ta.ema" not in plain and "ta.sar" not in plain and "add(54.5," in plain
    tom = tradingview.tom_script(datetime(2026, 10, 27, 20, 30))
    assert tom.startswith("//@version=6") and tom.isascii()
    assert 'indicator("Tom\'s technicals", overlay = false)' in tom
    assert "ta.rsi(close, 14)" in tom and "force_overlay = true" in tom and "ta.sar(0.02, 0.02, 0.2)" in tom
