"""Deciding which legs belong to the same trade.

The failure this guards against is specific. A trader legs in — sells the call,
sells the put a minute later — and the broker reports two orders. Counted
separately the short leg banks its credit and reads as a winner while the long
leg, which was never meant to make money alone, reads as a loser. In one real
book that turned a poor man's covered call into a $5,821 loss beside a $583 win
and made "naked calls lose money" look like a finding rather than an artifact.

The rule throughout: a wrong merge is worse than a missed one. A missed merge is
visible as two trades; a wrong merge is invisible.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from tastydesk.core.models import (
    Direction,
    Leg,
    OptionType,
    RiskProfile,
    Strategy,
    StrategyType,
)
from tastydesk.core.pairing import (
    Confidence,
    PairingRules,
    PairKind,
    apply_pairings,
    find_candidates,
)

T0 = datetime(2026, 3, 2, 15, 30, tzinfo=UTC)
NEAR = date(2026, 4, 17)
FAR = date(2027, 6, 17)


def leg(
    kind: str,
    strike: str,
    direction: str,
    expiration: date = NEAR,
    quantity: str = "1",
) -> Leg:
    return Leg(
        symbol=f"X{kind}{strike}{expiration:%y%m%d}",
        instrument_type="Equity Option",
        underlying="IWM",
        direction=Direction.SHORT if direction == "S" else Direction.LONG,
        quantity=D(quantity),
        option_type=OptionType.CALL if kind == "C" else OptionType.PUT,
        strike=D(strike),
        expiration=expiration,
        open_price=D("2.00"),
    )


def trade(
    sid: str,
    legs: list[Leg],
    *,
    opened: datetime = T0,
    pnl: str = "0",
    underlying: str = "IWM",
    strategy_type: StrategyType = StrategyType.NAKED_CALL,
) -> Strategy:
    return Strategy(
        id=sid,
        account_number="A",
        underlying=underlying,
        strategy_type=strategy_type,
        risk_profile=RiskProfile.UNDEFINED,
        legs=legs,
        opened_at=opened,
        closed_at=opened + timedelta(days=20),
        net_credit=D(pnl),
    )


# ------------------------------------------------------------------ certain


def test_a_legged_in_strangle_is_merged_without_asking() -> None:
    """Nobody sells an unrelated put on the same underlying 60 seconds later."""
    call = trade("a", [leg("C", "300", "S")], pnl="200")
    put = trade("b", [leg("P", "250", "S")], opened=T0 + timedelta(minutes=1), pnl="180")

    found = find_candidates([call, put])

    assert len(found) == 1
    assert found[0].kind is PairKind.STRANGLE
    assert found[0].confidence is Confidence.CERTAIN
    assert apply_pairings(found) == {"a": "a", "b": "a"}


def test_a_legged_in_vertical_is_merged_without_asking() -> None:
    short = trade("a", [leg("C", "585", "S")], pnl="150")
    long_ = trade("b", [leg("C", "595", "L")], opened=T0 + timedelta(minutes=1), pnl="-80")

    found = find_candidates([short, long_])

    assert found[0].kind is PairKind.VERTICAL
    assert found[0].confidence is Confidence.CERTAIN


def test_a_matched_straddle_is_recognised_as_one() -> None:
    call = trade("a", [leg("C", "112", "S")])
    put = trade("b", [leg("P", "112", "S")], opened=T0 + timedelta(minutes=2))

    assert find_candidates([call, put])[0].kind is PairKind.STRADDLE


# ------------------------------------------------------------------- likely


def test_a_diagonal_is_proposed_and_never_assumed() -> None:
    """A long LEAP with a short near call is usually one trade, not always.

    It can genuinely be two decisions, so this is the case that gets asked
    about rather than guessed.
    """
    leap = trade("a", [leg("C", "250", "L", FAR)], pnl="-5821")
    near = trade("b", [leg("C", "288", "S", NEAR)], opened=T0 + timedelta(seconds=30), pnl="583")

    found = find_candidates([leap, near])

    assert found[0].kind is PairKind.DIAGONAL
    assert found[0].confidence is Confidence.LIKELY
    # Nothing is merged until the question is answered.
    assert apply_pairings(found) == {}


def test_an_answer_settles_every_pair_of_that_shape() -> None:
    """What keeps hundreds of trades down to a handful of questions."""
    leap = trade("a", [leg("C", "250", "L", FAR)])
    near = trade("b", [leg("C", "288", "S", NEAR)], opened=T0 + timedelta(seconds=30))
    found = find_candidates([leap, near])

    rules = PairingRules(decisions={found[0].pattern: "merge"})

    assert apply_pairings(found, rules) == {"a": "a", "b": "a"}


def test_calling_a_shape_separate_stops_it_being_asked_again() -> None:
    leap = trade("a", [leg("C", "250", "L", FAR)])
    near = trade("b", [leg("C", "288", "S", NEAR)], opened=T0 + timedelta(seconds=30))
    pattern = find_candidates([leap, near])[0].pattern

    rules = PairingRules(decisions={pattern: "separate"})

    assert find_candidates([leap, near], rules) == []


def test_a_size_mismatch_drops_a_strangle_to_likely() -> None:
    """5 puts against 3 calls may be one idea or two; it is not obvious."""
    put = trade("a", [leg("P", "7000", "S", quantity="5")])
    call = trade("b", [leg("C", "8700", "S", quantity="3")], opened=T0 + timedelta(minutes=1))

    found = find_candidates([put, call])

    assert found[0].kind is PairKind.STRANGLE
    assert found[0].confidence is Confidence.LIKELY


# ---------------------------------------------------------------- left alone


def test_a_different_underlying_is_never_paired() -> None:
    a = trade("a", [leg("C", "300", "S")], underlying="IWM")
    b = trade("b", [leg("P", "250", "S")], opened=T0 + timedelta(minutes=1), underlying="XLE")

    assert find_candidates([a, b]) == []


def test_trades_hours_apart_are_left_alone() -> None:
    a = trade("a", [leg("C", "300", "S")])
    b = trade("b", [leg("P", "250", "S")], opened=T0 + timedelta(hours=9))

    assert find_candidates([a, b]) == []


def test_two_unrelated_short_calls_are_not_a_structure() -> None:
    """Same type, same direction, same expiry, same size is not a spread."""
    a = trade("a", [leg("C", "300", "S")])
    b = trade("b", [leg("C", "310", "S")], opened=T0 + timedelta(minutes=1))

    assert find_candidates([a, b]) == []


def test_an_already_merged_trade_is_not_proposed_again() -> None:
    a = trade("a", [leg("C", "300", "S")])
    a.manual_group = True
    b = trade("b", [leg("P", "250", "S")], opened=T0 + timedelta(minutes=1))

    assert find_candidates([a, b]) == []


def test_two_multi_leg_structures_are_not_merged_on_timing_alone() -> None:
    """Merging two already-complete structures is a much weaker claim."""
    a = trade("a", [leg("C", "300", "S"), leg("C", "310", "L")])
    b = trade("b", [leg("P", "250", "S"), leg("P", "240", "L")], opened=T0 + timedelta(minutes=1))

    assert find_candidates([a, b]) == []


# ------------------------------------------------------------------ chaining


def test_three_legs_opened_together_end_up_in_one_group() -> None:
    """A position legged in over a few minutes is one trade, not two pairs."""
    put = trade("a", [leg("P", "250", "S")])
    call = trade("b", [leg("C", "300", "S")], opened=T0 + timedelta(minutes=1))
    wing = trade("c", [leg("C", "310", "L")], opened=T0 + timedelta(minutes=2))

    groups = apply_pairings(find_candidates([put, call, wing]))

    assert len(set(groups.values())) == 1
    assert set(groups) == {"a", "b", "c"}


def test_the_group_id_is_stable_across_rebuilds() -> None:
    put = trade("a", [leg("P", "250", "S")])
    call = trade("b", [leg("C", "300", "S")], opened=T0 + timedelta(minutes=1))

    first = apply_pairings(find_candidates([put, call]))
    second = apply_pairings(find_candidates([call, put]))

    assert first == second


def test_the_queue_puts_the_biggest_money_first() -> None:
    """The point is to fix the trades that move the numbers."""
    small_a = trade("sa", [leg("C", "300", "S", FAR)], pnl="10")
    small_b = trade("sb", [leg("C", "310", "L", NEAR)], opened=T0 + timedelta(minutes=1), pnl="5")
    big_a = trade("ba", [leg("P", "250", "L", FAR)], opened=T0 + timedelta(days=5), pnl="-5821")
    big_b = trade("bb", [leg("P", "240", "S", NEAR)], opened=T0 + timedelta(days=5, minutes=1), pnl="583")

    found = find_candidates([small_a, small_b, big_a, big_b])

    assert abs(found[0].combined_pnl) > abs(found[-1].combined_pnl)
