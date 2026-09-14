"""The Tasty Desk engine.

Read ``models.py`` first: it carries the sign conventions and the rule that
governs everything else — risk belongs to a strategy, never to a leg.
"""

from tastydesk.core.analytics import (
    PerformanceStats,
    RuleAdherence,
    RuleSet,
    by_bucket,
    by_strategy_type,
    by_underlying,
    performance,
    rule_adherence,
)
from tastydesk.core.classify import classify
from tastydesk.core.grouping import build_strategies, match_rolls
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
from tastydesk.core.occ import ParsedOption, build_occ_symbol, is_option_symbol, parse_option_symbol
from tastydesk.core.pnl import breakevens, compute_pnl, cost_to_close, max_loss, max_profit, payoff_at
from tastydesk.core.risk import DEFAULT_THRESHOLDS, RiskThresholds, assess

__all__ = [
    "CREDIT_STRATEGIES",
    "DEFAULT_THRESHOLDS",
    "ZERO",
    "DangerLevel",
    "Direction",
    "Leg",
    "OptionType",
    "ParsedOption",
    "PerformanceStats",
    "PortfolioSummary",
    "RiskProfile",
    "RiskReason",
    "RiskThresholds",
    "RuleAdherence",
    "RuleSet",
    "Strategy",
    "StrategyPnL",
    "StrategyRisk",
    "StrategyType",
    "UnderlyingQuote",
    "assess",
    "breakevens",
    "build_occ_symbol",
    "build_strategies",
    "by_bucket",
    "by_strategy_type",
    "by_underlying",
    "classify",
    "compute_pnl",
    "cost_to_close",
    "is_option_symbol",
    "match_rolls",
    "max_loss",
    "max_profit",
    "parse_option_symbol",
    "payoff_at",
    "performance",
    "rule_adherence",
]
