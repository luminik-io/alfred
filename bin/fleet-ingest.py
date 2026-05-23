#!/usr/bin/env python3
"""``fleet-ingest`` — drain per-agent JSONL outboxes into the fleet-brain.

Each codename agent writes one JSON object per line to
``$ALFRED_HOME/state/memory-outbox/<codename>.jsonl`` when it learns
something it wants the next firing to know. This drainer reads those
files, dispatches each record into the brain, and tracks a per-file
watermark so a re-run is idempotent.

Opt-in scheduling: there is no default launchd / systemd unit. An
operator who wants this on the fleet adds a line to
``launchd/agents.conf``::

    my.fleet.fleet-ingest    fleet-ingest.py    interval:900    no    my.fleet.fleet-ingest    Memory outbox drainer

The drainer is bounded-work: it reads up to ``--max-records`` per
invocation and exits. Crashing mid-file is safe — the cursor only
advances after a record is persisted.

Outbox record shape (JSON per line)::

    {"event": "reflect", "codename": "lucius", "repo": "your-org/api",
     "body": "...", "tags": ["graphql"], "firing_id": "01HZ...",
     "severity": "info", "ts": "2026-05-23T12:00:00Z"}

    {"event": "firing_log", "firing_id": "01HZ...", "codename": "lucius",
     "repo": "your-org/api", "status": "ok", "summary": "...",
     "started_at": "...", "finished_at": "...", "cost_cents": 12,
     "pr_url": "...", "sentinel": null}

    {"event": "note_repo", "repo": "your-org/api", "body": "..."}

Unknown ``event`` values are logged and skipped; the cursor still
advances so a malformed line doesn't wedge the drain forever.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# lib/ on sys.path.
_HERE = Path(__file__).resolve().parent
for candidate in (
    _HERE.parent / "lib",
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from fleet_brain import FleetBrain  # noqa: E402

_LOG = logging.getLogger("fleet-ingest")


def alfred_home() -> Path:
    return Path(os.environ.get("ALFRED_HOME", os.path.expanduser("~/.alfred")))


def outbox_dir() -> Path:
    return alfred_home() / "state" / "memory-outbox"


def cursor_path() -> Path:
    p = alfred_home() / "state" / "fleet-brain" / "ingest-cursor.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_cursor() -> dict[str, int]:
    p = cursor_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: int(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        _LOG.warning("ingest cursor unreadable, starting from zero: %s", p)
    return {}


def save_cursor(cur: dict[str, int]) -> None:
    p = cursor_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


@dataclass
class Counts:
    seen: int = 0
    lessons: int = 0
    firings: int = 0
    notes: int = 0
    skipped: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "seen": self.seen,
            "lessons": self.lessons,
            "firings": self.firings,
            "notes": self.notes,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def dispatch(brain: FleetBrain, record: dict[str, Any]) -> str:
    """Apply one outbox record. Returns a tag identifying what fired."""
    event = record.get("event")
    if event == "reflect":
        brain.reflect(
            codename=record["codename"],
            repo=record["repo"],
            body=record["body"],
            tags=record.get("tags") or [],
            firing_id=record.get("firing_id"),
            severity=record.get("severity", "info"),
            created_at=_parse_ts(record.get("ts")),
        )
        return "lesson"
    if event == "firing_log":
        brain.firing_log(
            firing_id=record["firing_id"],
            codename=record["codename"],
            status=record["status"],
            summary=record.get("summary", ""),
            repo=record.get("repo"),
            started_at=_parse_ts(record.get("started_at")),
            finished_at=_parse_ts(record.get("finished_at")),
            cost_cents=int(record.get("cost_cents", 0)),
            pr_url=record.get("pr_url"),
            sentinel=record.get("sentinel"),
        )
        return "firing"
    if event == "note_repo":
        brain.note_repo(
            repo=record["repo"],
            body=record["body"],
            updated_at=_parse_ts(record.get("ts")),
        )
        return "note"
    raise ValueError(f"unknown event: {event!r}")


def _parse_ts(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        text = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def drain_file(brain: FleetBrain, path: Path, cursor: dict[str, int], max_records: int) -> Counts:
    counts = Counts()
    consumed = cursor.get(path.name, 0)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        _LOG.error("cannot read %s: %s", path, e)
        counts.errors += 1
        return counts
    for i, raw in enumerate(lines):
        if i < consumed:
            continue
        if counts.seen >= max_records:
            break
        counts.seen += 1
        line = raw.strip()
        if not line:
            cursor[path.name] = i + 1
            counts.skipped += 1
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            _LOG.warning("%s:%d malformed json: %s", path.name, i, e)
            cursor[path.name] = i + 1
            counts.errors += 1
            continue
        try:
            kind = dispatch(brain, record)
        except (KeyError, ValueError) as e:
            _LOG.warning("%s:%d dispatch failed: %s", path.name, i, e)
            cursor[path.name] = i + 1
            counts.errors += 1
            continue
        if kind == "lesson":
            counts.lessons += 1
        elif kind == "firing":
            counts.firings += 1
        elif kind == "note":
            counts.notes += 1
        cursor[path.name] = i + 1
    return counts


def drain(brain: FleetBrain, root: Path, max_records: int, codenames: list[str] | None) -> Counts:
    totals = Counts()
    cursor = load_cursor()
    if not root.is_dir():
        _LOG.info("outbox dir absent: %s", root)
        save_cursor(cursor)
        return totals
    files = sorted(root.glob("*.jsonl"))
    if codenames:
        wanted = {f"{c}.jsonl" for c in codenames}
        files = [f for f in files if f.name in wanted]
    for f in files:
        if totals.seen >= max_records:
            break
        c = drain_file(brain, f, cursor, max_records - totals.seen)
        totals.seen += c.seen
        totals.lessons += c.lessons
        totals.firings += c.firings
        totals.notes += c.notes
        totals.skipped += c.skipped
        totals.errors += c.errors
    save_cursor(cursor)
    return totals


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fleet-ingest",
        description="Drain per-agent JSONL outboxes into the fleet-brain.",
    )
    p.add_argument(
        "--codename",
        action="append",
        dest="codenames",
        help="restrict to one codename (repeatable)",
    )
    p.add_argument(
        "--max-records",
        type=int,
        default=1000,
        help="upper bound on records consumed this run",
    )
    p.add_argument(
        "--db",
        help="path to the SQLite brain file (overrides env)",
    )
    p.add_argument(
        "--reset-cursor",
        action="store_true",
        help="wipe the ingest cursor before draining",
    )
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("ALFRED_BRAIN_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    if args.reset_cursor:
        p = cursor_path()
        if p.exists():
            p.unlink()
            _LOG.info("cursor reset: %s", p)
    brain = FleetBrain(db_path=args.db) if args.db else FleetBrain()
    counts = drain(brain, outbox_dir(), int(args.max_records), args.codenames)
    if not args.quiet:
        _LOG.info("drain complete: %s", counts.as_dict())
    # Non-zero only if every record errored.
    if counts.seen > 0 and counts.errors == counts.seen:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
