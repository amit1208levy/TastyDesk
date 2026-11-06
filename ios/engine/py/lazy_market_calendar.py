"""tastytrade, imported on the phone without pandas.

``tastytrade.utils`` imports pandas at the top (for one type hint, on a local
variable, which Python never evaluates) and builds the NYSE calendar from
``pandas_market_calendars`` as it is imported. Tasty Desk never calls the
functions that use that calendar, and on the phone importing pandas, numpy and
the two calendar libraries is about 4.5 of the 14 seconds the engine takes to
start.

So, before tastytrade is imported, :func:`install` puts in place:

* a ``pandas_market_calendars`` whose ``get_calendar`` hands back a stand-in
  that loads the real calendar the first time anything is asked of it, and
  which passes any other name through to the real library, loaded then;
* for the length of that one import only, a ``pandas`` holding the name the
  type hint needs, removed straight after so any later ``import pandas`` gets
  the real one.

Nothing is computed differently: a call that needs the calendar gets the real
one. The parity check (ios/parity) runs with this in place.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

_STUB = "_tastydesk_lazy"


def _real_calendars() -> types.ModuleType:
    current = sys.modules.get("pandas_market_calendars")
    if getattr(current, _STUB, False):
        del sys.modules["pandas_market_calendars"]
    return importlib.import_module("pandas_market_calendars")


class _LazyCalendar:
    """The real calendar, made the first time it is used."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self._kwargs = kwargs
        self._real: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._real is None:
            self._real = _real_calendars().get_calendar(*self._args, **self._kwargs)
        return getattr(self._real, name)


def _pass_through(name: str) -> Any:
    # Python's import machinery probes modules for names like __path__; those
    # must not load the real library while the stand-in pandas is in place.
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_real_calendars(), name)


def install() -> None:
    if "tastytrade.utils" in sys.modules or "pandas_market_calendars" in sys.modules:
        return  # too late, or the real one is already here

    calendars = types.ModuleType("pandas_market_calendars")
    setattr(calendars, _STUB, True)
    calendars.get_calendar = _LazyCalendar  # type: ignore[attr-defined]
    calendars.__getattr__ = _pass_through  # type: ignore[method-assign]
    sys.modules["pandas_market_calendars"] = calendars

    added_pandas = "pandas" not in sys.modules
    if added_pandas:
        pandas = types.ModuleType("pandas")
        setattr(pandas, _STUB, True)

        class Timestamp:  # only ever a type hint in tastytrade.utils
            pass

        pandas.Timestamp = Timestamp  # type: ignore[attr-defined]
        sys.modules["pandas"] = pandas
    try:
        importlib.import_module("tastytrade.utils")
    finally:
        if added_pandas and getattr(sys.modules.get("pandas"), _STUB, False):
            del sys.modules["pandas"]
