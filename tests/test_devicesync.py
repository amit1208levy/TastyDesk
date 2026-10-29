"""Keeping what is set by hand the same on every device (core/devicesync.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tastydesk.core import devicesync
from tastydesk.core.db import Database

SECRET = "the-client-secret"
# Later than any row the tests write (those carry the real time).
T0 = datetime(2030, 1, 2, 9, 0, tzinfo=UTC)


async def _device(tmp_path: Path, name: str, account: str = "5WX00001") -> Database:
    db = Database(tmp_path / f"{name}.db")
    await db.connect()
    await db.migrate()
    await db.connection.execute(
        "INSERT INTO transactions (id, account_number) VALUES (?, ?)", (1, account)
    )
    await db.connection.commit()
    return db


async def _sync(
    db: Database,
    folder: dict[str, bytes],
    *,
    label: str,
    at: datetime,
    secret: str = SECRET,
) -> devicesync.Step:
    out = await devicesync.step(db, secret=secret, label=label, incoming=dict(folder), now=at)
    if out.outgoing is not None:
        folder[out.filename] = out.outgoing
    return out


async def _named(db: Database) -> dict[str, tuple[str, list[str]]]:
    return {n["id"]: (n["name"], n["member_ids"]) for n in await db.get_named_strategies()}


@pytest.fixture
async def pair(tmp_path: Path):
    mac = await _device(tmp_path, "mac")
    phone = await _device(tmp_path, "phone")
    yield mac, phone
    await mac.close()
    await phone.close()


async def test_what_is_set_on_one_device_arrives_on_the_other(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await mac.save_named_strategy(
        {"id": "n1", "name": "/ZB straddle", "product": "/ZB", "signature": {"a": 1}}
    )
    await mac.set_named_members("n1", ["t1", "t2"])
    await mac.set_setting("match_threshold", "0.8")
    await mac.set_manual_override("s2", "s1")
    await mac.set_pairing_rule("IWM leap+call", "merge", "one diagonal")
    await mac.set_roll_decision("c1", "o1", "separate")

    await _sync(mac, folder, label="Mac", at=T0)
    step = await _sync(phone, folder, label="iPhone", at=T0 + timedelta(seconds=5))

    assert await _named(phone) == {"n1": ("/ZB straddle", ["t1", "t2"])}
    assert await phone.get_setting("match_threshold") == "0.8"
    assert await phone.get_manual_overrides() == {"s2": "s1"}
    assert await phone.get_pairing_rules() == {"IWM leap+call": "merge"}
    assert await phone.roll_decisions() == {("c1", "o1"): "separate"}
    # Grouping answers changed, so the caller knows to rebuild.
    assert devicesync.REGROUP & step.applied


async def test_edits_and_deletions_travel_back(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await mac.save_named_strategy({"id": "n1", "name": "old name", "product": "/ZB", "signature": {}})
    await mac.set_named_members("n1", ["t1", "t2"])
    await mac.set_manual_override("s2", "s1")
    await _sync(mac, folder, label="Mac", at=T0)
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(seconds=1))

    await phone.save_named_strategy({"id": "n1", "name": "new name", "product": "/ZB", "signature": {}})
    await phone.remove_named_member("n1", "t2")
    await phone.clear_manual_override("s2")
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(minutes=1))
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(minutes=2))

    assert await _named(mac) == {"n1": ("new name", ["t1"])}
    assert await mac.get_manual_overrides() == {}


async def test_a_deleted_strategy_takes_its_members_with_it(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await mac.save_named_strategy({"id": "n1", "name": "x", "product": "SPY", "signature": {}})
    await mac.set_named_members("n1", ["t1"])
    await _sync(mac, folder, label="Mac", at=T0)
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(seconds=1))

    await phone.delete_named_strategy("n1")
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(minutes=1))
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(minutes=2))
    assert await _named(mac) == {}
    async with mac.connection.execute("SELECT COUNT(*) FROM named_strategy_members") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_the_later_of_two_edits_wins_on_both(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await mac.set_setting("position_columns", '["a"]')
    await _sync(mac, folder, label="Mac", at=T0)
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(seconds=1))

    # Both change it before either hears of the other; the phone's is later.
    await mac.set_setting("position_columns", '["mac"]')
    await phone.set_setting("position_columns", '["phone"]')
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(minutes=1))
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(minutes=2))
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(minutes=3))

    assert await mac.get_setting("position_columns") == '["phone"]'
    assert await phone.get_setting("position_columns") == '["phone"]'


async def test_plans_and_questions_travel_and_an_answer_comes_back(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await phone.save_trade_plan("n1", {"take": 50}, T0, while_open=False)
    question = await phone.ask("Should I roll IWM?")
    assert question
    await _sync(phone, folder, label="iPhone", at=T0)
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(seconds=1))

    assert (await mac.get_trade_plans())["n1"][0]["plan"] == {"take": 50}
    pending = await mac.pending_questions()
    assert [q["question"] for q in pending] == ["Should I roll IWM?"]

    # Answered on the Mac (by a Claude session), read on the phone.
    await mac.answer_question(pending[0]["id"], "Not yet.")
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(minutes=1))
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(minutes=2))
    thread = await phone.question_thread()
    assert [(q["question"], q["answer"]) for q in thread] == [("Should I roll IWM?", "Not yet.")]


async def test_device_settings_stay_on_their_device(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await mac.set_setting("last_sync", "2026-10-29T08:00:00+00:00")
    await _sync(mac, folder, label="Mac", at=T0)
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(seconds=1))
    assert await phone.get_setting("last_sync") is None
    ours = await phone.get_setting(devicesync.DEVICE_SETTING)
    assert ours and ours != await mac.get_setting(devicesync.DEVICE_SETTING)


async def test_a_different_secret_reads_nothing_and_says_so(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    await mac.set_setting("match_threshold", "0.9")
    await _sync(mac, folder, label="Mac", at=T0)
    step = await _sync(phone, folder, label="iPhone", at=T0 + timedelta(seconds=1), secret="another")
    assert await phone.get_setting("match_threshold") is None
    assert any("different client secret" in p for p in step.report["problems"])
    # The secret is never in a message, and the file itself does not carry it.
    assert SECRET not in json.dumps(step.report)
    assert SECRET.encode() not in folder[next(iter(folder))]


async def test_another_tastytrade_account_is_left_alone(tmp_path: Path) -> None:
    mine = await _device(tmp_path, "mine", account="5WX00001")
    theirs = await _device(tmp_path, "theirs", account="9ZZ99999")
    try:
        folder: dict[str, bytes] = {}
        await theirs.set_setting("match_threshold", "0.6")
        await _sync(theirs, folder, label="Mac", at=T0)
        step = await _sync(mine, folder, label="iPhone", at=T0 + timedelta(seconds=1))
        assert await mine.get_setting("match_threshold") is None
        assert any("different tastytrade account" in p for p in step.report["problems"])
    finally:
        await mine.close()
        await theirs.close()


async def test_nothing_is_shared_before_the_account_is_known(tmp_path: Path) -> None:
    db = Database(tmp_path / "new.db")
    await db.connect()
    await db.migrate()
    try:
        step = await devicesync.step(db, secret=SECRET, label="iPhone", incoming={}, now=T0)
        assert step.outgoing is None
        assert step.report["state"] == "waiting"
    finally:
        await db.close()


async def test_a_quiet_step_writes_nothing(pair) -> None:
    mac, _ = pair
    folder: dict[str, bytes] = {}
    await mac.set_setting("match_threshold", "0.8")
    first = await _sync(mac, folder, label="Mac", at=T0)
    assert first.outgoing is not None
    again = await _sync(mac, folder, label="Mac", at=T0 + timedelta(seconds=20))
    assert again.outgoing is None
    assert again.report["sent"] == 0


async def test_a_file_from_a_newer_version_is_skipped(pair) -> None:
    mac, _ = pair
    future = json.dumps({"tastydesk_sync": 99, "device": "x", "nonce": "", "data": ""}).encode()
    step = await _sync(mac, {"x.tdsync": future}, label="Mac", at=T0)
    assert any("newer Tasty Desk" in p for p in step.report["problems"])


async def test_a_device_joining_late_does_not_overwrite_newer_settings(pair) -> None:
    mac, phone = pair
    folder: dict[str, bytes] = {}
    # Set on the phone long ago, then on the Mac recently; the phone joins last.
    sql = "INSERT INTO settings (key, value, set_at) VALUES ('match_threshold', ?, ?)"
    await phone.connection.execute(sql, ("0.6", "2026-01-02T10:00:00+00:00"))
    await phone.connection.commit()
    await mac.connection.execute(sql, ("0.9", "2026-10-01T10:00:00+02:00"))
    await mac.connection.commit()
    await _sync(mac, folder, label="Mac", at=T0)
    await _sync(phone, folder, label="iPhone", at=T0 + timedelta(hours=1))
    await _sync(mac, folder, label="Mac", at=T0 + timedelta(hours=2))
    assert await phone.get_setting("match_threshold") == "0.9"
    assert await mac.get_setting("match_threshold") == "0.9"
