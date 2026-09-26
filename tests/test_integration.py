"""End-to-end tests across every seam of the engine.

Each core module was written against the domain model rather than against its
neighbours, so the interesting failures are not inside any one of them — they
are at the joins. This file walks one realistic account history through the
whole chain exactly as the application does::

    Transaction rows -> build_strategies -> classify
                     -> marks written onto the Leg objects
                     -> compute_pnl -> assess
                     -> analytics.performance
                     -> Database.save_strategies -> load_strategies
                     -> compute_pnl again, identical numbers

Two live trades run through it: a **put credit spread** (defined risk) and a
**short strangle** (undefined risk), deliberately positioned so that both are
down exactly 150% of the credit taken in. That equality is the point. The same
mark-to-market pain must produce a *quieter* alarm on the structure whose loss
is capped, because it is — the spread is only 37.5% of the way to everything it
can lose while the strangle has no floor at all. If those two ever come out at
the same danger level, the cardinal rule has been broken somewhere in the chain.

Transactions are built with ``Transaction.model_validate`` from dasherized keys,
the way the API actually returns them: the SDK's own validator is what turns
``{"net-value": "100", "net-value-effect": "Debit"}`` into ``Decimal("-100")``,
and grouping.py is built on money arriving already signed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from tastytrade.account import Transaction

from tastydesk.core import analytics
from tastydesk.core.classify import classify
from tastydesk.core.db import Database
from tastydesk.core.grouping import build_strategies
from tastydesk.core.models import (
    ZERO,
    DangerLevel,
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyPnL,
    StrategyType,
    UnderlyingQuote,
)
from tastydesk.core.occ import build_occ_symbol
from tastydesk.core.pnl import compute_pnl
from tastydesk.core.risk import assess

D = Decimal

ACCOUNT = "5WX24680"

# Everything is priced as of this date. The expirations below sit 45 days out,
# comfortably clear of the 21-DTE gamma line, so the danger levels asserted here
# come from the P&L and the strikes rather than from the calendar.
TODAY = date(2025, 5, 1)
EXPIRY = date(2025, 6, 15)  # 45 DTE from TODAY


def _money(raw: dict[str, Any], key: str, amount: Decimal | None) -> None:
    """Write a signed amount the way the API does: magnitude plus an effect."""
    if amount is None:
        return
    raw[key] = str(abs(amount))
    raw[f"{key}-effect"] = "Debit" if amount < ZERO else "Credit"


def tx(
    *,
    id: int,
    sub_type: str,
    symbol: str,
    net_value: Decimal,
    when: datetime,
    price: str,
    underlying: str,
    order_id: int,
    quantity: Decimal | int = 1,
    leg_count: int | None = None,
) -> Transaction:
    """One API-shaped option trade row for ``ACCOUNT``."""
    raw: dict[str, Any] = {
        "id": id,
        "account-number": ACCOUNT,
        "transaction-type": "Trade",
        "transaction-sub-type": sub_type,
        "description": f"{sub_type} {symbol}",
        "executed-at": when.isoformat(),
        "transaction-date": when.date().isoformat(),
        "is-estimated-fee": False,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": underlying,
        "action": sub_type,
        "quantity": str(quantity),
        "price": price,
        "order-id": order_id,
    }
    if leg_count is not None:
        raw["leg-count"] = leg_count
    _money(raw, "value", net_value)
    _money(raw, "net-value", net_value)
    return Transaction.model_validate(raw)


def opt(root: str, strike: str, option_type: str, expiration: date = EXPIRY) -> str:
    return build_occ_symbol(root, expiration, option_type, D(strike))


def at(month: int, day: int, minute: int = 0) -> datetime:
    return datetime(2025, month, day, 14, 30 + minute, tzinfo=UTC)


# The canonical baseline from the brief, moved onto this file's expiration.
SPY_580P = opt("SPY", "580", "P")
SPY_575P = opt("SPY", "575", "P")

QQQ_400P = opt("QQQ", "400", "P")
QQQ_450C = opt("QQQ", "450", "C")

IWM_200P = opt("IWM", "200", "P")
IWM_230C = opt("IWM", "230", "C")


# --------------------------------------------------------------------------- #
# The account history
# --------------------------------------------------------------------------- #


def history() -> list[Transaction]:
    """Three trades: an open PCS, an open strangle, and a strangle already closed."""
    return [
        # --- Trade 1: 5-wide SPY put credit spread, one order, net credit +100 ---
        tx(
            id=1,
            sub_type="Sell to Open",
            symbol=SPY_580P,
            price="2.00",
            net_value=D("200"),
            when=at(4, 1),
            underlying="SPY",
            order_id=1001,
            leg_count=2,
        ),
        tx(
            id=2,
            sub_type="Buy to Open",
            symbol=SPY_575P,
            price="1.00",
            net_value=D("-100"),
            when=at(4, 1),
            underlying="SPY",
            order_id=1001,
            leg_count=2,
        ),
        # --- Trade 2: QQQ short strangle, one order, net credit +400 ---
        tx(
            id=3,
            sub_type="Sell to Open",
            symbol=QQQ_400P,
            price="2.00",
            net_value=D("200"),
            when=at(4, 2),
            underlying="QQQ",
            order_id=1002,
            leg_count=2,
        ),
        tx(
            id=4,
            sub_type="Sell to Open",
            symbol=QQQ_450C,
            price="2.00",
            net_value=D("200"),
            when=at(4, 2),
            underlying="QQQ",
            order_id=1002,
            leg_count=2,
        ),
        # --- Trade 3: IWM short strangle, opened for +300 and bought back
        #     for -140. A closed winner, so analytics has something to chew on.
        tx(
            id=5,
            sub_type="Sell to Open",
            symbol=IWM_200P,
            price="1.50",
            net_value=D("150"),
            when=at(4, 3),
            underlying="IWM",
            order_id=1003,
            leg_count=2,
        ),
        tx(
            id=6,
            sub_type="Sell to Open",
            symbol=IWM_230C,
            price="1.50",
            net_value=D("150"),
            when=at(4, 3),
            underlying="IWM",
            order_id=1003,
            leg_count=2,
        ),
        tx(
            id=7,
            sub_type="Buy to Close",
            symbol=IWM_200P,
            price="0.80",
            net_value=D("-80"),
            when=at(4, 20),
            underlying="IWM",
            order_id=1004,
            leg_count=2,
        ),
        tx(
            id=8,
            sub_type="Buy to Close",
            symbol=IWM_230C,
            price="0.60",
            net_value=D("-60"),
            when=at(4, 20),
            underlying="IWM",
            order_id=1004,
            leg_count=2,
        ),
    ]


def leg_for(strategy: Strategy, symbol: str) -> Leg:
    matches = [leg for leg in strategy.legs if leg.symbol == symbol]
    assert matches, f"{symbol!r} not among {[leg.symbol for leg in strategy.legs]}"
    return matches[0]


def attach_marks(strategies: dict[str, Strategy]) -> None:
    """Write live prices onto the legs, exactly as MarkService.refresh would.

    MarkService mutates ``Leg`` objects in place — it does not return anything —
    so the contract pnl.py depends on is that ``leg.mark`` is a positive quoted
    price per unit, never a signed cash amount. Setting them by hand here keeps
    the test off the network while exercising that same contract.
    """
    pcs = strategies["SPY"]
    # The short put tripled; the long put more than quintupled at the same
    # moment. Reading either leg alone is the mistake this application exists
    # to prevent.
    leg_for(pcs, SPY_580P).mark = D("8.00")
    leg_for(pcs, SPY_580P).delta = D("-0.72")
    leg_for(pcs, SPY_575P).mark = D("5.50")
    leg_for(pcs, SPY_575P).delta = D("-0.58")

    strangle = strategies["QQQ"]
    leg_for(strangle, QQQ_400P).mark = D("9.00")
    leg_for(strangle, QQQ_400P).delta = D("-0.62")
    leg_for(strangle, QQQ_450C).mark = D("1.00")
    leg_for(strangle, QQQ_450C).delta = D("0.09")


# SPY near 574 is through the 580 short put; QQQ near 396 is through the 400
# short put. Both quotes are consistent with the marks above — a short put worth
# 8.00 with 45 days left is not a long way out of the money.
SPY_QUOTE = UnderlyingQuote(symbol="SPY", last=D("574.00"), mark=D("574.00"), iv=D("0.18"))
QQQ_QUOTE = UnderlyingQuote(symbol="QQQ", last=D("396.00"), mark=D("396.00"), iv=D("0.22"))


@pytest.fixture
def built() -> dict[str, Strategy]:
    """The whole history reconstructed and priced, keyed by underlying."""
    strategies = build_strategies(history(), ACCOUNT)
    by_underlying = {s.underlying: s for s in strategies}
    attach_marks(by_underlying)
    return by_underlying


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "integration" / "tastydesk.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# grouping -> classify
# --------------------------------------------------------------------------- #


def test_the_history_reconstructs_into_three_trades(built: dict[str, Strategy]) -> None:
    assert sorted(built) == ["IWM", "QQQ", "SPY"]
    assert built["SPY"].is_open
    assert built["QQQ"].is_open
    assert not built["IWM"].is_open


def test_grouping_and_classify_agree_on_every_structure(built: dict[str, Strategy]) -> None:
    """grouping calls classify(legs) -> (StrategyType, RiskProfile); re-running
    classify on the legs it stored must give the same answer back."""
    for strategy in built.values():
        if strategy.is_open:
            assert classify(strategy.legs) == (strategy.strategy_type, strategy.risk_profile)

    assert built["SPY"].strategy_type is StrategyType.PUT_CREDIT_SPREAD
    assert built["SPY"].risk_profile is RiskProfile.DEFINED
    assert built["QQQ"].strategy_type is StrategyType.SHORT_STRANGLE
    assert built["QQQ"].risk_profile is RiskProfile.UNDEFINED
    # A closed trade is still named by what it was traded as.
    assert built["IWM"].strategy_type is StrategyType.SHORT_STRANGLE


def test_legs_carry_positive_quoted_prices_and_direction_separately(built: dict[str, Strategy]) -> None:
    """The sign convention at the grouping/pnl seam: prices are never signed."""
    short_put = leg_for(built["SPY"], SPY_580P)
    long_put = leg_for(built["SPY"], SPY_575P)

    assert short_put.open_price == D("2.00")
    assert long_put.open_price == D("1.00")
    assert short_put.direction is Direction.SHORT
    assert long_put.direction is Direction.LONG
    assert short_put.option_type is OptionType.PUT
    assert short_put.strike == D("580")
    assert short_put.expiration == EXPIRY
    assert short_put.multiplier == D("100")

    # Cash flow is where the sign lives: selling took money in.
    assert short_put.open_cash_flow == D("200")
    assert long_put.open_cash_flow == D("-100")
    assert built["SPY"].net_credit == D("100")


def test_grouping_records_dte_at_entry_for_the_analytics_seam(built: dict[str, Strategy]) -> None:
    # Opened 2025-04-01 against a 2025-06-15 expiration.
    assert built["SPY"].dte_at_entry == (EXPIRY - date(2025, 4, 1)).days == 75


# --------------------------------------------------------------------------- #
# marks -> pnl
# --------------------------------------------------------------------------- #


def test_put_credit_spread_matches_the_verified_baseline(built: dict[str, Strategy]) -> None:
    """The brief's canonical numbers, reached through the full chain."""
    pnl = compute_pnl(built["SPY"])

    assert pnl.net_credit == D("100")
    assert pnl.cost_to_close == D("-250")
    assert pnl.open_pnl == D("-150")
    assert pnl.pct_of_credit == D("-1.50")
    # max_loss is a positive magnitude by convention -- it is a size, not a
    # cash flow. The sign lives in open_pnl, which is already negative here.
    assert pnl.max_loss == D("400")
    assert pnl.pct_of_max_loss == D("0.375")
    assert pnl.max_profit == D("100")
    # Progress against the ceiling, reported while behind as well as ahead:
    # -150 of a 100 ceiling is -150%, which is where this trade stands.
    assert pnl.pct_of_max_profit == D("-1.50")
    assert pnl.fully_quoted


def test_short_strangle_is_down_the_same_150_percent(built: dict[str, Strategy]) -> None:
    pnl = compute_pnl(built["QQQ"])

    assert pnl.net_credit == D("400")
    assert pnl.cost_to_close == D("-1000")
    assert pnl.open_pnl == D("-600")
    assert pnl.pct_of_credit == D("-1.50")
    # Undefined risk has no floor, so there is no max loss to be a percentage of.
    assert pnl.max_loss is None
    assert pnl.pct_of_max_loss is None


def test_an_unquoted_leg_refuses_to_report_a_cost(built: dict[str, Strategy]) -> None:
    """The marks/pnl seam under partial data: a half-priced spread reports
    nothing rather than a number that understates what it costs to get out."""
    leg_for(built["SPY"], SPY_575P).mark = None
    pnl = compute_pnl(built["SPY"])

    assert pnl.cost_to_close is None
    assert pnl.open_pnl is None
    assert pnl.pct_of_credit is None
    assert not pnl.fully_quoted
    assert (pnl.quoted_legs, pnl.total_legs) == (1, 2)


# --------------------------------------------------------------------------- #
# pnl -> risk: the cardinal rule, end to end
# --------------------------------------------------------------------------- #


def test_risk_consumes_exactly_what_compute_pnl_produces(built: dict[str, Strategy]) -> None:
    """assess() takes the StrategyPnL object itself, no adapter in between."""
    for strategy, quote in ((built["SPY"], SPY_QUOTE), (built["QQQ"], QQQ_QUOTE)):
        pnl = compute_pnl(strategy)
        assert isinstance(pnl, StrategyPnL)
        risk = assess(strategy, pnl, quote, TODAY, net_liq=D("50000"))
        assert risk.dte == 45
        assert risk.reasons


def test_defined_risk_is_scored_below_undefined_risk_at_the_same_loss(
    built: dict[str, Strategy],
) -> None:
    """THE cardinal rule, measured.

    Both trades are down exactly 150% of their credit. The spread is 37.5% of
    the way to a loss that cannot exceed $400; the strangle has no such ceiling.
    The spread must therefore rank strictly quieter, and must say why.
    """
    spread_pnl = compute_pnl(built["SPY"])
    strangle_pnl = compute_pnl(built["QQQ"])
    assert spread_pnl.pct_of_credit == strangle_pnl.pct_of_credit == D("-1.50")

    spread = assess(built["SPY"], spread_pnl, SPY_QUOTE, TODAY, net_liq=D("50000"))
    strangle = assess(built["QQQ"], strangle_pnl, QQQ_QUOTE, TODAY, net_liq=D("50000"))

    assert spread.level.rank < strangle.level.rank
    assert spread.score < strangle.score
    assert any(r.code == "defined_risk" for r in spread.reasons)
    assert not any(r.code == "defined_risk" for r in strangle.reasons)


def test_no_alarm_is_raised_by_a_single_legs_percentage_move(built: dict[str, Strategy]) -> None:
    """The short 580 put alone went 2.00 -> 8.00, down 300% on its own premium,
    while the spread it belongs to is down 150%. Nothing in the assessment may
    come from that 300%: the long put gained at the same moment."""
    short_put = leg_for(built["SPY"], SPY_580P)
    long_put = leg_for(built["SPY"], SPY_575P)
    leg_level_loss = (short_put.open_price - short_put.mark) / short_put.open_price
    assert leg_level_loss == D("-3")  # -300%, and meaningless

    risk = assess(built["SPY"], compute_pnl(built["SPY"]), SPY_QUOTE, TODAY, net_liq=D("50000"))

    # Every loss-derived reason quotes the strategy's own -150%, never -300%.
    loss_messages = [r.message for r in risk.reasons if r.code in ("loss_vs_credit", "near_max_loss")]
    assert loss_messages
    assert all("300%" not in m for m in loss_messages)
    assert any("150%" in m for m in loss_messages)

    # And the long wing really did move: reading it alone is just as wrong.
    assert (long_put.open_price - long_put.mark) / long_put.open_price == D("-4.5")


def test_physical_alarms_still_fire_on_the_defined_risk_trade(built: dict[str, Strategy]) -> None:
    """Moderation quiets the mark-to-market readings, not the physical ones.
    SPY at 574 is through the 580 short put, and that is a fact about the
    market, not a percentage."""
    risk = assess(built["SPY"], compute_pnl(built["SPY"]), SPY_QUOTE, TODAY, net_liq=D("50000"))
    assert risk.breached
    assert risk.breached_side == "put"


def test_a_strategy_with_no_quotes_is_not_silently_called_safe(built: dict[str, Strategy]) -> None:
    for leg in built["QQQ"].legs:
        leg.mark = None
    risk = assess(built["QQQ"], compute_pnl(built["QQQ"]), QQQ_QUOTE, TODAY)
    assert any(r.code == "partial_quotes" for r in risk.reasons)


# --------------------------------------------------------------------------- #
# analytics over what grouping actually populates
# --------------------------------------------------------------------------- #


def test_performance_sees_only_the_closed_trade(built: dict[str, Strategy]) -> None:
    stats = analytics.performance(list(built.values()))

    assert stats.trades == 1
    assert stats.wins == 1
    # +300 taken in, -140 paid to get out.
    assert stats.total_pnl == D("160")
    assert built["IWM"].realized_pnl == D("160")
    assert stats.avg_pct_of_max_profit_captured == D("160") / D("300")
    assert stats.pct_of_max_profit_n == 1


def test_analytics_degrades_honestly_when_grouping_never_saw_buying_power(
    built: dict[str, Strategy],
) -> None:
    """buying_power_used, iv_rank_at_entry and short_delta_at_entry are not in
    the transaction record, so grouping legitimately leaves them unset. The
    metrics that need them must report a reduced ``n``, never zero-fill."""
    closed = built["IWM"]
    assert closed.buying_power_used is None
    assert closed.iv_rank_at_entry is None
    assert closed.short_delta_at_entry is None

    stats = analytics.performance([closed])
    assert stats.trades == 1
    assert stats.pnl_per_bp_day is None
    assert stats.pnl_per_bp_day_n == 0

    buckets = analytics.by_bucket([closed], "iv_rank_at_entry")
    assert buckets == {analytics.UNKNOWN_BUCKET: buckets[analytics.UNKNOWN_BUCKET]}
    assert buckets[analytics.UNKNOWN_BUCKET].trades == 1


def test_a_trade_missing_buying_power_is_skipped_not_counted_as_zero() -> None:
    """Two closed trades, one with buying power recorded and one without. The
    per-BP metric must be the recorded trade's own number with n=1 — averaging
    in a zero would halve it and look like a real result."""
    recorded = _closed_strategy("AAA", realized=D("100"), buying_power=D("500"))
    missing = _closed_strategy("BBB", realized=D("100"), buying_power=None)

    alone = analytics.performance([recorded])
    both = analytics.performance([recorded, missing])

    assert both.trades == 2
    assert both.pnl_per_bp_day_n == 1
    assert both.pnl_per_bp_day == alone.pnl_per_bp_day
    # $100 on $500 held 10 days.
    assert both.pnl_per_bp_day == D("100") / (D("500") * D("10"))


def _closed_strategy(underlying: str, *, realized: Decimal, buying_power: Decimal | None) -> Strategy:
    """A minimal closed short strangle, held ten days, for the analytics tests."""
    legs = [
        Leg(
            symbol=opt(underlying, "100", "P"),
            instrument_type="Equity Option",
            underlying=underlying,
            direction=Direction.SHORT,
            quantity=D("1"),
            option_type=OptionType.PUT,
            strike=D("100"),
            expiration=EXPIRY,
            open_price=D("1.00"),
        ),
    ]
    return Strategy(
        id=f"{underlying}-1",
        account_number=ACCOUNT,
        underlying=underlying,
        strategy_type=StrategyType.NAKED_PUT,
        risk_profile=RiskProfile.UNDEFINED,
        legs=legs,
        opened_at=datetime(2025, 4, 1, 14, 30, tzinfo=UTC),
        closed_at=datetime(2025, 4, 11, 14, 30, tzinfo=UTC),
        net_credit=realized,
        buying_power_used=buying_power,
    )


# --------------------------------------------------------------------------- #
# the database round trip
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_strategies_survive_the_database_with_identical_numbers(
    built: dict[str, Strategy], db: Database
) -> None:
    """The whole point of the persistence layer: nothing about the trade may
    change on the way through SQLite — not the legs, not the marks, and above
    all not a single Decimal."""
    before = list(built.values())
    before_pnl = {s.id: compute_pnl(s) for s in before}
    before_risk = {
        built["SPY"].id: assess(built["SPY"], before_pnl[built["SPY"].id], SPY_QUOTE, TODAY),
        built["QQQ"].id: assess(built["QQQ"], before_pnl[built["QQQ"].id], QQQ_QUOTE, TODAY),
    }

    await db.save_strategies(before)
    after = await db.load_strategies()

    assert len(after) == len(before)
    by_id = {s.id: s for s in after}

    for original in before:
        reloaded = by_id[original.id]
        assert reloaded.strategy_type is original.strategy_type
        assert reloaded.risk_profile is original.risk_profile
        assert reloaded.opened_at == original.opened_at
        assert reloaded.closed_at == original.closed_at
        assert reloaded.net_credit == original.net_credit
        assert reloaded.closing_cash_flow == original.closing_cash_flow
        assert reloaded.dte_at_entry == original.dte_at_entry
        assert reloaded.order_ids == original.order_ids
        assert reloaded.notes == original.notes

        assert len(reloaded.legs) == len(original.legs)
        for was, now in zip(original.legs, reloaded.legs, strict=True):
            assert now.symbol == was.symbol
            assert now.direction is was.direction
            assert now.quantity == was.quantity
            assert now.multiplier == was.multiplier
            assert now.option_type is was.option_type
            assert now.strike == was.strike
            assert now.expiration == was.expiration
            assert now.open_price == was.open_price
            assert now.mark == was.mark
            assert now.delta == was.delta

        # The numbers, recomputed from the reloaded object rather than copied.
        assert compute_pnl(reloaded) == before_pnl[original.id]

    for sid, quote in ((built["SPY"].id, SPY_QUOTE), (built["QQQ"].id, QQQ_QUOTE)):
        again = assess(by_id[sid], compute_pnl(by_id[sid]), quote, TODAY)
        assert again.level is before_risk[sid].level
        assert again.score == before_risk[sid].score
        assert [r.code for r in again.reasons] == [r.code for r in before_risk[sid].reasons]

    # And the analytics read the same trades the same way.
    assert analytics.performance(after) == analytics.performance(before)


@pytest.mark.asyncio
async def test_the_full_pipeline_agrees_with_itself_from_disk(db: Database) -> None:
    """One more pass with nothing held in memory between the halves: rebuild
    from transactions, save, then start from the database alone."""
    strategies = build_strategies(history(), ACCOUNT)
    attach_marks({s.underlying: s for s in strategies})
    await db.save_strategies(strategies)

    loaded = await db.load_strategies()
    open_trades = [s for s in loaded if s.is_open]
    assert {s.strategy_type for s in open_trades} == {
        StrategyType.PUT_CREDIT_SPREAD,
        StrategyType.SHORT_STRANGLE,
    }

    quotes = {"SPY": SPY_QUOTE, "QQQ": QQQ_QUOTE}
    levels = {
        s.underlying: assess(s, compute_pnl(s), quotes[s.underlying], TODAY, net_liq=D("50000")).level
        for s in open_trades
    }
    assert levels["SPY"].rank < levels["QQQ"].rank
    assert levels["QQQ"] is not DangerLevel.OK

    assert analytics.performance(loaded).total_pnl == D("160")


# --------------------------------------------------------------------------- #
# the package's public API
# --------------------------------------------------------------------------- #


def test_the_engine_exports_one_coherent_public_api() -> None:
    """core/__init__ is the front door; everything it advertises must exist,
    and the names it re-exports must be the same objects the modules define."""
    import tastydesk.core as engine

    assert [name for name in engine.__all__ if not hasattr(engine, name)] == []
    assert sorted(engine.__all__) == sorted(set(engine.__all__)), "duplicate export"

    # The pipeline, reachable by its short names.
    assert engine.build_strategies is build_strategies
    assert engine.classify is classify
    assert engine.compute_pnl is compute_pnl
    assert engine.assess is assess
    assert engine.performance is analytics.performance
    assert engine.Database is Database

    # `classify` the function wins the bare name; the module stays reachable.
    assert engine.classify_mod.classify is engine.classify

    # One meaning for TastyClient: the concrete rate-limited client, not the
    # structural Protocol marks.py declares for its own narrow slice.
    assert engine.TastyClient is engine.client.TastyClient
    assert engine.TastyClient is not engine.marks.TastyClient


def test_the_public_api_runs_the_whole_pipeline() -> None:
    """Nothing but the package namespace, from transactions to a danger level."""
    import tastydesk.core as engine

    strategies = engine.build_strategies(history(), ACCOUNT)
    by_underlying = {s.underlying: s for s in strategies}
    attach_marks(by_underlying)

    spread = by_underlying["SPY"]
    assert spread.strategy_type is engine.StrategyType.PUT_CREDIT_SPREAD
    pnl = engine.compute_pnl(spread)
    assert pnl.open_pnl == D("-150")
    risk = engine.assess(spread, pnl, SPY_QUOTE, TODAY, net_liq=D("50000"))
    assert isinstance(risk.level, engine.DangerLevel)
    assert engine.performance(strategies).trades == 1
