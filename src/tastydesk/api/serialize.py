"""JSON encoding for the HTTP layer.

Every Decimal leaves as a string. JSON numbers are IEEE doubles, and rounding a
trader's cost basis through a double to save a pair of quote characters is not a
trade worth making. The browser parses these at the moment of display.

None stays None and is rendered as an em dash by the UI. A value the engine
could not compute and a value that happens to be zero are different facts, and
this boundary is where conflating them would start.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def encode(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: encode(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(encode(k)): encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [encode(v) for v in value]
    if hasattr(value, "model_dump"):
        return encode(value.model_dump())
    if hasattr(value, "__dict__"):
        return {k: encode(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def encode_strategy(strategy: Any) -> dict[str, Any]:
    """Strategy plus its computed properties, which asdict() would otherwise drop."""
    out = encode(strategy)
    out["is_open"] = strategy.is_open
    out["realized_pnl"] = str(strategy.realized_pnl)
    return out


def encode_view(view: Any) -> dict[str, Any]:
    return {
        "strategy": encode_strategy(view.strategy),
        "pnl": encode(view.pnl),
        "risk": encode(view.risk),
        "underlying_price": encode(view.underlying_price),
        "iv_rank": encode(view.iv_rank),
    }
