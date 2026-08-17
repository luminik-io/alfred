from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from agent_runner.agent_events import Event, parse_record  # noqa: E402
from server.run_evidence import (  # noqa: E402
    derive_run_evidence,
    discover_transcript_artifact,
)


def _event(event: str, seq: int, source: str, **payload: object) -> dict:
    return {
        "schema_version": 1,
        "seq": seq,
        "ts": f"2026-08-17T10:00:0{seq}Z",
        "agent": "senior-dev",
        "firing_id": "run-7",
        "type": event,
        "event": event,
        "source": source,
        **payload,
    }


def test_event_envelope_round_trips_schema_and_fact_source() -> None:
    record = Event.create(
        seq=1,
        agent="reviewer",
        firing_id="run-1",
        event_type="pr_opened",
        payload={"repo": "example/app", "number": 7},
    ).to_record()

    parsed = parse_record(record)

    assert parsed is not None
    assert parsed.schema_version == 1
    assert parsed.source == "github"
    assert parsed.payload == {"repo": "example/app", "number": 7}


def test_legacy_typed_event_infers_source_without_claiming_current_schema() -> None:
    parsed = parse_record(
        {
            "seq": 1,
            "ts": "2026-08-17T10:00:00Z",
            "agent": "reviewer",
            "firing_id": "run-1",
            "type": "llm_invoke_done",
            "event": "llm_invoke_done",
            "engine": "codex",
        }
    )

    assert parsed is not None
    assert parsed.schema_version == 0
    assert parsed.source == "engine"


def test_run_evidence_links_local_run_facts_without_external_reads(tmp_path: Path) -> None:
    record = derive_run_evidence(
        agent="senior-dev",
        run_id="run-7",
        events=[
            _event("firing_started", 1, "alfred"),
            _event("issue_picked", 2, "github", repo="example/app", number=42),
            _event(
                "worktree_created",
                3,
                "alfred",
                path="/tmp/worktrees/app-42",
                branch="senior-dev/42",
            ),
            _event(
                "llm_invoke_done",
                4,
                "engine",
                engine="opencode",
                session_id="ses_123",
                turns=7,
                subtype="success",
                success=True,
            ),
            _event("plan_approved", 5, "operator", number=42),
            _event(
                "pre_push_checks_passed",
                6,
                "alfred",
                command="pytest -q",
            ),
            _event(
                "branch_pushed",
                7,
                "alfred",
                branch="senior-dev/42",
                commit_sha="a" * 40,
            ),
            _event(
                "pr_opened",
                8,
                "github",
                repo="example/app",
                url="https://github.com/example/app/pull/7",
            ),
            _event(
                "review_posted",
                9,
                "github",
                repo="example/app",
                number=7,
                p0_count=0,
                p1_count=1,
            ),
            _event("firing_complete", 10, "alfred", outcome="pr-opened"),
        ],
        events_path=tmp_path / "events" / "run-7.jsonl",
        transcript_path=tmp_path / "transcripts" / "run-7.jsonl",
    )

    assert record.schema_version == 1
    assert record.run_id == "run-7"
    assert record.event_count == 10
    assert {fact.kind for fact in record.facts} >= {
        "repository",
        "issue",
        "worktree",
        "engine_session",
        "approval",
        "check",
        "branch",
        "commit",
        "pull_request",
        "review",
    }
    assert {fact.source for fact in record.facts} == {"alfred", "engine", "github", "operator"}
    assert [artifact.status for artifact in record.artifacts] == ["available", "available"]


def test_run_evidence_marks_missing_transcript_as_unavailable(tmp_path: Path) -> None:
    record = derive_run_evidence(
        agent="reviewer",
        run_id="idle-1",
        events=[
            _event("firing_started", 1, "alfred"),
            _event("firing_complete", 2, "alfred", outcome="idle-no-pr"),
        ],
        events_path=tmp_path / "events" / "idle-1.jsonl",
        transcript_path=None,
    )

    transcript = next(artifact for artifact in record.artifacts if artifact.kind == "transcript")
    assert transcript.status == "unavailable"
    assert transcript.path is None
    assert record.facts == []


def test_transcript_discovery_covers_all_supported_engines(tmp_path: Path) -> None:
    state = tmp_path / "state"
    cases = (
        ("transcripts", "run-claude.jsonl", "run-claude"),
        ("codex", "run-codex.stdout.txt", "run-codex"),
        ("opencode", "run-opencode.events.jsonl", "run-opencode"),
    )
    for root, filename, run_id in cases:
        path = state / root / "reviewer" / "2026-08" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence\n", encoding="utf-8")
        assert (
            discover_transcript_artifact(
                state,
                agent="reviewer",
                run_id=run_id,
            )
            == path
        )


def test_transcript_discovery_rejects_untrusted_names(tmp_path: Path) -> None:
    state = tmp_path / "state"
    assert discover_transcript_artifact(state, agent="../reviewer", run_id="run-1") is None
    assert discover_transcript_artifact(state, agent="reviewer", run_id="../run-1") is None
