"""Tests for lib/transcripts.py."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import transcripts  # noqa: E402

# --------------------------------------------------------------------------
# Fixtures: a tiny but representative stream-JSON corpus
# --------------------------------------------------------------------------


def _write_firing(
    state_dir: Path,
    codename: str,
    firing_id: str,
    events: list[dict],
    month: str | None = None,
) -> Path:
    month = month or datetime.now(UTC).strftime("%Y-%m")
    out = state_dir / "transcripts" / codename / month / f"{firing_id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return out


def _full_firing_events() -> list[dict]:
    return [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading the file"},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/repo/foo.py"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "ok"}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls -la /repo"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {
                            "file_path": "/repo/foo.py",
                            "old_string": "x",
                            "new_string": "y",
                        },
                    },
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "review"},
                    },
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "num_turns": 2,
            "total_cost_usd": 0.12,
            "session_id": "abc",
            "stop_reason": "end_turn",
        },
    ]


def _claude_auth_error_events() -> list[dict]:
    return [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Failed to authenticate. API Error: 401 Invalid authentication credentials",
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 401,
            "duration_ms": 2457,
            "num_turns": 1,
            "result": "Failed to authenticate. API Error: 401 Invalid authentication credentials",
            "stop_reason": "stop_sequence",
            "session_id": "auth-failed",
            "total_cost_usd": 0,
        },
    ]


def _result_only_event(**overrides) -> list[dict]:
    result = {
        "type": "result",
        "subtype": "success",
        "num_turns": 1,
        "total_cost_usd": 0,
        "stop_reason": "end_turn",
    }
    result.update(overrides)
    return [result]


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path


# --------------------------------------------------------------------------
# transcript_summary
# --------------------------------------------------------------------------


def test_transcript_summary_full_shape(state_dir: Path) -> None:
    path = _write_firing(state_dir, "lucius", "L001", _full_firing_events())
    s = transcripts.transcript_summary(path)
    assert s.tool_calls_total == 4
    assert s.tool_calls_by_name == {"Read": 1, "Bash": 1, "Edit": 1, "Skill": 1}
    assert s.bash_commands == ["ls -la /repo"]
    assert s.files_read == ["/repo/foo.py"]
    assert s.files_edited == ["/repo/foo.py"]
    assert s.skills_invoked == ["review"]
    assert s.result is not None
    assert s.result.subtype == "success"
    assert s.result.num_turns == 2
    assert s.result.total_cost_usd == 0.12
    assert s.result.raw_subtype == "success"
    assert not s.result.is_error


def test_transcript_summary_classifies_provider_error_envelope(state_dir: Path) -> None:
    path = _write_firing(state_dir, "drake", "D401", _claude_auth_error_events())
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_authentication"
    assert s.result.raw_subtype == "success"
    assert s.result.is_error
    assert s.result.api_error_status == 401
    assert s.result.result_text == (
        "Failed to authenticate. API Error: 401 Invalid authentication credentials"
    )


def test_transcript_summary_preserves_non_provider_aborted_result(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DABORT",
        _result_only_event(
            stop_reason="aborted",
            result="Cancelled while editing rate-limit handling.",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "success"
    assert s.result.raw_subtype == "success"
    assert not s.result.is_error


def test_transcript_summary_preserves_aborted_error_envelope_without_status(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DABORTERR",
        _result_only_event(
            is_error=True,
            result="Failed to authenticate while editing rate-limit handling.",
            stop_reason="aborted",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "success"
    assert s.result.raw_subtype == "success"
    assert s.result.is_error


def test_transcript_summary_preserves_aborted_error_envelope_with_status(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DABORT401",
        _result_only_event(
            is_error=True,
            api_error_status=401,
            result="Failed to authenticate.",
            stop_reason="aborted",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "success"
    assert s.result.raw_subtype == "success"
    assert s.result.api_error_status == 401


def test_transcript_summary_ignores_blank_api_status(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DBLANK",
        _result_only_event(api_error_status="", result="Completed."),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "success"


def test_transcript_summary_ignores_generic_rate_limit_prose(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DAPI",
        _result_only_event(
            is_error=True,
            result="Implemented rate-limit handling and stopped.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_api"


def test_transcript_summary_classifies_auth_marker_without_is_error(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "D401NOFLAG",
        _result_only_event(
            result="Failed to authenticate. API Error: 401 Invalid authentication credentials",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_authentication"
    assert not s.result.is_error


def test_transcript_summary_classifies_budget_marker_without_is_error(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DBUDGETNOFLAG",
        _result_only_event(
            result="You've hit your usage limit.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_budget"
    assert not s.result.is_error


def test_transcript_summary_classifies_strict_rate_limit_without_is_error(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DRATELIMITNOFLAG",
        _result_only_event(
            error_message="quota exceeded",
            result="Stopped before completing.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_rate_limit"
    assert not s.result.is_error


def test_transcript_summary_ignores_result_rate_limit_prose_without_is_error(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DRATELIMITPROSE",
        _result_only_event(
            result="Implemented quota exceeded handling and stopped.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "success"


def test_transcript_summary_classifies_subscription_cap_as_rate_limit(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DSUBCAP",
        _result_only_event(
            is_error=True,
            result=(
                "Your organization has disabled Claude subscription access for "
                "Claude Code. Use an Anthropic API key instead."
            ),
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_rate_limit"


def test_transcript_summary_classifies_quota_exceeded_as_rate_limit(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DQUOTA",
        _result_only_event(
            is_error=True,
            result="quota exceeded",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_rate_limit"


def test_transcript_summary_classifies_hyphenated_provider_rate_limit(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DRATELIMIT",
        _result_only_event(
            is_error=True,
            result="API Error: rate-limit exceeded",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_rate_limit"


def test_transcript_summary_budget_wins_over_limit_wording(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DBUDGET",
        _result_only_event(
            is_error=True,
            api_error_status=429,
            result="You've hit your usage limit.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_budget"


def test_transcript_summary_budget_handles_curly_apostrophe(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DBUDGETCURLY",
        _result_only_event(
            is_error=True,
            api_error_status=429,
            result="You\u2019ve hit your usage limit.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_budget"


def test_transcript_summary_overload_wins_over_too_many_requests(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "D529",
        _result_only_event(
            is_error=True,
            api_error_status=529,
            result="HTTP 529 too many requests",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_overloaded"


def test_transcript_summary_bedrock_throttle_is_overload(state_dir: Path) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DBEDROCK",
        _result_only_event(
            is_error=True,
            result=(
                '{"type":"error","error":{"type":"throttling_error",'
                '"message":"Bedrock: too many requests, throttled"}}'
            ),
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_overloaded"


def test_transcript_summary_strict_overload_envelope_without_is_error(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DOVERLOADRAW",
        _result_only_event(
            result='{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "error_overloaded"
    assert not s.result.is_error


def test_transcript_summary_ignores_success_prose_about_overloaded_worker(
    state_dir: Path,
) -> None:
    path = _write_firing(
        state_dir,
        "drake",
        "DOVERLOADPROSE",
        _result_only_event(
            result="Fixed an overloaded worker and added regression tests.",
            stop_reason="stop_sequence",
        ),
    )
    s = transcripts.transcript_summary(path)
    assert s.result is not None
    assert s.result.subtype == "success"


def test_transcript_summary_missing_file(state_dir: Path) -> None:
    s = transcripts.transcript_summary(state_dir / "does-not-exist.jsonl")
    assert s.tool_calls_total == 0
    assert s.result is None


def test_transcript_summary_skips_invalid_json(state_dir: Path) -> None:
    path = state_dir / "transcripts" / "drake" / "2026-05" / "D001.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "result", "subtype": "ok", "num_turns": 1})
        + "\n"
        + "not valid json\n"
        + json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/a"}}]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    s = transcripts.transcript_summary(path)
    # The valid lines still parse - torn tail doesn't sink the whole file.
    assert s.tool_calls_total == 1
    assert s.result is not None


def test_transcript_summary_handles_empty_lines(state_dir: Path) -> None:
    path = state_dir / "transcripts" / "drake" / "2026-05" / "D002.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n\n" + json.dumps({"type": "system", "subtype": "init"}) + "\n\n",
        encoding="utf-8",
    )
    s = transcripts.transcript_summary(path)
    assert s.tool_calls_total == 0


# --------------------------------------------------------------------------
# list_firings / find_firing / list_codenames
# --------------------------------------------------------------------------


def test_list_firings_sorted_newest_first(state_dir: Path) -> None:
    a = _write_firing(state_dir, "lucius", "old", [{"type": "system"}], month="2026-01")
    b = _write_firing(state_dir, "lucius", "new", [{"type": "system"}], month="2026-05")
    import os
    import time

    # Force ordering by setting mtimes explicitly.
    now = time.time()
    os.utime(a, (now - 10_000, now - 10_000))
    os.utime(b, (now, now))

    firings = transcripts.list_firings(state_dir, "lucius")
    assert [f.firing_id for f in firings] == ["new", "old"]


def test_list_firings_empty_returns_empty_list(state_dir: Path) -> None:
    assert transcripts.list_firings(state_dir, "nobody") == []


def test_find_firing_match_and_miss(state_dir: Path) -> None:
    _write_firing(state_dir, "drake", "D001", [{"type": "system"}])
    hit = transcripts.find_firing(state_dir, "drake", "D001")
    assert hit is not None
    assert hit.path.exists()
    assert transcripts.find_firing(state_dir, "drake", "nope") is None


def test_list_codenames(state_dir: Path) -> None:
    _write_firing(state_dir, "lucius", "L1", [{"type": "system"}])
    _write_firing(state_dir, "drake", "D1", [{"type": "system"}])
    assert transcripts.list_codenames(state_dir) == ["drake", "lucius"]


# --------------------------------------------------------------------------
# default_state_dir resolution
# --------------------------------------------------------------------------


def test_default_state_dir_honours_alfred_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_STATE_DIR", str(tmp_path / "explicit"))
    monkeypatch.delenv("ALFRED_HOME", raising=False)
    assert transcripts.default_state_dir() == tmp_path / "explicit"


def test_default_state_dir_falls_back_to_alfred_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ALFRED_STATE_DIR", raising=False)
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "h"))
    assert transcripts.default_state_dir() == tmp_path / "h" / "state"


def test_default_state_dir_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALFRED_STATE_DIR", raising=False)
    monkeypatch.delenv("ALFRED_HOME", raising=False)
    assert transcripts.default_state_dir() == Path.home() / ".alfred" / "state"


# --------------------------------------------------------------------------
# Codex helpers
# --------------------------------------------------------------------------


def test_extract_codex_tokens() -> None:
    text = "some preamble\ntokens used\n12,345\nmore output\n"
    assert transcripts.extract_codex_tokens(text) == 12345


def test_extract_codex_tokens_missing_returns_zero() -> None:
    assert transcripts.extract_codex_tokens("nothing here") == 0


def test_extract_codex_session_id() -> None:
    assert transcripts.extract_codex_session_id("session id: 01HXYZ\n") == "01HXYZ"
    assert transcripts.extract_codex_session_id("session id:\n") is None


def test_codex_rate_limit_signal() -> None:
    assert transcripts.codex_run_hit_rate_limit("HTTP 429 too many requests")
    assert transcripts.codex_run_hit_rate_limit("rate-limit hit")
    assert not transcripts.codex_run_hit_rate_limit("all good")


# --------------------------------------------------------------------------
# Render helpers
# --------------------------------------------------------------------------


def test_render_firing_jsonl(state_dir: Path) -> None:
    path = _write_firing(state_dir, "lucius", "L1", _full_firing_events())
    lines = transcripts.render_firing_jsonl(path)
    joined = "\n".join(lines)
    assert "[system] init" in joined
    assert "[tool_use Read]" in joined
    assert "[tool_use Bash] $ ls -la /repo" in joined
    assert "[tool_use Skill] /review" in joined
    assert "[result] subtype=success turns=2" in joined


def test_render_firing_jsonl_shows_effective_provider_error(state_dir: Path) -> None:
    path = _write_firing(state_dir, "drake", "D401", _claude_auth_error_events())
    lines = transcripts.render_firing_jsonl(path)
    joined = "\n".join(lines)
    assert "[result] subtype=error_authentication" in joined
    assert "raw_subtype=success" in joined
    assert "is_error=true" in joined
    assert "api_error_status=401" in joined
