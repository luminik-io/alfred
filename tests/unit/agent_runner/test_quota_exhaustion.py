"""Tests for the engine quota-exhaustion honesty path.

Covers three cooperating pieces added for the codex credit-exhaustion
incident (usage probe read 0% while the CLI hit a hard "try again at Jul 7"
wall):

* :func:`result.looks_quota_exhausted` / :func:`result.parse_quota_resume_at`
  -- classify a hard credit wall distinctly from a transient rate limit and
  parse the resume instant out of the CLI message.
* :func:`state.record_engine_quota_exhausted` /
  :func:`state.engine_quota_backoff` -- persist a per-engine backoff so the
  scheduler skips the spent engine until its window resets.
* the reliability subtype table -- ``error_quota_exhausted`` must be FATAL
  for the same-engine retry loop (never retried into the wall).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

# --------------------------------------------------------------------------
# Classification + resume parsing
# --------------------------------------------------------------------------


def test_looks_quota_exhausted_matches_codex_wall(fresh_agent_runner):
    from agent_runner.result import looks_quota_exhausted

    assert looks_quota_exhausted("You've hit your usage limit. try again at Jul 7")
    assert looks_quota_exhausted("usage limit reached")
    assert looks_quota_exhausted("You are out of extra usage")
    assert looks_quota_exhausted("plan quota exhausted")


def test_looks_quota_exhausted_ignores_transient_rate_limit(fresh_agent_runner):
    from agent_runner.result import looks_quota_exhausted

    # A plain 429 / rate limit is transient, not an exhaustion wall.
    assert not looks_quota_exhausted("HTTP 429 too many requests, retry later")
    assert not looks_quota_exhausted("rate limit exceeded")
    assert not looks_quota_exhausted("added rate-limit handling to the runner")


def test_parse_quota_resume_absolute_bare_date(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    now = datetime(2026, 7, 3, tzinfo=UTC)
    got = parse_quota_resume_at("You've hit your usage limit. try again at Jul 7", now=now)
    assert got == "2026-07-07T00:00:00Z"


def test_parse_quota_resume_bare_date_rolls_to_next_year(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    # A date already well in the past this year infers next year's occurrence.
    now = datetime(2026, 12, 20, tzinfo=UTC)
    got = parse_quota_resume_at("hit your usage limit, try again at Jan 5", now=now)
    assert got == "2027-01-05T00:00:00Z"


def test_parse_quota_resume_iso_datetime(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    got = parse_quota_resume_at("usage limit reached; resets on 2026-07-07T15:30")
    assert got == "2026-07-07T15:30:00Z"


def test_parse_quota_resume_relative_days(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    now = datetime(2026, 7, 3, 12, 0, 0, tzinfo=UTC)
    got = parse_quota_resume_at("hit your usage limit, try again in 3 days", now=now)
    assert got == "2026-07-06T12:00:00Z"


def test_parse_quota_resume_returns_none_without_hint(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    assert parse_quota_resume_at("You've hit your usage limit.") is None
    assert parse_quota_resume_at("") is None


# --------------------------------------------------------------------------
# REAL captured strings (not author-invented) -- these are the messages the
# feature exists to handle, pulled from tests/test_result_classification.py
# and the canonical curly-apostrophe form terminals emit. A false-negative
# here means the engine is never parked on the exact input that motivated the
# change, so these are the load-bearing cases.
# --------------------------------------------------------------------------

# The real string captured from a live codex run (see
# tests/test_result_classification.py:116). It deliberately carries the real
# curly apostrophe (U+2019), middle dot (U+00B7), and the time-of-day-with-UTC
# resume format -- exactly what the parser has to survive, so the inline
# suppression below is load-bearing, not cosmetic.
_REAL_CODEX_WALL = "You’re out of extra usage · resets 5:50pm (UTC)"  # noqa: RUF001


def test_real_captured_string_classifies_as_exhausted(fresh_agent_runner):
    from agent_runner.result import looks_quota_exhausted

    # The exact live message must be recognised as a hard wall.
    assert looks_quota_exhausted(_REAL_CODEX_WALL)


def test_real_captured_string_parses_a_resume_instant(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    # It must yield a concrete resume instant, NOT fall through to the default
    # 5h window. 5:50pm UTC with now=10:00 is later today.
    now = datetime(2026, 7, 3, 10, 0, 0, tzinfo=UTC)
    got = parse_quota_resume_at(_REAL_CODEX_WALL, now=now)
    assert got == "2026-07-03T17:50:00Z"


def test_real_captured_string_resume_rolls_to_next_day_when_past(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    # When the reset time already passed today, the resume is tomorrow.
    now = datetime(2026, 7, 3, 18, 0, 0, tzinfo=UTC)
    got = parse_quota_resume_at(_REAL_CODEX_WALL, now=now)
    assert got == "2026-07-04T17:50:00Z"


def test_curly_apostrophe_hit_limit_classifies_and_parses(fresh_agent_runner):
    from agent_runner.result import looks_quota_exhausted, parse_quota_resume_at

    # macOS terminals / rich CLIs emit the typographic apostrophe. The ASCII
    # apostrophe used to be the only match, a live false-negative.
    curly = "You’ve hit your usage limit. try again at Jul 7"  # noqa: RUF001
    assert looks_quota_exhausted(curly)
    now = datetime(2026, 7, 3, tzinfo=UTC)
    assert parse_quota_resume_at(curly, now=now) == "2026-07-07T00:00:00Z"


def test_resume_handles_after_keyword(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    now = datetime(2026, 7, 3, tzinfo=UTC)
    got = parse_quota_resume_at("usage limit reached, try again after 2026-08-01", now=now)
    assert got == "2026-08-01T00:00:00Z"


def test_resume_time_of_day_without_utc_suffix(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    now = datetime(2026, 7, 3, 10, 0, 0, tzinfo=UTC)
    assert parse_quota_resume_at("out of extra usage, resets 5pm", now=now) == (
        "2026-07-03T17:00:00Z"
    )


def test_month_name_with_time_of_day_is_preserved(fresh_agent_runner):
    """A month-day hint WITH a time (``Jul 7 at 3pm``) must park until that
    time, not midnight -- otherwise the backoff expires hours early and the
    scheduler resumes firing into the still-shut wall."""
    from agent_runner.result import parse_quota_resume_at

    now = datetime(2026, 7, 3, tzinfo=UTC)
    assert parse_quota_resume_at("try again at Jul 7 at 3pm", now=now) == "2026-07-07T15:00:00Z"
    assert parse_quota_resume_at("try again at Jul 7 at 3:05pm", now=now) == "2026-07-07T15:05:00Z"
    assert parse_quota_resume_at("resets Jul 7 at 15:30", now=now) == "2026-07-07T15:30:00Z"
    # No time still pins to midnight.
    assert parse_quota_resume_at("try again at Jul 7", now=now) == "2026-07-07T00:00:00Z"


def test_month_name_with_explicit_year_and_time(fresh_agent_runner):
    from agent_runner.result import parse_quota_resume_at

    now = datetime(2026, 7, 3, tzinfo=UTC)
    assert parse_quota_resume_at("resets July 7, 2026 at 9am", now=now) == "2026-07-07T09:00:00Z"


# --------------------------------------------------------------------------
# Reliability classification: quota exhaustion is FATAL for the retry loop
# --------------------------------------------------------------------------


def test_quota_exhausted_classifies_fatal(fresh_agent_runner):
    ar = fresh_agent_runner
    result = ar.ClaudeResult(
        success=False,
        subtype="error_quota_exhausted",
        num_turns=1,
        cost_usd=0.0,
        session_id=None,
        result_text="usage limit reached",
        raw={},
        stop_reason="error",
    )
    # FATAL means the same-engine transient retry loop never touches it.
    assert ar.classify_result(result) is ar.FailureClass.FATAL


# --------------------------------------------------------------------------
# Backoff persistence
# --------------------------------------------------------------------------


def test_record_and_read_engine_quota_backoff(fresh_agent_runner):
    from agent_runner.state import (
        engine_quota_backoff,
        is_engine_quota_exhausted,
        record_engine_quota_exhausted,
    )

    future = (datetime.now(UTC).replace(microsecond=0)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Use a clearly-future instant.
    future = "2999-01-01T00:00:00Z"
    record_engine_quota_exhausted("codex", resume_at=future, reason="usage limit")

    record = engine_quota_backoff("codex")
    assert record is not None
    assert record["until"] == future
    assert record["engine"] == "codex"
    assert is_engine_quota_exhausted("codex") is True
    # A different engine is unaffected.
    assert engine_quota_backoff("claude") is None


def test_engine_quota_backoff_expires_and_self_clears(fresh_agent_runner):
    from agent_runner.state import (
        _engine_quota_path,
        engine_quota_backoff,
    )

    past = "2000-01-01T00:00:00Z"
    path = _engine_quota_path("codex")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"engine": "codex", "until": past, "reason": "stale"}),
        encoding="utf-8",
    )
    # An expired record reads as None AND is cleaned up on read.
    assert engine_quota_backoff("codex") is None
    assert not path.exists()


def test_record_engine_quota_default_window_when_no_resume(fresh_agent_runner):
    from agent_runner.state import engine_quota_backoff, record_engine_quota_exhausted

    # No parseable resume hint -> a default future window is applied so the
    # engine is still parked (not hammered every tick).
    until = record_engine_quota_exhausted("codex", resume_at=None, reason="no date")
    assert until  # non-empty ISO string
    record = engine_quota_backoff("codex")
    assert record is not None
    parsed = datetime.strptime(record["until"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert parsed > datetime.now(UTC)


def test_clear_engine_quota_backoff(fresh_agent_runner):
    from agent_runner.state import (
        clear_engine_quota_backoff,
        engine_quota_backoff,
        record_engine_quota_exhausted,
    )

    record_engine_quota_exhausted("codex", resume_at="2999-01-01T00:00:00Z")
    assert clear_engine_quota_backoff("codex") is True
    assert engine_quota_backoff("codex") is None
    # Clearing an absent record returns False.
    assert clear_engine_quota_backoff("codex") is False


def test_record_engine_quota_dry_run_does_not_write(fresh_agent_runner, monkeypatch):
    from agent_runner.state import _engine_quota_path, record_engine_quota_exhausted

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    until = record_engine_quota_exhausted("codex", resume_at="2999-01-01T00:00:00Z")
    # Dry-run returns the until-string for messaging but never writes state.
    assert until == "2999-01-01T00:00:00Z"
    assert not _engine_quota_path("codex").exists()
