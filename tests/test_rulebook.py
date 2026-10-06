"""His rulebook: rule sets per kind of strategy, and which of his strategies is which.

The names in the matching tests are the ones he actually gave his strategies,
misspellings and all. The point of the module is that "Dmpcc" and "Stangle"
are read the way he meant them, and that a name which could be two things, or
nothing, is asked about rather than guessed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from tastydesk.core import rulebook
from tastydesk.core.indicators import Verdict
from tastydesk.core.models import Direction, Leg, OptionType, RiskProfile, Strategy, StrategyType
from tastydesk.core.pnl import compute_pnl
from tastydesk.core.rulebook import Named

BOOK = rulebook.from_dict(
    {
        "source": "Trading rules folder",
        "sets": [
            {
                "name": "Strangle",
                "take_profit": "50%",
                "stop_multiple": "1.5",
                "dte_exit": 21,
                "max_loss": "2%",
                "rules": [
                    {"section": "entry", "text": "Sell at 60 DTE, 9 delta call and 8 delta put."},
                    {"section": "loss", "text": "Buy back at 2.5x the credit."},
                ],
            },
            {
                "id": "dpmcc",
                "name": "Dynamic PMCC",
                "aliases": ["DPMCC", "poor man's covered call"],
                "stop_of_debit": "0.30",
                "max_bp": "0.10",
                "rules": [{"section": "loss", "text": "Close everything at a 30% net loss."}],
            },
            {"name": "Put credit spread", "aliases": ["weekly put spread"], "take_profit": "0.5"},
            {"name": "Naked put", "stop_multiple": "2"},
            {"name": "Hedge"},
        ],
    }
)


def reading(name: str, product: str = "/ZB") -> str | None:
    return rulebook.assign([Named("x", name, product)], BOOK, {})[0].rules_id


# --------------------------------------------------------------------------
# Reading his names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "product", "expected"),
    [
        ("Dmpcc", "IWM", "dpmcc"),  # two letters swapped
        ("Stangle", "BBY", "strangle"),  # a letter dropped
        ("strangle", "/ZW", "strangle"),
        ("calnderised strangle", "/ZS", "strangle"),
        ("weekly PCS", "SPX", "put-credit-spread"),  # initials
        ("NAKED PUT", "/ZB", "naked-put"),
    ],
)
def test_his_names_are_read_the_way_he_meant_them(name: str, product: str, expected: str) -> None:
    assert reading(name, product) == expected


def test_a_name_that_could_be_two_rule_sets_is_asked_about() -> None:
    # "Headge PPMCC": a hedge, or a PMCC? Both fit half the name.
    [a] = rulebook.assign([Named("h", "Headge PPMCC", "/MES")], BOOK, {})
    assert a.rules_id is None and a.by is None
    assert {c.rules_id for c in a.candidates[:2]} == {"hedge", "dpmcc"}


def test_a_name_that_fits_nothing_is_asked_about() -> None:
    # The product is not part of the idea: "Bull ZB" on /ZB reads as "bull".
    for name, product in (("Bull ZB", "/ZB"), ('"ABA"', "/ZC")):
        [a] = rulebook.assign([Named("n", name, product)], BOOK, {})
        assert a.rules_id is None and a.by is None
        assert all(c.score < rulebook.AUTO_MATCH for c in a.candidates)


def test_the_more_specific_rule_set_wins() -> None:
    book = rulebook.from_dict({"sets": [{"name": "Strangle"}, {"name": "Calendarised strangle"}]})
    [a] = rulebook.assign([Named("c", "calnderised strangle", "/ZS")], book, {})
    assert a.rules_id == "calendarised-strangle"


def test_his_choice_wins_and_survives_until_its_rule_set_is_gone() -> None:
    named = [Named("h", "Headge PPMCC", "/MES"), Named("s", "Stangle", "BBY")]
    chosen = rulebook.assign(named, BOOK, {"h": "hedge", "s": ""})
    assert [(a.rules_id, a.by) for a in chosen] == [("hedge", "you"), (None, "you")]
    # A choice naming a rule set the book no longer has is read again.
    gone = rulebook.assign([Named("d", "Dmpcc", "IWM")], BOOK, {"d": "old-set"})
    assert (gone[0].rules_id, gone[0].by) == ("dpmcc", "name")


# --------------------------------------------------------------------------
# The rulebook itself
# --------------------------------------------------------------------------


def test_numbers_read_as_fractions_or_percentages() -> None:
    strangle = BOOK.get("strangle")
    assert strangle is not None
    assert strangle.take_profit == D("0.5") and strangle.max_loss == D("0.02")
    assert strangle.stop_multiple == D("1.5")


@pytest.mark.parametrize(
    "bad",
    [
        {"sets": [{"name": "X", "take_profit": "150%"}]},
        {"sets": [{"name": "X", "stop_multiple": "lots"}]},
        {"sets": [{"name": "X"}, {"name": "x"}]},
        {"sets": [{"name": "X", "rules": [{"section": "vibes", "text": "trust me"}]}]},
        {"sets": [{"name": "X", "entry_dte": [90, 30]}]},
        {"sets": [{"name": ""}]},
    ],
)
def test_nonsense_is_refused(bad: dict) -> None:
    with pytest.raises(ValueError):
        rulebook.from_dict(bad)


def test_a_rule_set_is_a_plan_with_its_words_beside_the_lines() -> None:
    strangle = BOOK.get("strangle")
    assert strangle is not None
    plan = rulebook.plan_of(strangle)
    assert (plan.take_profit, plan.stop_multiple, plan.dte_exit) == (D("0.5"), D("1.5"), 21)
    assert plan.loss_note == "Buy back at 2.5x the credit."
    assert plan.notes == "Sell at 60 DTE, 9 delta call and 8 delta put."


def test_the_rulebook_round_trips() -> None:
    again = rulebook.from_dict(rulebook.to_dict(BOOK))
    assert again.sets == BOOK.sets


# --------------------------------------------------------------------------
# The lines a plan does not cover
# --------------------------------------------------------------------------


def _pmcc(long_mark: str, short_mark: str) -> Strategy:
    opened = datetime(2026, 6, 1, tzinfo=UTC)
    legs = [
        Leg(
            symbol="IWM   270115C00200000",
            instrument_type="Equity Option",
            underlying="IWM",
            direction=Direction.LONG,
            quantity=D(1),
            option_type=OptionType.CALL,
            strike=D(200),
            expiration=date(2027, 1, 15),
            open_price=D("40.00"),
            mark=D(long_mark),
            opened_at=opened,
        ),
        Leg(
            symbol="IWM   261016C00240000",
            instrument_type="Equity Option",
            underlying="IWM",
            direction=Direction.SHORT,
            quantity=D(1),
            option_type=OptionType.CALL,
            strike=D(240),
            expiration=date(2026, 10, 16),
            open_price=D("2.00"),
            mark=D(short_mark),
            opened_at=opened,
        ),
    ]
    return Strategy(
        id="pmcc",
        account_number="A",
        underlying="IWM",
        strategy_type=StrategyType.DIAGONAL,
        risk_profile=RiskProfile.DEFINED,
        legs=legs,
        opened_at=opened,
        net_credit=D(-3800),
        buying_power_used=D(3800),
    )


def test_a_debit_trade_past_its_stop_is_a_stop_out() -> None:
    dpmcc = BOOK.get("dpmcc")
    assert dpmcc is not None
    # The LEAP cost $4,000 and the call sold against it brought in $200: down
    # $1,400 net of the call is 35% of the LEAP, past 30%. (Against the $3,800
    # left after the credit it would read 36.8%: the rule is about the LEAP.)
    s = _pmcc("25.00", "1.00")
    pnl = compute_pnl(s)
    found = rulebook.checks(s, pnl, dpmcc, D(100000))
    debit = next(c for c in found if c.key == "debit")
    assert debit.hit and debit.reading == "down 35% now"
    calm = Verdict("Leave it", "nothing to do", 5, "calm")
    out = rulebook.verdict(calm, found, dpmcc)
    assert out.action == "Stop out" and out.rank == 0
    assert "your rules: Close everything at a 30% net loss." in out.reason
    # $3,800 of buying power is 3.8% of $100,000, under its 10%.
    bp = next(c for c in found if c.key == "bp")
    assert not bp.hit and bp.reading == "3.8% of net liq now"
    # Covered by its LEAP, the broker counts no buying power: what it cost counts.
    s.buying_power_used = D(0)
    bp = next(c for c in rulebook.checks(s, pnl, dpmcc, D(100000)) if c.key == "bp")
    assert bp.reading == "3.8% of net liq now"


def test_a_credit_trade_is_sized_on_the_stop_in_force() -> None:
    strangle = BOOK.get("strangle")
    assert strangle is not None
    s = _pmcc("25.00", "1.00")
    s.net_credit = D(1000)
    pnl = compute_pnl(s)
    # A 1.5x stop on $1,000 loses $1,500: 1.5% of $100,000, inside 2%...
    [size] = [c for c in rulebook.checks(s, pnl, strangle, D(100000)) if c.key == "size"]
    assert not size.hit and size.reading == "1.5% of net liq at the stop"
    # ...but his own plan's 3x stop would lose $3,000, past it.
    [size] = [c for c in rulebook.checks(s, pnl, strangle, D(100000), stop_multiple=D(3)) if c.key == "size"]
    assert size.hit and size.reading == "3% of net liq at the stop"


# --------------------------------------------------------------------------
# In the app: the plan it fills in, and the list of strategies to ask about
# --------------------------------------------------------------------------


class _Db:
    def __init__(self) -> None:
        self.settings: dict[str, str] = {}
        self.events: list[str] = []

    async def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    async def record(self, kind: str, message: str, **_: object) -> None:
        self.events.append(message)


def _service():  # noqa: ANN202
    from tastydesk.core import playbook
    from tastydesk.core.analytics import RuleSet
    from tastydesk.service import DeskService

    open_pmcc = _pmcc("39.00", "1.50")
    service = DeskService.__new__(DeskService)
    service._db = _Db()
    service._rules = RuleSet()
    service._strategies = [open_pmcc]
    shape = playbook.signature_of([open_pmcc])
    service._named = [
        playbook.NamedStrategy("ns-iwm-dmpcc", "Dmpcc", "IWM", shape, member_ids=[open_pmcc.id]),
        playbook.NamedStrategy("ns-mes-headge-ppmcc", "Headge PPMCC", "/MES", shape),
        playbook.NamedStrategy("ns-zc-aba", '"ABA"', "/ZC", shape),
    ]
    service._plans = {}
    service._rulebook = BOOK
    service._rule_choices = {}
    # New Levy carries the hedge in; "ABA" stays in the old life.
    service._fresh_start = {"since": "2026-10-02", "kept": ["ns-iwm-dmpcc", "ns-mes-headge-ppmcc"]}
    return service


def test_the_app_lists_what_it_read_and_what_to_ask_him() -> None:
    view = _service().rulebook_view()
    rows = {r["named_id"]: r for r in view["strategies"]}
    assert rows["ns-iwm-dmpcc"]["status"] == "matched"
    assert rows["ns-iwm-dmpcc"]["rules_name"] == "Dynamic PMCC"
    assert rows["ns-mes-headge-ppmcc"]["status"] == "ask"
    assert rows["ns-zc-aba"]["status"] == "ask"
    # Only what he trades now is worth a question: the hedge, not the old ABA.
    assert view["needs_you"] == 1
    assert [r["named_id"] for r in view["strategies"]][:2] == ["ns-mes-headge-ppmcc", "ns-iwm-dmpcc"]


def test_the_rulebook_fills_in_a_plan_only_where_he_wrote_none() -> None:
    service = _service()
    named = service._named[0]
    detail = service._plan_detail(named, service._strategies)
    assert detail["source"] == "rulebook"
    assert detail["rulebook"]["name"] == "Dynamic PMCC" and detail["rulebook"]["by"] == "name"
    # Read back as promises: the lines only a rulebook has come in their place.
    assert [s["text"] for s in detail["sentences"]] == [
        "I cut the loss at 30% of what the long side cost. Close everything at a 30% net loss.",
        "One trade uses under 10% of net liq in buying power.",
    ]

    service._plans = {
        named.id: [
            {
                "saved_at": datetime(2026, 9, 1, tzinfo=UTC),
                "plan": {"take_profit": "0.7"},
                "while_open": False,
            }
        ]
    }
    detail = service._plan_detail(named, service._strategies)
    assert detail["source"] == "you"
    assert detail["current"]["take_profit"] == "0.7"
    # The rule set still shows beside his plan: its size lines count either way.
    assert detail["rulebook"]["id"] == "dpmcc"


async def test_his_call_is_kept_and_can_be_undone() -> None:
    service = _service()
    view = await service.choose_rules("ns-mes-headge-ppmcc", "hedge")
    row = next(r for r in view["strategies"] if r["named_id"] == "ns-mes-headge-ppmcc")
    assert (row["status"], row["rules_id"]) == ("chosen", "hedge")
    assert view["needs_you"] == 0
    assert service._db.settings["rulebook_choices"] == '{"ns-mes-headge-ppmcc": "hedge"}'

    view = await service.choose_rules("ns-mes-headge-ppmcc", "auto")
    row = next(r for r in view["strategies"] if r["named_id"] == "ns-mes-headge-ppmcc")
    assert row["status"] == "ask"

    with pytest.raises(ValueError):
        await service.choose_rules("ns-mes-headge-ppmcc", "no-such-set")
    with pytest.raises(KeyError):
        await service.choose_rules("ns-nope", "hedge")


async def test_a_saved_rulebook_is_stored_and_read_back() -> None:
    import json

    service = _service()
    await service.save_rulebook(rulebook.to_dict(BOOK))
    stored = rulebook.from_dict(json.loads(service._db.settings["rulebook"]))
    assert stored.sets == BOOK.sets and stored.saved_at is not None
    assert service._db.events == ["Rulebook saved: 5 rule sets from Trading rules folder."]


def test_a_rule_set_reads_back_in_the_order_of_a_trade() -> None:
    strangle = BOOK.get("strangle")
    assert strangle is not None
    assert rulebook.sentences(strangle) == [
        {"key": "entry", "text": "Sell at 60 DTE, 9 delta call and 8 delta put."},
        {"key": "profit", "text": "I take profit at 50% of max profit."},
        {
            "key": "loss",
            "text": "I cut the loss when it reaches 1.5× the credit I took in. Buy back at 2.5x the credit.",
        },
        {"key": "time", "text": "With 21 days left, I close or roll."},
        {"key": "size", "text": "The loss at my stop stays under 2% of net liq."},
    ]


def test_a_strangle_over_its_credit_line_is_flagged() -> None:
    book = rulebook.from_dict({"sets": [{"name": "Strangle", "max_credit": "1%"}]})
    strangle = book.get("strangle")
    assert strangle is not None
    s = _pmcc("25.00", "1.00")
    s.net_credit = D(1200)
    [credit] = [c for c in rulebook.checks(s, compute_pnl(s), strangle, D(100000)) if c.key == "credit"]
    assert credit.hit and credit.reading == "1.2% of net liq taken in"
    assert rulebook.sentences(strangle) == [
        {"key": "size", "text": "One trade takes in under 1% of net liq in credit."}
    ]


def _leg(direction: Direction, strike: str, expiry: date, opened: str, mark: str, when: datetime) -> Leg:
    return Leg(
        symbol=f"IWM {strike}",
        instrument_type="Equity Option",
        underlying="IWM",
        direction=direction,
        quantity=D(1),
        option_type=OptionType.CALL,
        strike=D(strike),
        expiration=expiry,
        open_price=D(opened),
        mark=D(mark),
        opened_at=when,
    )


def _trade(sid: str, legs: list[Leg], opened: datetime, credit: str, closing: str = "0", closed=None):  # noqa: ANN001, ANN202
    return Strategy(
        id=sid,
        account_number="A",
        underlying="IWM",
        strategy_type=StrategyType.CUSTOM,
        risk_profile=RiskProfile.DEFINED,
        legs=legs,
        opened_at=opened,
        closed_at=closed,
        net_credit=D(credit),
        closing_cash_flow=D(closing),
    )


def test_the_pmcc_stop_counts_the_whole_trade() -> None:
    """His IWM PMCC on 6 Oct: the open legs alone read down 29.6%; the whole
    trade is up about 15%.

    The LEAP was bought on 28 May and rolled on 12 Aug: the first one sold for
    $1,233 more than it cost, and the 250 call bought then for $6,470 is worth
    $4,567. Twenty-four calls were sold against it: six he put in the strategy
    himself (net -$213, and this week's down $14), eighteen the app matched to
    it (net +$1,878). His rule nets all of it — "the loss on the long call, net
    of short-call gains" — and so do the reports; counting only his six picks
    had the card at down 13.9% while the reports showed +$979.
    """
    from tastydesk.core import playbook

    may28 = datetime(2026, 5, 28, 15, tzinfo=UTC)
    aug12 = datetime(2026, 8, 12, 18, tzinfo=UTC)
    oct5 = datetime(2026, 10, 5, 15, tzinfo=UTC)
    leap = _leg(Direction.LONG, "250", date(2027, 6, 17), "64.70", "45.67", aug12)
    short = _leg(Direction.SHORT, "282", date(2026, 10, 12), "2.97", "3.11", oct5)
    trades = [
        # Bought the 230 call for $6,936.26, sold it for $8,169.71, bought the 250.
        _trade("leap", [leap], may28, "-13407.383", "8169.707"),
        _trade("calls", [], datetime(2026, 6, 8, tzinfo=UTC), "100", "-312.82", closed=oct5),
        _trade("today", [short], oct5, "297"),
        # A call sold against an earlier LEAP is another trade: left out.
        _trade("before", [], datetime(2026, 1, 5, tzinfo=UTC), "500", "0", closed=may28),
        # Matched at his 97% bar: in. Matched at 50%: not his strategy's.
        _trade("matched", [], datetime(2026, 6, 5, tzinfo=UTC), "1877.642", "0", closed=oct5),
        _trade("weak", [], datetime(2026, 7, 1, tzinfo=UTC), "999", "0", closed=oct5),
    ]
    service = _service()
    service._strategies = trades
    service._threshold = 0.97
    service.named_matches = lambda _id: {  # type: ignore[method-assign]
        "candidates": [{"trade_id": "matched", "confidence": 0.98}, {"trade_id": "weak", "confidence": 0.5}]
    }
    named = playbook.NamedStrategy(
        "ns-iwm-dmpcc",
        "Dmpcc",
        "IWM",
        playbook.signature_of(trades[:1]),
        member_ids=["leap", "calls", "today", "before"],
    )
    service._named = [named]
    whole = service._whole_trade(named)
    assert whole == (D("980.146"), date(2026, 5, 28))

    dpmcc = BOOK.get("dpmcc")
    assert dpmcc is not None
    merged = _trade("named:ns-iwm-dmpcc", [leap, short], may28, "-6173")
    pnl = compute_pnl(merged)
    [open_only] = [c for c in rulebook.checks(merged, pnl, dpmcc, D(92000)) if c.key == "debit"]
    assert open_only.reading == "down 29.6% now"
    [check] = [
        c for c in rulebook.checks(merged, pnl, dpmcc, D(92000), whole_trade=whole) if c.key == "debit"
    ]
    assert check.reading == "whole trade up 15.1% since 28 May"
    assert not check.hit and check.progress == 0.0
