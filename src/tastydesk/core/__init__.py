"""Tasty Desk engine: the whole options brain, with no UI attached.

Everything the dashboard, the CLI and the MCP server are allowed to depend on
is re-exported here, so a caller writes ``from tastydesk.core import
compute_pnl, assess`` and never has to know which module something lives in.
The submodules stay importable under their own names for the cases where the
module namespace reads better (``core.pnl.max_profit`` vs a bare ``max_profit``).

The pipeline, in the order the data flows
-----------------------------------------
1. :mod:`~tastydesk.core.auth` / :mod:`~tastydesk.core.client` pull transactions
   and quotes from tastytrade, read-only.
2. :func:`build_strategies` reconstructs trades from those transaction rows and
   :func:`classify` names each structure and says whether its risk is capped.
3. :class:`MarkService` writes live prices and greeks onto the :class:`Leg`
   objects in place.
4. :func:`compute_pnl` turns a :class:`Strategy` into a :class:`StrategyPnL`,
   and :func:`assess` turns that into a :class:`StrategyRisk`.
5. :mod:`~tastydesk.core.analytics` reports on the closed trades; :class:`Database`
   persists everything.

Two conventions carry across every one of those steps and are worth repeating
at the front door:

*Money is account cash flow.* Positive means cash came in. ``net_credit > 0``
for a trade that took premium in, ``cost_to_close < 0`` because you pay to get
out, and ``open_pnl = net_credit + cost_to_close`` is positive when winning.
Per-leg ``open_price`` and ``mark`` are the exception: they are positive quoted
prices, and direction lives in ``Leg.direction``.

*Risk belongs to a strategy, never to a leg.* The short put of a put credit
spread routinely shows a far worse percentage move than the spread it belongs
to, because the long put gained at the same instant. Nothing here raises an
alarm from one leg's percentage; the only leg-level alarms are physical ones
(a short leg in the money near expiry, a short call in the money before an
ex-dividend date).
"""

from __future__ import annotations

from tastydesk.core import (
    analytics,
    auth,
    client,
    db,
    grouping,
    marks,
    models,
    occ,
    pnl,
    risk,
)
from tastydesk.core import (
    classify as classify_mod,
)
from tastydesk.core.analytics import (
    BUCKET_DIMENSIONS,
    DEFAULT_RULES,
    DTE_BUCKETS,
    IV_RANK_BUCKETS,
    SHORT_DELTA_BUCKETS,
    UNKNOWN_BUCKET,
    PerformanceStats,
    RuleAdherence,
    RuleSet,
    by_bucket,
    by_strategy_type,
    by_underlying,
    closed_strategies,
    max_profit_at_close,
    performance,
    rule_adherence,
)
from tastydesk.core.auth import (
    SETUP_INSTRUCTIONS,
    CredentialError,
    Credentials,
    SessionManager,
    credentials_present,
    load_credentials,
)
from tastydesk.core.classify import classify

# ``TastyClient`` is deliberately the concrete, rate-limited account client.
# marks.py declares a structural Protocol of the same name for the slice it
# needs; that one stays behind ``marks.TastyClient`` so this namespace has
# exactly one meaning for the word.
from tastydesk.core.client import BrokerError, ClientHealth, TastyClient
from tastydesk.core.db import DEFAULT_DB_PATH, SCHEMA_VERSION, Database
from tastydesk.core.grouping import build_strategies, match_rolls
from tastydesk.core.marks import MarkService, streamer_symbol_for
from tastydesk.core.models import (
    CREDIT_STRATEGIES,
    ZERO,
    DangerLevel,
    Direction,
    Leg,
    OptionType,
    PortfolioSummary,
    RiskProfile,
    RiskReason,
    Strategy,
    StrategyPnL,
    StrategyRisk,
    StrategyType,
    UnderlyingQuote,
)
from tastydesk.core.occ import (
    ParsedOption,
    build_occ_symbol,
    is_option_symbol,
    parse_option_symbol,
)
from tastydesk.core.pnl import (
    breakevens,
    cash_secured_max_loss,
    compute_pnl,
    cost_to_close,
    jade_lizard_upside_covered,
    max_loss,
    max_profit,
    payoff_at,
)
from tastydesk.core.risk import DEFAULT_THRESHOLDS, RiskThresholds, assess

# ``classify`` the function shadows ``classify`` the module in this namespace,
# and the function is what callers want. The module is still reachable as
# ``tastydesk.core.classify_mod`` (and, as always, by importing it directly).
__all__ = [
    # --- submodules -------------------------------------------------------
    "analytics",
    "auth",
    "classify_mod",
    "client",
    "db",
    "grouping",
    "marks",
    "models",
    "occ",
    "pnl",
    "risk",
    # --- domain model -----------------------------------------------------
    "ZERO",
    "CREDIT_STRATEGIES",
    "DangerLevel",
    "Direction",
    "Leg",
    "OptionType",
    "PortfolioSummary",
    "RiskProfile",
    "RiskReason",
    "Strategy",
    "StrategyPnL",
    "StrategyRisk",
    "StrategyType",
    "UnderlyingQuote",
    # --- symbols ----------------------------------------------------------
    "ParsedOption",
    "build_occ_symbol",
    "is_option_symbol",
    "parse_option_symbol",
    # --- reconstruction and naming ----------------------------------------
    "build_strategies",
    "classify",
    "match_rolls",
    # --- live data --------------------------------------------------------
    "MarkService",
    "streamer_symbol_for",
    # --- profit and loss --------------------------------------------------
    "breakevens",
    "cash_secured_max_loss",
    "compute_pnl",
    "cost_to_close",
    "jade_lizard_upside_covered",
    "max_loss",
    "max_profit",
    "payoff_at",
    # --- risk -------------------------------------------------------------
    "DEFAULT_THRESHOLDS",
    "RiskThresholds",
    "assess",
    # --- analytics --------------------------------------------------------
    "BUCKET_DIMENSIONS",
    "DEFAULT_RULES",
    "DTE_BUCKETS",
    "IV_RANK_BUCKETS",
    "PerformanceStats",
    "RuleAdherence",
    "RuleSet",
    "SHORT_DELTA_BUCKETS",
    "UNKNOWN_BUCKET",
    "by_bucket",
    "by_strategy_type",
    "by_underlying",
    "closed_strategies",
    "max_profit_at_close",
    "performance",
    "rule_adherence",
    # --- broker and persistence -------------------------------------------
    "BrokerError",
    "ClientHealth",
    "DEFAULT_DB_PATH",
    "Database",
    "SCHEMA_VERSION",
    "TastyClient",
    # --- credentials ------------------------------------------------------
    "CredentialError",
    "Credentials",
    "SETUP_INSTRUCTIONS",
    "SessionManager",
    "credentials_present",
    "load_credentials",
]
