"""The Mac's folder side of sharing hand-set data (core/syncfolder.py, service.device_sync)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tastydesk.core import auth, devicesync, syncfolder
from tastydesk.core.db import Database
from tastydesk.service import DeskService


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth, "load_credentials", lambda: auth.Credentials(client_secret="s3cret", refresh_token="t")
    )


_SIGNATURE = {
    "product": "SPY",
    "legs": [{"right": "P", "side": "short", "ratio": 1}],
    "expiry_pattern": "single",
    "window_minutes": 5,
}


async def _service(tmp_path: Path, name: str) -> DeskService:
    db = Database(tmp_path / f"{name}.db")
    await db.connect()
    await db.migrate()
    await db.connection.execute("INSERT INTO transactions (id, account_number) VALUES (1, 'ACC')")
    await db.connection.commit()
    service = DeskService(db, None)  # type: ignore[arg-type] - never reaches the broker here
    await service.load_named()
    return service


async def test_the_loop_writes_this_macs_file_and_skips_what_is_not_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "Tasty Desk Sync"
    folder.mkdir()
    (folder / "notes.txt").write_text("not ours")
    (folder / ".abc.tdsync.icloud").write_bytes(b"placeholder")
    monkeypatch.setenv("TASTYDESK_SYNC_DIR", str(folder))

    service = await _service(tmp_path, "mac")
    try:
        await service._db.set_setting("match_threshold", "0.7")  # noqa: SLF001
        task = asyncio.create_task(syncfolder.run(service, every=0.05))
        for _ in range(100):
            if any(p.suffix == devicesync.SUFFIX for p in folder.iterdir()):
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        written = [p for p in folder.iterdir() if p.suffix == devicesync.SUFFIX]
        assert len(written) == 1
        assert not any(p.name.endswith(".tmp") for p in folder.iterdir())
        assert service.device_sync_status()["state"] == "ok"
    finally:
        await service._db.close()  # noqa: SLF001


async def test_what_arrives_is_in_the_service_at_once(tmp_path: Path) -> None:
    mac = await _service(tmp_path, "mac")
    phone = await _service(tmp_path, "phone")
    try:
        await phone._db.save_named_strategy(  # noqa: SLF001
            {"id": "n1", "name": "Phone's", "product": "SPY", "signature": _SIGNATURE}
        )
        out = await phone.device_sync({}, label="iPhone")
        assert out.outgoing is not None
        step = await mac.device_sync({out.filename: out.outgoing}, label="Mac")
        assert "named" in step.applied
        # Loaded, not only stored: the pages read the service.
        assert [n.name for n in mac._named] == ["Phone's"]  # noqa: SLF001
        status = mac.device_sync_status()
        assert status["received"] == 1
        assert [d["label"] for d in status["devices"]] == ["iPhone"]
        assert "device" not in status
    finally:
        await mac._db.close()  # noqa: SLF001
        await phone._db.close()  # noqa: SLF001


async def test_without_keys_nothing_is_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> auth.Credentials:
        raise auth.CredentialError("not set up")

    monkeypatch.setattr(auth, "load_credentials", missing)
    service = await _service(tmp_path, "mac")
    try:
        step = await service.device_sync({}, label="Mac")
        assert step.outgoing is None
        assert service.device_sync_status()["state"] == "waiting"
    finally:
        await service._db.close()  # noqa: SLF001


def test_no_icloud_drive_means_no_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TASTYDESK_SYNC_DIR", raising=False)
    monkeypatch.setattr(syncfolder, "_ICLOUD_DRIVE", tmp_path / "absent")
    assert syncfolder.folder() is None
    monkeypatch.setattr(syncfolder, "_ICLOUD_DRIVE", tmp_path)
    assert syncfolder.folder() == tmp_path / "Tasty Desk Sync"
