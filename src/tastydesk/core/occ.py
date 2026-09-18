"""Parsing of option symbols.

Equity options use the 21-character OCC format::

    AAPL  240119C00150000
    |     |     ||
    |     |     |+-- strike x 1000, 8 digits, zero padded
    |     |     +--- C(all) or P(ut)
    |     +--------- expiration, YYMMDD
    +--------------- root symbol, 6 chars, space padded on the right

Futures options use tastytrade's own format (``./ESZ4 EW4Z4 241227P5800``)
which we parse on a best-effort basis; anything we cannot read comes back as
``None`` fields rather than raising, so one odd symbol never breaks a sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

__all__ = ["ParsedOption", "parse_option_symbol", "build_occ_symbol", "is_option_symbol"]

_OCC_RE = re.compile(
    r"^(?P<root>[A-Z0-9./ ]{1,6}?)\s*"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])"
    r"(?P<strike>\d{8})$"
)

# Futures options come in more shapes than one pattern of spaces:
#
#   ./ZBZ6 OZBZ6 261120P102        contract, option product, date  (two spaces)
#   ./MESH7EX3Z6 261218P7000       product run together            (one space)
#   ./MESZ6MS4DU6260924P7500       no space at all
#
# So anchor on the tail, which is always YYMMDD + C/P + strike, and treat
# everything between "./" and that as contract identification. Trying to split
# the contract from the option product is guesswork that fails on a third of a
# real futures book; the underlying comes from the API's own
# ``underlying_symbol`` anyway, which is authoritative.
_FUT_RE = re.compile(
    r"^\./(?P<head>.+?)\s*"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])"
    r"(?P<strike>[0-9]+(?:\.[0-9]+)?)$"
)


@dataclass(frozen=True, slots=True)
class ParsedOption:
    """The pieces of an option symbol we actually care about."""

    root: str
    expiration: date
    option_type: str  # "C" or "P"
    strike: Decimal

    @property
    def is_call(self) -> bool:
        return self.option_type == "C"

    @property
    def is_put(self) -> bool:
        return self.option_type == "P"


def _to_date(yy: str, mm: str, dd: str) -> date:
    # OCC only carries two year digits. Options do not trade 75 years out, so
    # the 2000s are always the right guess.
    return date(2000 + int(yy), int(mm), int(dd))


def parse_option_symbol(symbol: str | None) -> ParsedOption | None:
    """Parse an equity or futures option symbol, or return None if it isn't one."""
    if not symbol:
        return None
    raw = symbol.strip()

    m = _FUT_RE.match(raw)
    if m:
        return ParsedOption(
            # The first token is the futures contract; the rest is the option
            # product code, which nothing downstream needs.
            root=m.group("head").split()[0],
            expiration=_to_date(m.group("yy"), m.group("mm"), m.group("dd")),
            option_type=m.group("cp"),
            strike=Decimal(m.group("strike")),
        )

    m = _OCC_RE.match(raw)
    if m:
        return ParsedOption(
            root=m.group("root").strip(),
            expiration=_to_date(m.group("yy"), m.group("mm"), m.group("dd")),
            option_type=m.group("cp"),
            strike=Decimal(m.group("strike")) / Decimal(1000),
        )
    return None


def is_option_symbol(symbol: str | None) -> bool:
    return parse_option_symbol(symbol) is not None


def build_occ_symbol(root: str, expiration: date, option_type: str, strike: Decimal) -> str:
    """Inverse of :func:`parse_option_symbol` for equity options. Round-trips."""
    if option_type not in ("C", "P"):
        raise ValueError(f"option_type must be 'C' or 'P', got {option_type!r}")
    thousandths = int((Decimal(strike) * 1000).to_integral_value())
    return f"{root.upper():<6}{expiration:%y%m%d}{option_type}{thousandths:08d}"


MONTH_CODES = "FGHJKMNQUVXZ"
_FUT_MONTH_TAIL = re.compile(rf"[{MONTH_CODES}]\d{{1,2}}$")


def product_root(underlying: str | None) -> str:
    """The tradable product behind a contract month.

    ``/ZSF7`` and ``/ZSX6`` are January and November soybeans: one product, two
    months. Everything the user judges — performance, strategy history, the beta
    tastytrade publishes — is a property of the product, never of the month, so
    every grouping and every market-metrics lookup has to go through here.

    Equity symbols are already products and come back unchanged.
    """
    key = (underlying or "").strip().upper()
    if not key.startswith("/"):
        return key
    trimmed = _FUT_MONTH_TAIL.sub("", key[1:])
    return f"/{trimmed}" if trimmed else key
