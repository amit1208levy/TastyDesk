"""A small, fixed tastytrade account, served at the HTTP level.

The parity check runs the Mac's Python and the phone's engine against this,
so both go through everything real: the tastytrade library's own parsing
(pydantic, which differs in version between the two), Tasty Desk's sync,
grouping, pricing, P&L and risk, the database, and the API's serialisation.
Only the network is replaced.

Dates are relative to today, so the positions are always open and their
expirations always ahead. Both runs happen on the same day.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx2

ACCOUNT = "5WX24680"
D = Decimal


def _friday_after(day: date, days: int) -> date:
    target = day + timedelta(days=days)
    return target + timedelta(days=(4 - target.weekday()) % 7)


def _occ(root: str, expiry: date, kind: str, strike: str) -> str:
    whole = int(D(strike) * 1000)
    return f"{root:<6}{expiry:%y%m%d}{kind}{whole:08d}"


TODAY = datetime.now(UTC).date()
EXPIRY = _friday_after(TODAY, 45)
OPENED = TODAY - timedelta(days=12)
CLOSED_OPEN = TODAY - timedelta(days=40)
CLOSED_SHUT = TODAY - timedelta(days=20)
CLOSED_EXPIRY = _friday_after(CLOSED_OPEN, 30)

# Open: a SPY put credit spread, a QQQ short strangle, an IWM iron condor.
# Closed: an XLE short strangle, bought back for less than it was sold.
SPY_580P = _occ("SPY", EXPIRY, "P", "580")
SPY_575P = _occ("SPY", EXPIRY, "P", "575")
QQQ_400P = _occ("QQQ", EXPIRY, "P", "400")
QQQ_450C = _occ("QQQ", EXPIRY, "C", "450")
IWM_200P = _occ("IWM", EXPIRY, "P", "200")
IWM_195P = _occ("IWM", EXPIRY, "P", "195")
IWM_235C = _occ("IWM", EXPIRY, "C", "235")
IWM_240C = _occ("IWM", EXPIRY, "C", "240")
XLE_80P = _occ("XLE", CLOSED_EXPIRY, "P", "80")
XLE_95C = _occ("XLE", CLOSED_EXPIRY, "C", "95")


def _at(day: date, minute: int = 0) -> str:
    return datetime.combine(day, time(14, 30 + minute), tzinfo=UTC).isoformat()


def _money(row: dict[str, Any], key: str, amount: Decimal) -> None:
    row[key] = str(abs(amount))
    row[f"{key}-effect"] = "Debit" if amount < 0 else "Credit"


def _tx(
    n: int,
    action: str,
    symbol: str,
    root: str,
    price: str,
    value: str,
    day: date,
    order: int,
    legs: int,
    qty: int = 1,
):
    row: dict[str, Any] = {
        "id": n,
        "account-number": ACCOUNT,
        "transaction-type": "Trade",
        "transaction-sub-type": action,
        "description": f"{action} {qty} {symbol}",
        "executed-at": _at(day, n % 20),
        "transaction-date": day.isoformat(),
        "is-estimated-fee": False,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": root,
        "action": action,
        "quantity": str(qty),
        "price": price,
        "order-id": order,
        "leg-count": legs,
    }
    _money(row, "value", D(value))
    _money(row, "net-value", D(value) - D("1.14") * qty)
    _money(row, "commission", D("-1.00") * qty)
    _money(row, "clearing-fees", D("-0.10") * qty)
    _money(row, "regulatory-fees", D("-0.04") * qty)
    return row


TRANSACTIONS = [
    _tx(1, "Sell to Open", SPY_580P, "SPY", "2.00", "200", OPENED, 1001, 2),
    _tx(2, "Buy to Open", SPY_575P, "SPY", "1.00", "-100", OPENED, 1001, 2),
    _tx(3, "Sell to Open", QQQ_400P, "QQQ", "2.10", "210", OPENED, 1002, 2),
    _tx(4, "Sell to Open", QQQ_450C, "QQQ", "1.90", "190", OPENED, 1002, 2),
    _tx(5, "Sell to Open", IWM_200P, "IWM", "1.20", "240", OPENED, 1003, 4, 2),
    _tx(6, "Buy to Open", IWM_195P, "IWM", "0.70", "-140", OPENED, 1003, 4, 2),
    _tx(7, "Sell to Open", IWM_235C, "IWM", "1.10", "220", OPENED, 1003, 4, 2),
    _tx(8, "Buy to Open", IWM_240C, "IWM", "0.55", "-110", OPENED, 1003, 4, 2),
    _tx(9, "Sell to Open", XLE_80P, "XLE", "1.50", "150", CLOSED_OPEN, 1004, 2),
    _tx(10, "Sell to Open", XLE_95C, "XLE", "1.40", "140", CLOSED_OPEN, 1004, 2),
    _tx(11, "Buy to Close", XLE_80P, "XLE", "0.60", "-60", CLOSED_SHUT, 1005, 2),
    _tx(12, "Buy to Close", XLE_95C, "XLE", "0.45", "-45", CLOSED_SHUT, 1005, 2),
]

# (symbol, root, signed quantity, average open price)
POSITIONS = [
    (SPY_580P, "SPY", -1, "2.00"),
    (SPY_575P, "SPY", 1, "1.00"),
    (QQQ_400P, "QQQ", -1, "2.10"),
    (QQQ_450C, "QQQ", -1, "1.90"),
    (IWM_200P, "IWM", -2, "1.20"),
    (IWM_195P, "IWM", 2, "0.70"),
    (IWM_235C, "IWM", -2, "1.10"),
    (IWM_240C, "IWM", 2, "0.55"),
]

# symbol -> mark, bid, ask, prev close, delta, gamma, theta, vega, iv
OPTION_QUOTES = {
    SPY_580P: ("2.65", "2.60", "2.70", "2.40", "-0.31", "0.011", "-0.21", "0.62", "0.192"),
    SPY_575P: ("2.05", "2.00", "2.10", "1.85", "-0.26", "0.010", "-0.19", "0.58", "0.198"),
    QQQ_400P: ("1.35", "1.30", "1.40", "1.45", "-0.17", "0.006", "-0.14", "0.41", "0.231"),
    QQQ_450C: ("2.85", "2.80", "2.90", "2.60", "0.29", "0.008", "-0.18", "0.49", "0.205"),
    IWM_200P: ("0.85", "0.80", "0.90", "0.95", "-0.18", "0.012", "-0.08", "0.21", "0.262"),
    IWM_195P: ("0.50", "0.45", "0.55", "0.57", "-0.12", "0.009", "-0.06", "0.17", "0.275"),
    IWM_235C: ("0.75", "0.70", "0.80", "0.70", "0.16", "0.011", "-0.07", "0.20", "0.241"),
    IWM_240C: ("0.40", "0.35", "0.45", "0.37", "0.10", "0.008", "-0.05", "0.15", "0.249"),
}

# symbol -> last, prev close, iv, iv rank (0-1), beta
UNDERLYINGS = {
    "SPY": ("612.40", "608.95", "0.168", "0.31", "1.00"),
    "QQQ": ("441.80", "437.10", "0.214", "0.38", "1.12"),
    "IWM": ("218.35", "219.10", "0.249", "0.44", "1.18"),
    "XLE": ("88.10", "88.60", "0.262", "0.27", "0.81"),
    "VIX": ("16.85", "17.40", None, None, None),
}

NET_LIQ = D("104850.25")


def _balances(snapshot_date: date | None = None) -> dict[str, Any]:
    zero = "0.0"
    row = {
        k: zero
        for k in (
            "long-equity-value short-equity-value long-futures-value short-futures-value "
            "long-futures-derivative-value short-futures-derivative-value long-margineable-value "
            "short-margineable-value day-trading-buying-power futures-margin-requirement "
            "maintenance-call-value reg-t-call-value day-trading-call-value day-equity-call-value "
            "day-trade-excess pending-cash long-cryptocurrency-value short-cryptocurrency-value "
            "cryptocurrency-margin-requirement unsettled-cryptocurrency-fiat-amount "
            "equity-offering-margin-requirement long-bond-value bond-margin-requirement "
            "pending-margin-interest effective-cryptocurrency-buying-power "
            "futures-overnight-margin-requirement futures-intraday-margin-requirement"
        ).split()
    }
    row.update(
        {
            "account-number": ACCOUNT,
            "cash-balance": "106210.25",
            "long-derivative-value": "1730.0",
            "short-derivative-value": "3090.0",
            "margin-equity": str(NET_LIQ),
            "equity-buying-power": "186400.0",
            "derivative-buying-power": "93200.0",
            "available-trading-funds": "93200.0",
            "maintenance-requirement": "11650.0",
            "net-liquidating-value": str(NET_LIQ),
            "cash-available-to-withdraw": "93200.0",
            "closed-loop-available-balance": "93200.0",
            "used-derivative-buying-power": "11650.0",
            "reg-t-margin-requirement": "11650.0",
            "maintenance-excess": "93200.0",
            "snapshot-date": (snapshot_date or TODAY).isoformat(),
            "updated-at": _at(TODAY),
        }
    )
    return row


def _position(symbol: str, root: str, qty: int, avg: str) -> dict[str, Any]:
    mark = OPTION_QUOTES[symbol][0]
    return {
        "account-number": ACCOUNT,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": root,
        "quantity": str(abs(qty)),
        "quantity-direction": "Short" if qty < 0 else "Long",
        "close-price": OPTION_QUOTES[symbol][3],
        "mark": mark,
        "mark-price": mark,
        "average-open-price": avg,
        "multiplier": 100,
        "cost-effect": "Credit" if qty < 0 else "Debit",
        "is-suppressed": False,
        "is-frozen": False,
        "realized-day-gain": "0.0",
        "realized-today": "0.0",
        "created-at": _at(OPENED),
        "updated-at": _at(TODAY),
        "expires-at": _at(EXPIRY),
    }


def _quote(symbol: str) -> dict[str, Any] | None:
    if symbol in OPTION_QUOTES:
        mark, bid, ask, prev, delta, gamma, theta, vega, iv = OPTION_QUOTES[symbol]
        return {
            "symbol": symbol,
            "instrument-type": "Equity Option",
            "mark": mark,
            "bid": bid,
            "ask": ask,
            "mid": mark,
            "prev-close": prev,
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
            "volatility": iv,
        }
    if symbol in UNDERLYINGS:
        last, prev, *_ = UNDERLYINGS[symbol]
        return {
            "symbol": symbol,
            "instrument-type": "Index" if symbol == "VIX" else "Equity",
            "mark": last,
            "last": last,
            "bid": last,
            "ask": last,
            "mid": last,
            "prev-close": prev,
        }
    return None


def _metric(symbol: str) -> dict[str, Any] | None:
    if symbol not in UNDERLYINGS or UNDERLYINGS[symbol][2] is None:
        return None
    _, _, iv, rank, beta = UNDERLYINGS[symbol]
    return {
        "symbol": symbol,
        "implied-volatility-index": iv,
        "implied-volatility-index-rank": rank,
        "tw-implied-volatility-index-rank": rank,
        "implied-volatility-percentile": rank,
        "implied-volatility-updated-at": _at(TODAY),
        "beta": beta,
        "liquidity-rating": 4,
        "updated-at": _at(TODAY),
        "market-cap": "0",
    }


def _ok(data: Any, **extra: Any) -> httpx2.Response:
    return httpx2.Response(200, json={"data": data, **extra})


def _items(items: list[Any]) -> httpx2.Response:
    return _ok(
        {"items": items},
        pagination={
            "page-offset": 0,
            "total-pages": 1,
            "per-page": len(items) or 1,
            "total-items": len(items),
        },
    )


def handle(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    query = parse_qs(
        request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query
    )
    if request.method != "GET" and path != "/oauth/token":
        # The app is read-only: anything else reaching here is a bug.
        return httpx2.Response(405, json={"error": {"code": "read_only", "message": "read-only fake"}})
    if path == "/oauth/token":
        return httpx2.Response(
            200, json={"access_token": "fake-access", "expires_in": 900, "token_type": "Bearer"}
        )
    if path == "/customers/me/accounts":
        account = {
            "account-number": ACCOUNT,
            "opened-at": _at(TODAY - timedelta(days=900)),
            "nickname": "Individual",
            "account-type-name": "Individual",
            "is-closed": False,
            "day-trader-status": False,
            "is-firm-error": False,
            "is-firm-proprietary": False,
            "is-futures-approved": True,
            "margin-or-cash": "Margin",
            "is-foreign": False,
            "created-at": _at(TODAY - timedelta(days=900)),
        }
        return _ok({"items": [{"account": account, "authority-level": "owner"}]})
    prefix = f"/accounts/{ACCOUNT}"
    if path == f"{prefix}/positions":
        return _ok({"items": [_position(*p) for p in POSITIONS]})
    if path.startswith(f"{prefix}/balances"):
        return _ok(_balances())
    if path == f"{prefix}/balance-snapshots":
        days = [TODAY - timedelta(days=n) for n in range(1, 6)]
        return _items(
            [{**_balances(d), "net-liquidating-value": str(NET_LIQ - 300 * n)} for n, d in enumerate(days)]
        )
    if path == f"{prefix}/transactions":
        return _items(TRANSACTIONS)
    if path == f"{prefix}/net-liq/history":
        rows = []
        for n in range(30, 0, -1):
            value = str(NET_LIQ - 25 * n)
            stamp = _at(TODAY - timedelta(days=n))
            rows.append(
                {
                    **{
                        k: value
                        for k in (
                            "open",
                            "high",
                            "low",
                            "close",
                            "total-open",
                            "total-high",
                            "total-low",
                            "total-close",
                        )
                    },
                    **{
                        k: "0.0"
                        for k in (
                            "pending-cash-open",
                            "pending-cash-high",
                            "pending-cash-low",
                            "pending-cash-close",
                        )
                    },
                    "time": stamp,
                }
            )
        return _ok({"items": rows})
    if path == f"/margin/accounts/{ACCOUNT}/requirements":
        groups = [
            {
                "description": root,
                "code": root,
                "underlying-symbol": root,
                "underlying-type": "Equity",
                "buying-power": value,
                "margin-calculation-type": "Reg T",
                "margin-requirement": value,
                "margin-requirement-effect": "Debit",
            }
            for root, value in (("SPY", "500.0"), ("QQQ", "8750.0"), ("IWM", "2400.0"))
        ]
        return _ok(
            {
                "account-number": ACCOUNT,
                "description": "Total",
                "margin-calculation-type": "Reg T",
                "option-level": "No Restrictions",
                "margin-requirement": "11650.0",
                "maintenance-requirement": "11650.0",
                "margin-equity": str(NET_LIQ),
                "option-buying-power": "93200.0",
                "reg-t-margin-requirement": "11650.0",
                "reg-t-option-buying-power": "93200.0",
                "maintenance-excess": "93200.0",
                "last-state-timestamp": 0,
                "groups": groups,
            }
        )
    if path == "/market-data/by-type":
        symbols = [s for values in query.values() for v in values for s in v.split(",")]
        return _items([q for s in symbols if (q := _quote(s.strip())) is not None])
    if path == "/market-metrics":
        symbols = [s for v in query.get("symbols", []) for s in v.split(",")]
        return _ok({"items": [m for s in symbols if (m := _metric(s.strip())) is not None]})
    return httpx2.Response(404, json={"error": {"code": "not_found", "message": f"fake has no {path}"}})


def install() -> None:
    """Route the tastytrade library's HTTP through :func:`handle`, and supply fake keys."""
    import tastytrade.session as session_module

    from tastydesk.core import auth

    real = session_module.AsyncClient

    def client(*args: Any, **kwargs: Any):
        kwargs["transport"] = httpx2.MockTransport(handle)
        return real(*args, **kwargs)

    session_module.AsyncClient = client
    auth._get = lambda service: "fake-" + service  # noqa: SLF001


def dumps(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True)
