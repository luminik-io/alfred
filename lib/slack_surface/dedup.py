"""Event de-duplication for the Slack listener.

Slack redelivers events (its at-least-once envelope, plus the app_mention /
message double delivery of a single @mention), so the listener must remember
which envelopes it has already handled and drop repeats. This module owns that
durable, filesystem-backed record so the listener stays routing-only.

The store is a directory of zero-content marker files, one per event id. The
mark is created with ``O_CREAT | O_EXCL`` so the "have I seen this?" test and
the "remember it" write are a single atomic step -- two concurrent Socket Mode
handler threads racing the same redelivered envelope cannot both win.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path


def _safe_event_id(event_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", event_id).strip("_") or "event"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SeenEventStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def mark_seen(self, event_id: str) -> bool:
        if not event_id:
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{_safe_event_id(event_id)}.seen"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return True
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_utc_now() + "\n")
        return False

    def has_seen(self, event_id: str) -> bool:
        """True iff ``event_id`` was already marked, WITHOUT marking it now.

        Lets a caller test for a prior delivery and defer the mark until it knows
        the current delivery will actually be handled, so an ignored delivery
        (e.g. the plain ``message`` copy of an @mention that the ambient path
        drops in favour of the ``app_mention`` copy) never consumes the key and
        strand the delivery that should handle it.
        """
        if not event_id:
            return False
        return (self.root / f"{_safe_event_id(event_id)}.seen").exists()


__all__ = ["SeenEventStore"]
