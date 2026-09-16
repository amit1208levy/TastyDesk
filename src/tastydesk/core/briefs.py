"""Storage for the daily brief.

The brief is written by a scheduled Claude Code session rather than by an API
call to a model, which is a deliberate choice: it means no Anthropic key has to
live on this machine alongside the brokerage credential, and there is no
per-call cost. A file is the natural interface for an agent, so that is what
this is — markdown on disk, read back by the dashboard.

Briefs live beside the database, under the app support directory, not in the
project folder. They quote account figures, and the project folder is synced to
iCloud.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

__all__ = ["Brief", "BriefStore", "DEFAULT_BRIEF_DIR"]

DEFAULT_BRIEF_DIR = (
    Path.home() / "Library" / "Application Support" / "TastyDesk" / "briefs"
)

_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


@dataclass(frozen=True, slots=True)
class Brief:
    on: date
    markdown: str
    written_at: datetime

    @property
    def is_stale(self) -> bool:
        """True when the newest brief is not today's.

        Shown rather than hidden: a brief from three days ago quietly presented
        as this morning's would be worse than no brief.
        """
        return self.on != date.today()


class BriefStore:
    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or DEFAULT_BRIEF_DIR

    @property
    def directory(self) -> Path:
        return self._dir

    def _ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)

    def path_for(self, on: date) -> Path:
        return self._dir / f"{on.isoformat()}.md"

    def write(self, markdown: str, on: date | None = None) -> Path:
        self._ensure()
        target = self.path_for(on or date.today())
        target.write_text(markdown, encoding="utf-8")
        os.chmod(target, 0o600)
        return target

    def latest(self) -> Brief | None:
        if not self._dir.is_dir():
            return None
        best: tuple[date, Path] | None = None
        for entry in self._dir.iterdir():
            m = _NAME.match(entry.name)
            if not m:
                continue
            on = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if best is None or on > best[0]:
                best = (on, entry)
        if best is None:
            return None
        on, path = best
        return Brief(
            on=on,
            markdown=path.read_text(encoding="utf-8"),
            written_at=datetime.fromtimestamp(path.stat().st_mtime),
        )

    def prune(self, keep: int = 60) -> int:
        """Keep the most recent briefs and drop the rest."""
        if not self._dir.is_dir():
            return 0
        dated = sorted(
            ((m, p) for p in self._dir.iterdir() if (m := _NAME.match(p.name))),
            key=lambda pair: pair[0].group(0),
            reverse=True,
        )
        removed = 0
        for _, path in dated[keep:]:
            path.unlink(missing_ok=True)
            removed += 1
        return removed
