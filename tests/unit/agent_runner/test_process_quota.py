"""Process-level tests for the engine quota-exhaustion path.

* :func:`process.codex_invoke` must classify a hard usage-limit wall as
  ``error_quota_exhausted`` (not the generic ``error_rate_limit`` it used to),
  stamp the parsed resume instant into ``raw``, and persist a backoff.
* :func:`process.invoke_agent_engine` must SKIP an engine that is currently
  quota-parked, returning an honest result without invoking it -- so the
  hybrid caller keeps claude running while codex is spent.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime


def _fake_codex_exhausted_proc(*_a, **_kw):
    return subprocess.CompletedProcess(
        args=["codex"],
        returncode=1,
        stdout="",
        stderr="You've hit your usage limit. try again at Jul 7.",
    )


def test_codex_invoke_classifies_quota_exhausted(fresh_agent_runner, monkeypatch, tmp_path):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    monkeypatch.setattr(proc, "_popen_run_text", _fake_codex_exhausted_proc)

    out = proc.codex_invoke(
        "do work",
        workdir=tmp_path,
        agent="lucius",
        firing_id="f1",
        timeout=30,
    )

    assert out.success is False
    assert out.subtype == "error_quota_exhausted"
    resume_at = out.raw.get("quota_resume_at", "")
    assert resume_at
    resume = datetime.fromisoformat(resume_at.replace("Z", "+00:00"))
    assert resume.tzinfo is not None
    assert resume > datetime.now(UTC)
    # And it persisted a backoff so the next firing skips codex.
    assert ar.is_engine_quota_exhausted("codex") is True


def test_codex_invoke_plain_rate_limit_still_transient(fresh_agent_runner, monkeypatch, tmp_path):
    ar = fresh_agent_runner
    import agent_runner.process as proc

    def _fake_rate_limited(*_a, **_kw):
        return subprocess.CompletedProcess(
            args=["codex"],
            returncode=1,
            stdout="",
            stderr="HTTP 429 too many requests",
        )

    monkeypatch.setattr(proc, "_popen_run_text", _fake_rate_limited)

    out = proc.codex_invoke(
        "do work",
        workdir=tmp_path,
        agent="lucius",
        firing_id="f2",
        timeout=30,
    )
    # A plain 429 stays a transient rate limit -- NOT a quota-exhaustion wall,
    # and does not park the engine.
    assert out.subtype == "error_rate_limit"
    assert ar.is_engine_quota_exhausted("codex") is False


def test_engine_quota_backoff_uses_default_when_resume_hint_is_expired(
    fresh_agent_runner, monkeypatch
):
    ar = fresh_agent_runner
    monkeypatch.setenv("ALFRED_ENGINE_QUOTA_DEFAULT_HOURS", "2")

    until = ar.record_engine_quota_exhausted(
        "codex",
        resume_at="2000-01-01T00:00:00Z",
        reason="stale resume hint",
    )

    assert until != "2000-01-01T00:00:00Z"
    assert ar.is_engine_quota_exhausted("codex") is True
    record = ar.engine_quota_backoff("codex")
    assert record is not None
    assert record["until"] == until


def test_invoke_agent_engine_skips_quota_parked_engine(fresh_agent_runner, tmp_path):
    ar = fresh_agent_runner

    # Park codex until the far future.
    ar.record_engine_quota_exhausted("codex", resume_at="2999-01-01T00:00:00Z")

    def fake_codex(*_a, **_kw):  # pragma: no cover - must not run
        raise AssertionError("codex must not be invoked while quota-parked")

    def fake_claude(*_a, **_kw):  # pragma: no cover - must not run
        raise AssertionError("claude must not be invoked in codex mode")

    out, engine_used = ar.invoke_agent_engine(
        "hi",
        engine="codex",
        agent="lucius",
        firing_id="f3",
        workdir=tmp_path,
        claude_allowed_tools="Read",
        timeout=30,
        claude_fn=fake_claude,
        codex_fn=fake_codex,
    )
    assert out.success is False
    assert out.subtype == "error_quota_exhausted"
    assert out.raw.get("quota_exhausted") is True
    assert engine_used == "codex"


def test_hybrid_keeps_claude_when_codex_quota_parked(fresh_agent_runner, tmp_path):
    """In hybrid mode a claude success is returned normally; codex being
    parked never matters because the fallback only fires on a claude
    capability gap, and even then it would skip the parked engine."""
    ar = fresh_agent_runner
    ar.record_engine_quota_exhausted("codex", resume_at="2999-01-01T00:00:00Z")

    good = ar.ClaudeResult(
        success=True,
        subtype="success",
        num_turns=2,
        cost_usd=0.0,
        session_id="s",
        result_text="done",
        raw={},
        stop_reason="end_turn",
    )

    out, engine_used = ar.invoke_agent_engine(
        "hi",
        engine="hybrid",
        agent="lucius",
        firing_id="f4",
        workdir=tmp_path,
        claude_allowed_tools="Read",
        timeout=30,
        claude_fn=lambda *a, **kw: good,
        codex_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("codex parked")),
    )
    assert out.success is True
    assert engine_used == "claude"
