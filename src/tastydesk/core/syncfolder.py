"""The Mac's side of keeping hand-set data the same on every device.

The shared files live in a folder in iCloud Drive, "Tasty Desk Sync", which the
iPhone app is pointed at once. Every 20 seconds this reads every sync file in
it, runs one step (core/devicesync.py), and writes this Mac's own file back
when it changed. The iPhone does the same from its side of the app.

``TASTYDESK_SYNC_DIR`` names another folder instead (tests, or a Mac whose
iCloud Drive lives elsewhere). Without it, and without iCloud Drive turned on,
nothing is shared and the status says why.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from tastydesk.core.devicesync import SUFFIX

if TYPE_CHECKING:
    from tastydesk.service import DeskService

logger = logging.getLogger(__name__)

FOLDER_NAME = "Tasty Desk Sync"
EVERY_SECONDS = 20
_ICLOUD_DRIVE = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"


def folder() -> Path | None:
    override = os.environ.get("TASTYDESK_SYNC_DIR")
    if override:
        return Path(override).expanduser()
    if _ICLOUD_DRIVE.is_dir():
        return _ICLOUD_DRIVE / FOLDER_NAME
    return None


def _read(where: Path) -> dict[str, bytes]:
    where.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    for path in where.iterdir():
        name = path.name
        if name.endswith(SUFFIX) and not name.startswith("."):
            files[name] = path.read_bytes()
        elif name.startswith(".") and name.endswith(SUFFIX + ".icloud"):
            # Moved to the cloud to save space ("Optimize Mac Storage"): ask
            # for it back and read it on a later round.
            real = where / name[1 : -len(".icloud")]
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.Popen(  # noqa: S603 - fixed program, path from our own folder
                    ["/usr/bin/brctl", "download", str(real)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    return files


def _write(where: Path, name: str, data: bytes) -> None:
    """Beside the real file first, then moved over it: never half a file in the folder."""
    temp = where / f".{name}.tmp"
    temp.write_bytes(data)
    os.replace(temp, where / name)


async def run(service: DeskService, *, every: float = EVERY_SECONDS) -> None:
    where = folder()
    if where is None:
        service.device_sync_where = None
        logger.info("iCloud Drive is not on: hand-set data stays on this Mac")
        return
    in_icloud = where.parent == _ICLOUD_DRIVE
    service.device_sync_where = f"iCloud Drive › {where.name}" if in_icloud else str(where)
    while True:
        try:
            files = await asyncio.to_thread(_read, where)
            step = await service.device_sync(files, label="Mac")
            if step.outgoing is not None:
                await asyncio.to_thread(_write, where, step.filename, step.outgoing)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad round must not end sharing
            logger.exception("Sharing with other devices failed this round")
        await asyncio.sleep(every)
