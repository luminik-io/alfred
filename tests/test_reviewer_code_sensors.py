from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runner import EventLog

ROOT = Path(__file__).resolve().parent.parent
REVIEWER_TEMPLATE = ROOT / "prompts/code-review.md"


def _load_reviewer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALFRED_HOME", str(ROOT))
    monkeypatch.setenv("GH_ORG", "acme")
    monkeypatch.setenv("ALFRED_REVIEWER_REPOS", "web")
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location("reviewer_code_sensors", ROOT / "bin/reviewer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    spec.loader.exec_module(module)
    return module


def _write_code_map(path: Path, *, generated_at: str) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "repos": {
                    "api": {
                        "head_sha": "def456",
                        "endpoints": [
                            {
                                "method": "GET",
                                "path": "/api/v1/things",
                                "file": "src/ThingResource.kt:12",
                            }
                        ],
                        "files": [],
                        "edges": [],
                    },
                    "web": {
                        "head_sha": "abc123",
                        "files": [
                            {
                                "path": "src/App.tsx",
                                "language": "typescript",
                                "symbols": [],
                                "imports": ["./api"],
                            },
                            {
                                "path": "src/api.ts",
                                "language": "typescript",
                                "symbols": [],
                                "imports": [],
                            },
                        ],
                        "edges": [{"from": "src/App.tsx", "to": "./api", "kind": "import"}],
                        "api_calls": [
                            {"method": "GET", "path": "/api/v1/things", "file": "src/api.ts:8"}
                        ],
                    },
                },
                "contract_drift": [],
            }
        )
    )


def test_review_sensor_renders_fresh_blast_radius(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    code_map_path = tmp_path / "code-map.json"
    _write_code_map(code_map_path, generated_at="2026-07-22T10:00:00Z")

    status, context = reviewer.build_review_sensor_context(
        "web",
        ["src/api.ts"],
        code_map_path=code_map_path,
        now=datetime(2026, 7, 22, 11, 0, tzinfo=UTC),
    )

    assert status == "ready"
    assert "Level:" in context
    assert "src/App.tsx depends on src/api.ts" in context
    assert "api endpoint GET /api/v1/things" in context
    assert "not as proof of correctness" in context


def test_review_sensor_refuses_stale_map(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    code_map_path = tmp_path / "code-map.json"
    _write_code_map(code_map_path, generated_at="2026-07-20T10:00:00Z")

    status, context = reviewer.build_review_sensor_context(
        "web",
        ["src/api.ts"],
        code_map_path=code_map_path,
        now=datetime(2026, 7, 22, 11, 0, tzinfo=UTC),
    )

    assert status == "stale"
    assert "Do not rely on it" in context
    assert "src/App.tsx" not in context


def test_review_sensor_refuses_future_map(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    code_map_path = tmp_path / "code-map.json"
    _write_code_map(code_map_path, generated_at="2026-07-23T12:00:00Z")

    status, context = reviewer.build_review_sensor_context(
        "web",
        ["src/api.ts"],
        code_map_path=code_map_path,
        now=datetime(2026, 7, 22, 11, 0, tzinfo=UTC),
    )

    assert status == "stale"
    assert "Do not rely on it" in context


def test_review_sensor_event_is_registered(tmp_path: Path) -> None:
    events = EventLog(agent="reviewer", path=tmp_path / "events.jsonl")

    events.emit(
        "review_sensor_context",
        repo="acme/web",
        status="ready",
        changed_paths=2,
    )

    record = json.loads((tmp_path / "events.jsonl").read_text())
    assert record["event"] == "review_sensor_context"
    assert record["status"] == "ready"


def test_reviewer_ignores_unmodified_seeded_guidance(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    installed = tmp_path / "prompts/reviewer.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(REVIEWER_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(reviewer, "PROMPT_PATH", installed)

    guidance = reviewer._operator_prompt_guidance("web", 17, "Trace callers", tmp_path / "web")

    assert guidance == ""


def test_reviewer_ignores_generated_guidance_from_an_older_release(
    monkeypatch, tmp_path: Path
) -> None:
    reviewer = _load_reviewer(monkeypatch)
    installed = tmp_path / "prompts/reviewer.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "<!-- alfred:auto-seed v0 -->\nObsolete provider-specific generated workflow.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reviewer, "PROMPT_PATH", installed)

    guidance = reviewer._operator_prompt_guidance("web", 17, "Trace callers", tmp_path / "web")

    assert guidance == ""


def test_reviewer_ignores_ambiguous_markerless_legacy_guidance(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    installed = tmp_path / "prompts/reviewer.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "Obsolete provider-specific generated workflow without a marker.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reviewer, "PROMPT_PATH", installed)

    guidance = reviewer._operator_prompt_guidance("web", 17, "Trace callers", tmp_path / "web")

    assert guidance == ""


def test_reviewer_requires_the_exact_operator_guidance_marker(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    installed = tmp_path / "prompts/reviewer.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "<!-- not alfred:operator-guidance v1 -->\nDo not activate this file.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reviewer, "PROMPT_PATH", installed)

    guidance = reviewer._operator_prompt_guidance("web", 17, "Trace callers", tmp_path / "web")

    assert guidance == ""


def test_reviewer_renders_operator_edited_guidance(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    installed = tmp_path / "prompts/reviewer.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "<!-- alfred:operator-guidance v1 -->\n"
        "Check ${REPO_SLUG} PR ${PR_NUMBER}: ${PR_TITLE}. Scope: ${REVIEW_REPOS}.",
        encoding="utf-8",
    )
    monkeypatch.setattr(reviewer, "PROMPT_PATH", installed)

    guidance = reviewer._operator_prompt_guidance("web", 17, "Trace callers", tmp_path / "web")

    assert "Check web PR 17: Trace callers. Scope: web." in guidance
    assert str(installed) in guidance
    assert "alfred:operator-guidance" not in guidance


def test_reviewer_preserves_unapproved_dollar_expressions(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    installed = tmp_path / "prompts/reviewer.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "<!-- alfred:operator-guidance v1 -->\n"
        "Run `echo $PATH`, keep ${PROCESS_ONLY_VALUE}, and tolerate ${BROKEN.\n"
        "Review ${REPO_SLUG} PR ${PR_NUMBER}.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROCESS_ONLY_VALUE", "SENTINEL_MUST_NOT_ENTER_PROMPT")
    monkeypatch.setattr(reviewer, "PROMPT_PATH", installed)

    guidance = reviewer._operator_prompt_guidance("web", 17, "Trace callers", tmp_path / "web")

    assert "$PATH" in guidance
    assert "${PROCESS_ONLY_VALUE}" in guidance
    assert "${BROKEN" in guidance
    assert "SENTINEL_MUST_NOT_ENTER_PROMPT" not in guidance
    assert "Review web PR 17." in guidance


def test_reviewer_passes_changed_paths_and_sensor_to_engine(monkeypatch, tmp_path: Path) -> None:
    reviewer = _load_reviewer(monkeypatch)
    captured: dict[str, object] = {}

    class _StopAfterInvoke(Exception):
        pass

    class _Events:
        firing_id = "fire-1"

        def __init__(self, **_kwargs):
            self.rows: list[tuple[str, dict]] = []

        def emit(self, name: str, **fields) -> None:
            self.rows.append((name, fields))

    class _Spend:
        def __init__(self, _agent: str):
            self.state = {"turns_today": 0, "reviews_posted": 0, "consecutive_failures": 0}

    def fake_sensor(repo: str, changed_paths: list[str]):
        captured["sensor_repo"] = repo
        captured["changed_paths"] = changed_paths
        return "ready", "SENSOR EVIDENCE"

    def fake_invoke(prompt: str, **_kwargs):
        captured["prompt"] = prompt
        raise _StopAfterInvoke

    def fake_gh_json(argv, default=None):
        if any("/files?per_page=100" in arg for arg in argv):
            return [
                [
                    {
                        "filename": "src/api-client.ts",
                        "previous_filename": "src/api.ts",
                        "status": "renamed",
                    }
                ],
                [{"filename": "tests/api.test.ts"}, {"filename": "src/api.ts"}],
            ]
        if argv[:2] == ["gh", "api"]:
            return []
        if "title,body,additions,deletions,headRefOid" in argv:
            return {
                "title": "Trace API callers",
                "body": "Review impacted callers.",
                "headRefOid": "a" * 40,
            }
        return {"state": "OPEN"} if "state" in argv else default

    monkeypatch.setattr(reviewer, "with_lock", lambda *_a, **_k: None)
    monkeypatch.setattr(reviewer, "preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(reviewer, "doctor_requested", lambda: False)
    monkeypatch.setattr(reviewer, "doctor_mode", lambda: False)
    monkeypatch.setattr(reviewer, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(reviewer, "maybe_halt_on_fail_streak", lambda *_a, **_k: False)
    monkeypatch.setattr(
        reviewer,
        "pick_pr",
        lambda: (
            "web",
            {"number": 17, "headRefOid": "a" * 40, "title": "Trace API callers"},
        ),
    )
    monkeypatch.setattr(reviewer, "EventLog", _Events)
    monkeypatch.setattr(reviewer, "SpendState", _Spend)
    monkeypatch.setattr(
        reviewer, "run", lambda *_a, **_k: SimpleNamespace(stdout="diff --git\n+x\n")
    )
    monkeypatch.setattr(reviewer, "gh_json", fake_gh_json)
    monkeypatch.setattr(reviewer, "build_review_sensor_context", fake_sensor)
    monkeypatch.setattr(reviewer, "invoke_agent_engine", fake_invoke)
    monkeypatch.setattr(reviewer, "WORKSPACE", tmp_path)
    operator_prompt = tmp_path / "reviewer-operator.md"
    operator_prompt.write_text(
        "<!-- alfred:operator-guidance v1 -->\n"
        "Inspect the ${REPO_SLUG} compatibility ledger for PR ${PR_NUMBER}.",
        encoding="utf-8",
    )
    monkeypatch.setattr(reviewer, "PROMPT_PATH", operator_prompt)

    with pytest.raises(_StopAfterInvoke):
        reviewer.main()

    assert captured["sensor_repo"] == "web"
    assert captured["changed_paths"] == [
        "src/api-client.ts",
        "src/api.ts",
        "tests/api.test.ts",
    ]
    assert "Deterministic code-map evidence:\nSENSOR EVIDENCE" in str(captured["prompt"])
    assert "for every client HTTP or API call added or changed" in str(captured["prompt"])
    assert "Inspect the web compatibility ledger for PR 17." in str(captured["prompt"])
