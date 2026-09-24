"""Strategies the user names, and finding older trades shaped like them.

The rule the user set: match on shape alone, and never guess. Strikes and
deltas are execution, not identity — selling the 0.20 and selling the 0.35 is
the same strategy done differently, and folding distance into the identity
would split one idea into a dozen. The name is read for the user's benefit and
never allowed to decide anything, because shorthand leaves things out.
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
from tastydesk.core.playbook import (
    MATCH_THRESHOLD,
    NamedStrategy,
    find_matches,
    product_of,
    read_name,
    score_match,
    signature_of,
)

T0 = datetime(2026, 3, 2, 15, 30, tzinfo=UTC)
EXP = date(2026, 6, 19)
FAR = date(2026, 9, 18)


def leg(kind: str, strike: str, side: str, expiration: date = EXP, qty: str = "1") -> Leg:
    return Leg(
        symbol=f"{kind}{strike}{expiration:%y%m%d}",
        instrument_type="Future Option",
        underlying="/ZBZ6",
        direction=Direction.SHORT if side == "S" else Direction.LONG,
        quantity=D(qty),
        option_type=OptionType.CALL if kind == "C" else OptionType.PUT,
        strike=D(strike),
        expiration=expiration,
        open_price=D("1.00"),
    )


def trade(
    sid: str,
    legs: list[Leg],
    *,
    underlying: str = "/ZBZ6",
    opened: datetime = T0,
    pnl: str = "100",
    structure: StrategyType = StrategyType.SHORT_STRANGLE,
) -> Strategy:
    return Strategy(
        id=sid,
        account_number="A",
        underlying=underlying,
        strategy_type=structure,
        risk_profile=RiskProfile.UNDEFINED,
        legs=legs,
        opened_at=opened,
        closed_at=opened + timedelta(days=14),
        net_credit=D(pnl),
    )


# --------------------------------------------------------------- the product


def test_a_contract_month_reduces_to_its_product() -> None:
    """A /ZS idea has to match a /ZS trade in a different month."""
    assert product_of("/ZSF7") == "/ZS"
    assert product_of("/ZSX6") == "/ZS"
    assert product_of("/MESZ6") == "/MES"
    assert product_of("/RTYZ6") == "/RTY"
    assert product_of("IWM") == "IWM"


# ------------------------------------------------------------------ the name


def test_the_name_is_read_but_never_decides() -> None:
    """Shown so the user can see what was understood; not an input to matching."""
    assert read_name("my ZB straddle") == "a straddle"
    assert read_name("PMCC on IWM") == "a poor man's covered call"
    # An idiosyncratic name is fine and simply tells us nothing.
    assert read_name("the thing i do on oil") is None

    # And the reading changes no score.
    sig = signature_of([trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])])
    other = signature_of([trade("b", [leg("P", "104", "S"), leg("C", "127", "S")])])
    score, _, _ = score_match(sig, other)
    assert score >= MATCH_THRESHOLD


# ----------------------------------------------------------------- the shape


def test_the_same_shape_at_different_strikes_matches() -> None:
    """Strikes are execution, not identity."""
    original = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    later = trade("b", [leg("P", "95", "S"), leg("C", "135", "S")], opened=T0 + timedelta(days=40))

    score, reasons, misses = score_match(signature_of([original]), signature_of([later]))

    assert score >= MATCH_THRESHOLD
    assert misses == []
    assert any("identical legs" in r for r in reasons)


def test_a_different_product_is_not_a_weaker_match_but_no_match() -> None:
    original = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    other = trade("b", [leg("P", "110", "S"), leg("C", "120", "S")], underlying="/CLZ6")

    score, _, misses = score_match(signature_of([original]), signature_of([other]))

    assert score == 0.0
    assert any("different product" in m for m in misses)


def test_a_missing_leg_falls_well_below_the_threshold() -> None:
    strangle = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    just_the_put = trade("b", [leg("P", "110", "S")])

    score, _, misses = score_match(signature_of([strangle]), signature_of([just_the_put]))

    assert score < MATCH_THRESHOLD
    assert misses


def test_a_split_expiry_version_is_not_the_same_strategy() -> None:
    """A strangle and a diagonal are different ideas, whatever the legs say."""
    same_expiry = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    split = trade("b", [leg("P", "110", "S"), leg("C", "120", "S", FAR)])

    score, _, misses = score_match(signature_of([same_expiry]), signature_of([split]))

    assert score < MATCH_THRESHOLD
    assert any("expiries" in m for m in misses)


def test_the_same_ratio_matches_whatever_the_absolute_size() -> None:
    """2:2 is the same shape as 1:1 — size is position sizing, not identity."""
    one_lot = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    two_lots = trade("b", [leg("P", "110", "S", qty="2"), leg("C", "120", "S", qty="2")])

    score, _, _ = score_match(signature_of([one_lot]), signature_of([two_lots]))

    assert score >= MATCH_THRESHOLD


def test_an_uneven_ratio_is_flagged_and_not_counted() -> None:
    even = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    lopsided = trade("b", [leg("P", "110", "S", qty="5"), leg("C", "120", "S", qty="3")])

    score, _, misses = score_match(signature_of([even]), signature_of([lopsided]))

    assert score < MATCH_THRESHOLD
    assert any("ratio" in m for m in misses)


# --------------------------------------------------------------- the matching


def test_matching_finds_the_repeats_and_nothing_else() -> None:
    seed = trade("seed", [leg("P", "110", "S"), leg("C", "120", "S")])
    repeat = trade("repeat", [leg("P", "104", "S"), leg("C", "127", "S")], opened=T0 + timedelta(days=30))
    unrelated = trade("naked", [leg("P", "100", "S")], opened=T0 + timedelta(days=31))
    wrong_product = trade(
        "oil",
        [leg("P", "110", "S"), leg("C", "120", "S")],
        underlying="/CLZ6",
        opened=T0 + timedelta(days=32),
    )

    named = NamedStrategy(
        id="s1",
        name="my ZB strangle",
        product="/ZB",
        signature=signature_of([seed]),
        member_ids=["seed"],
    )
    found = find_matches(named, [seed, repeat, unrelated, wrong_product])
    confident = [m for m in found if m.confident]

    assert [m.trade_ids[0] for m in confident] == ["repeat"]
    # The seed itself is a member, never re-proposed.
    assert all("seed" not in m.trade_ids for m in found)
    # A different product does not even appear as a near miss.
    assert all("oil" not in m.trade_ids for m in found)


def test_a_trade_already_in_another_strategy_is_left_alone() -> None:
    seed = trade("seed", [leg("P", "110", "S"), leg("C", "120", "S")])
    repeat = trade("repeat", [leg("P", "104", "S"), leg("C", "127", "S")])

    named = NamedStrategy(
        id="s1", name="x", product="/ZB", signature=signature_of([seed]), member_ids=["seed"]
    )
    found = find_matches(named, [seed, repeat], exclude_ids={"repeat"})

    assert found == []


def test_nothing_reaches_confidence_by_timing_alone() -> None:
    """Corroboration, not identity: a shape mismatch cannot be voted up."""
    seed = trade("seed", [leg("P", "110", "S"), leg("C", "120", "S")])
    single = trade("single", [leg("P", "110", "S")], opened=T0)

    score, _, _ = score_match(signature_of([seed]), signature_of([single]))

    assert score < MATCH_THRESHOLD


# ------------------------------------------- naming a strategy from history


def test_repeats_of_one_idea_describe_one_of_them() -> None:
    """Four strangles picked out of the history are a strangle.

    Naming a strategy from past trades means "these are each the same idea".
    Pooling their legs described a /ZB strangle as seven short calls and four
    short puts, which is a description of nothing and is what the user then
    reads on the card.
    """
    first = trade("a", [leg("P", "110", "S"), leg("C", "120", "S")])
    second = trade("b", [leg("P", "104", "S"), leg("C", "127", "S")], opened=T0 + timedelta(days=40))
    # Rolled twice, so its record carries the legs of every roll. It is still
    # the same idea, and it must not be the one that describes it.
    rolled = trade(
        "c",
        [
            leg("P", "101", "S"),
            leg("C", "130", "S"),
            leg("P", "99", "S"),
            leg("C", "133", "S"),
            leg("P", "97", "S"),
            leg("C", "136", "S"),
        ],
        opened=T0 + timedelta(days=90),
    )

    sig = signature_of([first, second, rolled])

    assert len(sig.legs) == 2
    assert sig.describe() == signature_of([first]).describe()


def test_parts_of_one_position_still_pool() -> None:
    """The other meaning of picking two trades, which must not change.

    A LEAP bought in July and a call sold against it in September are one
    position legged in, not two versions of an idea, and the shape is both.
    """
    leap = trade("leap", [leg("C", "90", "L")], structure=StrategyType.LONG_CALL)
    weekly = trade(
        "weekly",
        [leg("C", "120", "S")],
        opened=T0 + timedelta(days=60),
        structure=StrategyType.NAKED_CALL,
    )

    sig = signature_of([leap, weekly])

    assert len(sig.legs) == 2
    assert {s.side for s in sig.legs} == {"long", "short"}
