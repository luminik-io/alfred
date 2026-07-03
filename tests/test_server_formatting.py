"""Tests for the ``alfred serve`` presentation helpers.

The desktop client mirrors ``friendly_time`` in
``clients/desktop/src/format.ts``. Both compute day boundaries and calendar
fields in UTC so the two surfaces render the same relative date near midnight.
The fixtures below are shared with ``format.test.ts`` on the TypeScript side.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from server.formatting import friendly_time  # noqa: E402

# Shared day-boundary fixture. The value is late on Mar 14 UTC (23:50); ``now``
# is Mar 16 midday UTC, so the render falls into the "month day, HH:MM" branch.
# The UTC calendar day (Mar 14) and the UTC time of day (23:50) both differ from
# what a local-time implementation would produce in any offset timezone, so this
# fixture pins the whole computation to UTC. The identical fixture and expected
# string are asserted on the TypeScript side in
# ``clients/desktop/src/format.test.ts``.
FIXTURE_NOW = "2026-03-16T12:00:00Z"
FIXTURE_VALUE = "2026-03-14T23:50:00Z"
FIXTURE_EXPECTED = "Mar 14, 23:50"


def _now() -> datetime:
    return datetime.fromisoformat(FIXTURE_NOW.replace("Z", "+00:00"))


def test_friendly_time_utc_day_boundary_fixture() -> None:
    assert friendly_time(FIXTURE_VALUE, now=_now()) == FIXTURE_EXPECTED


def test_friendly_time_yesterday_uses_utc_time_of_day() -> None:
    # Value ~24.5h before now and on now's previous UTC calendar day.
    now = datetime.fromisoformat("2026-03-16T00:20:00+00:00")
    assert friendly_time("2026-03-15T00:05:00Z", now=now) == "yesterday 00:05"


def test_friendly_time_just_now() -> None:
    now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
    assert friendly_time("2026-03-15T12:00:00Z", now=now) == "just now"


def test_friendly_time_minutes_and_hours() -> None:
    now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
    assert friendly_time("2026-03-15T11:30:00Z", now=now) == "30m ago"
    assert friendly_time("2026-03-15T09:00:00Z", now=now) == "3h ago"


def test_friendly_time_same_year_uses_utc_time_of_day() -> None:
    now = datetime(2026, 3, 15, 0, 30, 0, tzinfo=UTC)
    assert friendly_time("2026-01-02T07:05:00Z", now=now) == "Jan 2, 07:05"


def test_friendly_time_prior_year_drops_time() -> None:
    now = datetime(2026, 3, 15, 0, 30, 0, tzinfo=UTC)
    assert friendly_time("2025-11-20T07:05:00Z", now=now) == "Nov 20, 2025"


def test_friendly_time_never() -> None:
    assert friendly_time(None) == "never"
    assert friendly_time("never") == "never"
