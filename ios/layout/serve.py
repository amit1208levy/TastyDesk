"""The Mac's server on the parity check's fake tastytrade account, for the layout check.

Serves http://127.0.0.1:8787 exactly as the Mac does, with every tastytrade
call answered by ``parity/fake_tastytrade.py``, so the pages have a real book to
lay out and no credentials are needed. Run by ``check.mjs``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parity"))

import fake_tastytrade  # noqa: E402


def main() -> None:
    fake_tastytrade.install()

    import uvicorn

    from tastydesk.api import main as api
    from tastydesk.core.auth import SessionManager
    from tastydesk.core.client import TastyClient
    from tastydesk.core.db import Database
    from tastydesk.service import DeskService

    db = Path(tempfile.mkdtemp(prefix="tastydesk-layout-")) / "layout.db"
    api.build_service = lambda: DeskService(Database(db), TastyClient(SessionManager()))
    uvicorn.run(api.app, host="127.0.0.1", port=8787, log_level="warning")


if __name__ == "__main__":
    main()
