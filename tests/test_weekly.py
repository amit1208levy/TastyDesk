"""The week in review: its bounds, its readings, and whether it corrected the book."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from tastydesk.core import weekly
from tastydesk.core.analytics import CurveDay


def test_a_weekend_reviews_the_week_just_ended() -> None:
    friday = weekly.week_of(date(2026, 11, 20))
    assert (friday.monday, friday.friday) == (date(2026, 11, 16), date(2026, 11, 20))
    assert weekly.week_of(date(2026, 11, 21)) == friday  # Saturday
    assert weekly.week_of(date(2026, 11, 22)) == friday  # Sunday
    assert weekly.week_of(date(2026, 11, 23)).monday == date(2026, 11, 23)
    assert friday.label == "16–20 Nov 2026"


def _row(day: date, **kw: str) -> dict[str, object]:
    base = {"day": day, "net_liq": D(100000), "bp_used": None, "delta": None, "theta": None, "vega": None}
    base.update({k: D(v) for k, v in kw.items()})
    return base


def test_the_start_is_the_last_reading_before_monday_and_a_gap_is_not_filled() -> None:
    week = weekly.week_of(date(2026, 11, 20))
    rows = [_row(date(2026, 11, 12)), _row(date(2026, 11, 14)), _row(date(2026, 11, 18))]
    start, end = weekly.week_snapshots(rows, week)
    assert start is not None and start["day"] == date(2026, 11, 14)
    assert end is not None and end["day"] == date(2026, 11, 18)

    # Nothing recorded inside the week: no end, rather than last week's.
    start, end = weekly.week_snapshots(rows[:2], week)
    assert end is None
    # A reading three weeks old does not stand for the week's start.
    start, _ = weekly.week_snapshots([_row(date(2026, 10, 20)), rows[2]], week)
    assert start is None


def test_the_scorecard_reads_which_way_each_part_moved() -> None:
    start = _row(date(2026, 11, 13), theta="150", vega="-450", delta="60", bp_used="30000")
    end = _row(date(2026, 11, 20), theta="290", vega="-300", delta="250", bp_used="45000")
    rows = {r.key: r for r in weekly.scorecard(
        start, end, overage_start=D("0.05"), overage_end=D("0.05"), week_return=D("0.006")
    )}
    assert rows["theta"].direction == "closer"  # 0.15% -> 0.29%, toward 0.3%
    assert rows["vega"].direction == "closer"  # 3.0x -> 1.03x
    assert rows["delta"].direction == "further"  # inside ±200, then 250 past it
    assert rows["bp"].direction == "closer"  # 30% -> 45%, into 40-50%
    assert rows["allocation"].direction == "same"
    assert rows["profit"].direction == "met"
    assert rows["theta"].start_text == "0.15%" and rows["theta"].end_text == "0.29%"


def test_the_verdict_counts_toward_against_away() -> None:
    def dim(direction: str, key: str = "theta") -> weekly.Dimension:
        return weekly.Dimension(key, key, "", None, None, "", "", direction)

    good = weekly.verdict([dim("closer"), dim("met"), dim("further"), dim("same")])
    assert (good.label, good.text) == ("Correcting", "2 of 4 moved toward the plan")
    bad = weekly.verdict([dim("further"), dim("missed"), dim("closer")])
    assert bad.label == "Not constructive"
    assert weekly.verdict([dim("closer"), dim("further")]).label == "Mixed"


def test_a_week_before_the_greeks_were_kept_is_judged_on_profit_alone() -> None:
    rows = weekly.scorecard(None, None, overage_start=None, overage_end=None, week_return=D("-0.01"))
    v = weekly.verdict(rows)
    assert v.measured == 1 and v.label == "Not constructive"
    assert not v.greeks_recorded and v.notes


def test_the_week_is_measured_from_the_close_before_it() -> None:
    days = [
        CurveDay(date(2026, 11, 13), D(100000), D(0), D(0), D("0"), D("0")),
        CurveDay(date(2026, 11, 16), D(101000), D(0), D(1000), D("0.01"), D("0.01")),
        CurveDay(date(2026, 11, 20), D(99990), D(0), D(-10), D("-0.0001"), D("-0.02")),
    ]
    change = weekly.week_change(days, weekly.week_of(date(2026, 11, 20)))
    assert change is not None
    assert change.start_day == date(2026, 11, 13) and change.end_day == date(2026, 11, 20)
    assert change.gain == D(-10)
    assert change.spy == D("-0.02")


def test_allocation_counts_only_what_runs_over_each_limit() -> None:
    # 11x 30% of the allowed half = 15% of net liq; spec 30% of it = 15%.
    rows, over = weekly.allocation({"11x": D(10000), "Spec": D(25000)}, D(100000))
    by = {r.bucket: r for r in rows}
    assert by["11x"].over == 0
    assert by["Spec"].over == D("0.10")
    assert over == D("0.10")
    # Kept outside the plan on purpose: not held against it.
    _, over = weekly.allocation({"Outside": D(3000)}, D(100000), excused=D(3000))
    assert over == 0


def test_takeaways_say_only_what_was_measured() -> None:
    rows = weekly.scorecard(None, None, overage_start=None, overage_end=None, week_return=None)
    notes = weekly.takeaways(
        rows,
        gain=None,
        week_return=None,
        spy_return=None,
        closed=0,
        banked=D(0),
        big_losses=[],
        losses=0,
        broken_rules=[],
    )
    assert notes == []
    notes = weekly.takeaways(
        rows,
        gain=D(-1200),
        week_return=D("-0.013"),
        spy_return=D("0.004"),
        closed=3,
        banked=D(-400),
        big_losses=["/MESH7 campain 11x"],
        losses=2,
        broken_rules=[("Never lose more than 2%", 1)],
    )
    assert notes[0] == "The account lost $1,200 (-1.30%) while SPY moved 0.40%."
    assert any("2% of net liq: /MESH7 campain 11x" in n for n in notes)
