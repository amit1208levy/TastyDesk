"""The open positions as a TradingView indicator.

TradingView has no API for drawing on a personal account's charts, and lines
drawn by hand belong to one chart layout and one symbol at a time. An
indicator does not: added once to a layout, it draws on whichever ticker that
chart shows, on every timeframe, and one save of the script updates every
chart it is on. So the positions are written into a Pine Script, keyed by the
symbol's root — "ZB" covers ZB1!, ZBZ2026 and every other /ZB contract, and
for a stock the root is the ticker itself — and Claude pastes that script into
the user's TradingView when asked or on schedule.

The script is generated whole, never edited by hand: a position that closed
simply is not in the next version.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

SCRIPT_NAME = "Tasty Desk positions"

_SHORT = "#e5484d"  # short options: the strikes that can hurt
_LONG = "#30a46c"  # long options: protection, or the LEAP
_ENTRY = "#3e8ef7"  # futures and shares: where they were bought or sold


def tv_root(product: str) -> str:
    """tastytrade's product ("/ZB", "/MES", "IWM") as TradingView's root."""
    return product.lstrip("/").upper()


def _num(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _plain(value: Decimal) -> str:
    """A price as Pine reads it: no exponent, no trailing zeros."""
    text = format(value.normalize(), "f")
    return text if "." in text else f"{text}.0"


def _qty(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _day(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {d:%b}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def levels(legs: Iterable[Mapping[str, Any]]) -> dict[str, list[tuple[Decimal, str, str]]]:
    """(price, label, colour) per TradingView root, legs on the same line merged.

    Options are drawn at their strike; futures and shares at the price they
    were opened at, averaged when there are several.
    """
    options: dict[tuple[str, str, str, Decimal, str, str], Decimal] = defaultdict(Decimal)
    entries: dict[tuple[str, str, str], list[tuple[Decimal, Decimal]]] = defaultdict(list)
    names: dict[tuple[Any, ...], str] = {}

    for leg in legs:
        root = tv_root(str(leg.get("product") or leg.get("underlying") or ""))
        qty = _num(leg.get("quantity")) or Decimal(0)
        side = "short" if str(leg.get("side", "")).lower() == "short" else "long"
        owners = [
            s.get("name") if isinstance(s, Mapping) else str(s) for s in (leg.get("in_strategies") or [])
        ]
        owner = ", ".join(o for o in owners if o)
        strike = _num(leg.get("strike"))
        right = str(leg.get("right") or "")
        if strike is not None and right in ("C", "P"):
            key = (root, side, right, strike, str(leg.get("expiration") or ""), owner)
            options[key] += qty
        else:
            price = _num(leg.get("open_price"))
            if price is None:
                continue
            kind = "shares" if not str(leg.get("product", "")).startswith("/") else str(
                leg.get("underlying") or root
            )
            ekey = (root, side, kind)
            entries[ekey].append((price, qty))
            names[ekey] = owner

    out: dict[str, list[tuple[Decimal, str, str]]] = defaultdict(list)
    for (root, side, right, strike, expiry, owner), qty in options.items():
        # A price tag, read left to right: how many and which way, the strike
        # and side, when it expires, and whose it is. "-2 7200P 27 Nov".
        sign = "-" if side == "short" else "+"
        text = f"{sign}{_qty(qty)} {_plain(strike).removesuffix('.0')}{right}"
        if expiry:
            text += f"  {_day(expiry)}"
        if owner:
            text += f"  ({owner})"
        out[root].append((strike, text, _SHORT if side == "short" else _LONG))
    for (root, side, kind), fills in entries.items():
        total = sum((q for _, q in fills), Decimal(0))
        if not total:
            continue
        average = sum((p * q for p, q in fills), Decimal(0)) / total
        average = average.quantize(Decimal("0.0001")).normalize()
        unit = "shares" if kind == "shares" else kind
        sign = "-" if side == "short" else "+"
        text = f"{sign}{_qty(total)} {unit} @ {_plain(average).removesuffix('.0')}"
        owner = names.get((root, side, kind), "")
        if owner:
            text += f"  ({owner})"
        out[root].append((average, text, _ENTRY))
    for root in out:
        out[root].sort(key=lambda lv: lv[0], reverse=True)
    return dict(out)


# Plain ASCII only in the script: it travels through the clipboard, and a
# clipboard that reads it in the wrong encoding turns "·" into "¬∑".
def gap_notes(gaps: Iterable[Mapping[str, Any]]) -> dict[str, list[tuple[str, bool]]]:
    """IV − HV per root, one line per expiry: ("IV-HV 27 Nov: +3.1  (IV 16.1 / HV 13.0)", rich)."""
    out: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for g in gaps:
        value, iv, hv = _num(g.get("gap")), _num(g.get("iv")), _num(g.get("hv"))
        if value is None or iv is None or hv is None:
            continue
        expiry = g.get("expiration")
        day = _day(expiry.isoformat() if isinstance(expiry, date) else str(expiry))
        text = f"IV-HV {day}: {value * 100:+.1f}  (IV {iv * 100:.1f} / HV {hv * 100:.1f})"
        out[tv_root(str(g.get("product", "")))].append((text, value >= 0))
    return dict(out)


COMBINED_NAME = "Tasty Desk positions + IV-HV"


def pine_script(
    legs: Iterable[Mapping[str, Any]],
    as_of: datetime,
    gaps: Iterable[Mapping[str, Any]] | None = None,
    name: str = SCRIPT_NAME,
) -> str:
    """The positions indicator, ready to paste into TradingView's Pine editor.

    With ``gaps`` it is the combined one: the same lines, and IV - HV for each
    expiry in its corner box. Three indicators are kept — positions, IV - HV,
    and both — so any of them can sit on a chart on its own.
    """
    by_root = levels(legs)
    notes = gap_notes(gaps) if gaps is not None else {}
    stamp = f"{as_of:%d %b %Y %H:%M}"
    lines = [
        "//@version=6",
        f'indicator("{name}", overlay = true, max_lines_count = 300, max_labels_count = 300)',
        f"// Written by Tasty Desk on {stamp}. Replaced whole on every update - do not edit by hand.",
        "",
        # The chart tools carried in here so one indicator slot does the work
        # of several on the plan's indicator limit. Each one is switched on or
        # off, and its numbers changed, in the indicator's settings; the
        # defaults are TradingView's own, Keltner at 3 ATR as in Tom's videos.
        "// Chart tools, each switched on or off and set in this indicator's settings.",
        'showLines = input.bool(true, "My position lines", group = "Positions")',
        "",
        'e1On = input.bool(true, "EMA 1", inline = "e1", group = "EMAs")',
        'e1Len = input.int(8, "", minval = 1, inline = "e1", group = "EMAs")',
        'e2On = input.bool(true, "EMA 2", inline = "e2", group = "EMAs")',
        'e2Len = input.int(21, "", minval = 1, inline = "e2", group = "EMAs")',
        'e3On = input.bool(false, "EMA 3", inline = "e3", group = "EMAs")',
        'e3Len = input.int(50, "", minval = 1, inline = "e3", group = "EMAs")',
        'e4On = input.bool(true, "EMA 4", inline = "e4", group = "EMAs")',
        'e4Len = input.int(200, "", minval = 1, inline = "e4", group = "EMAs")',
        "ema1 = ta.ema(close, e1Len)",
        "ema2 = ta.ema(close, e2Len)",
        "ema3 = ta.ema(close, e3Len)",
        "ema4 = ta.ema(close, e4Len)",
        'plot(e1On ? ema1 : na, "EMA 1", color = #5b9cf6, linewidth = 1)',
        'plot(e2On ? ema2 : na, "EMA 2", color = #2962ff, linewidth = 1)',
        'plot(e3On ? ema3 : na, "EMA 3", color = #f06292, linewidth = 1)',
        'plot(e4On ? ema4 : na, "EMA 4", color = #9c27b0, linewidth = 2)',
        "",
        'bbOn = input.bool(false, "Bollinger Bands", group = "Bollinger Bands")',
        'bbLen = input.int(20, "Length", minval = 1, group = "Bollinger Bands")',
        'bbMult = input.float(2.0, "Std devs", minval = 0.1, step = 0.5, group = "Bollinger Bands")',
        "[bbMid, bbUp, bbLo] = ta.bb(close, bbLen, bbMult)",
        'plot(bbOn ? bbUp : na, "BB upper", color = color.new(color.gray, 30))',
        'plot(bbOn ? bbMid : na, "BB middle", color = color.new(color.gray, 60))',
        'plot(bbOn ? bbLo : na, "BB lower", color = color.new(color.gray, 30))',
        "",
        # TradingView's built-in Keltner: an EMA middle, and the range as an
        # EMA of the true range, so the two read the same numbers.
        'kcOn = input.bool(false, "Keltner Channels", group = "Keltner Channels")',
        'kcLen = input.int(20, "Length", minval = 1, group = "Keltner Channels")',
        'kcMult = input.float(3.0, "ATRs wide", minval = 0.1, step = 0.5, group = "Keltner Channels")',
        "kcMid = ta.ema(close, kcLen)",
        "kcRange = ta.ema(ta.tr(true), kcLen)",
        'plot(kcOn ? kcMid + kcMult * kcRange : na, "Keltner upper", color = color.new(#ff9800, 20))',
        'plot(kcOn ? kcMid : na, "Keltner middle", color = color.new(#ff9800, 60))',
        'plot(kcOn ? kcMid - kcMult * kcRange : na, "Keltner lower", color = color.new(#ff9800, 20))',
        "",
        'sarOn = input.bool(false, "Parabolic SAR", group = "Parabolic SAR")',
        'sarStart = input.float(0.02, "Start", minval = 0.001, step = 0.01, group = "Parabolic SAR")',
        'sarInc = input.float(0.02, "Increment", minval = 0.001, step = 0.01, group = "Parabolic SAR")',
        'sarMax = input.float(0.2, "Max value", minval = 0.01, step = 0.05, group = "Parabolic SAR")',
        "sar = ta.sar(sarStart, sarInc, sarMax)",
        'plot(sarOn ? sar : na, "Parabolic SAR", color = sar < close ? #30a46c : #e5484d,',
        "     style = plot.style_circles, linewidth = 1)",
        "",
        "var prices = array.new<float>()",
        "var texts = array.new<string>()",
        "var colors = array.new<color>()",
        "add(float p, string t, color c) =>",
        "    prices.push(p)",
        "    texts.push(t)",
        "    colors.push(c)",
        "var noteTexts = array.new<string>()",
        "var noteColors = array.new<color>()",
        "note(string t, color c) =>",
        "    noteTexts.push(t)",
        "    noteColors.push(c)",
        "",
        "if barstate.isfirst",
        "    switch syminfo.root",
    ]
    if not by_root:
        lines.append('        "" => add(na, "", na)')
    for root in sorted(set(by_root) | set(notes)):
        lines.append(f'        "{_escape(root)}" =>')
        for price, text, colour in by_root.get(root, []):
            lines.append(f'            add({_plain(price)}, "{_escape(text)}", color.new({colour}, 0))')
        for text, rich in notes.get(root, []):
            lines.append(f'            note("{_escape(text)}", {_LONG if rich else _SHORT})')
    lines += [
        "",
        "var line[] drawn = array.new<line>()",
        "var label[] tags = array.new<label>()",
        "if barstate.islast",
        "    for l in drawn",
        "        l.delete()",
        "    for t in tags",
        "        t.delete()",
        "    drawn.clear()",
        "    tags.clear()",
        "    for i = 0 to prices.size() - 1",
        "        if prices.size() == 0 or not showLines",
        "            break",
        "        p = prices.get(i)",
        "        c = colors.get(i)",
        # A faint dotted line from the past up to today, and a solid price tag
        # just right of the last bar: the line never runs through the text.
        "        drawn.push(line.new(bar_index - 1, p, bar_index, p, extend = extend.left,",
        "             color = color.new(c, 55), style = line.style_dotted, width = 1))",
        "        tags.push(label.new(bar_index + 1, p, texts.get(i), style = label.style_label_left,",
        "             color = color.new(c, 15), textcolor = color.white, size = size.small))",
        "",
        # The corner box: IV - HV for each expiry held on this ticker, then
        # when the script was last written.
        "var table corner = table.new(position.bottom_right, 1, 12)",
        "if barstate.islast and (prices.size() > 0 or noteTexts.size() > 0)",
        "    n = math.min(noteTexts.size(), 11)",
        "    for i = 0 to n - 1",
        "        if n == 0",
        "            break",
        "        corner.cell(0, i, noteTexts.get(i), text_color = noteColors.get(i), text_size = size.small,",
        "             text_halign = text.align_right)",
        f'    corner.cell(0, n, "Tasty Desk | {stamp}", text_color = color.gray, text_size = size.tiny,',
        "         text_halign = text.align_right)",
    ]
    return "\n".join(lines) + "\n"


IVHV_NAME = "Tasty Desk IV-HV"


def ivhv_script(gaps: Iterable[Mapping[str, Any]], as_of: datetime) -> str:
    """IV - HV as its own indicator: a small box per ticker, one line per expiry held.

    Kept apart from the positions indicator so either can be shown, hidden or
    moved without the other.
    """
    notes = gap_notes(gaps)
    stamp = f"{as_of:%d %b %Y %H:%M}"
    lines = [
        "//@version=6",
        f'indicator("{IVHV_NAME}", overlay = true)',
        f"// Written by Tasty Desk on {stamp}. Replaced whole on every update - do not edit by hand.",
        "// For each expiry held on this ticker: that expiry's implied volatility minus the",
        "// volatility the product actually moved with over the same number of days.",
        "",
        "var texts = array.new<string>()",
        "var colors = array.new<color>()",
        "note(string t, color c) =>",
        "    texts.push(t)",
        "    colors.push(c)",
        "",
        "if barstate.isfirst",
        "    switch syminfo.root",
    ]
    if not notes:
        lines.append('        "" => note("", na)')
    for root in sorted(notes):
        lines.append(f'        "{_escape(root)}" =>')
        for text, rich in notes[root]:
            lines.append(f'            note("{_escape(text)}", {_LONG if rich else _SHORT})')
    lines += [
        "",
        "var table corner = table.new(position.bottom_left, 1, 12, bgcolor = color.new(color.black, 30))",
        "if barstate.islast and texts.size() > 0",
        "    n = math.min(texts.size(), 11)",
        "    for i = 0 to n - 1",
        "        corner.cell(0, i, texts.get(i), text_color = colors.get(i), text_size = size.small,",
        "             text_halign = text.align_left)",
        f'    corner.cell(0, n, "IV-HV | {stamp}", text_color = color.gray, text_size = size.tiny,',
        "         text_halign = text.align_left)",
    ]
    return "\n".join(lines) + "\n"
