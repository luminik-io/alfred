from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def gate_module():
    path = ROOT / "bin" / "alfred-pr-gate.py"
    spec = importlib.util.spec_from_file_location("alfred_pr_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payloads(head: str) -> list[object]:
    return [
        {
            "headRefOid": head,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "state": "OPEN",
            "statusCheckRollup": [
                {"name": "pytest", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
        },
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "headRefOid": head,
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "reviewThreads": {"nodes": []},
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "object": {
                        "statusCheckRollup": {
                            "contexts": {
                                "nodes": [
                                    {
                                        "__typename": "CheckRun",
                                        "name": "pytest",
                                        "status": "COMPLETED",
                                        "conclusion": "SUCCESS",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }
        },
        [
            {
                "user": {"login": "greptile-apps[bot]"},
                "updated_at": "2026-07-11T10:00:00Z",
                "body": (
                    "Confidence Score: 5/5\nLast reviewed commit: "
                    f"https://github.com/acme/app/commit/{head}"
                ),
            },
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "updated_at": "2026-07-11T10:01:00Z",
                "body": f"Reviewed commit: `{head[:10]}`\nNo major issues.",
            },
        ],
        [
            {
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "commit_id": head,
                "submitted_at": "2026-07-11T10:02:00Z",
            }
        ],
    ]


def _fake_run(payloads: list[object]):
    queue = list(payloads)

    def run(command, **kwargs):
        assert queue, command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(queue.pop(0)), stderr="")

    return run


def test_collect_snapshot_accepts_exact_head_green_gate(gate_module, monkeypatch):
    head = "a" * 40
    monkeypatch.setattr(gate_module.subprocess, "run", _fake_run(_payloads(head)))

    snapshot = gate_module.collect_snapshot("acme/app", 42)

    assert snapshot.head == head
    assert snapshot.unresolved_threads == 0
    assert snapshot.greptile_score == "5/5"
    assert snapshot.codex_commit == head


def test_collect_snapshot_selects_latest_edited_greptile_summary(gate_module, monkeypatch):
    head = "b" * 40
    payloads = _payloads(head)
    comments = payloads[3]
    comments.insert(
        0,
        {
            "user": {"login": "greptile-apps[bot]"},
            "updated_at": "2026-07-10T10:00:00Z",
            "body": f"Confidence Score: 5/5\nLast reviewed commit: {'c' * 40}",
        },
    )
    monkeypatch.setattr(gate_module.subprocess, "run", _fake_run(payloads))

    assert gate_module.collect_snapshot("acme/app", 42).greptile_commit == head


def test_collect_snapshot_blocks_late_unresolved_thread(gate_module, monkeypatch):
    head = "d" * 40
    payloads = _payloads(head)
    payloads[1]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [
        {"isResolved": False}
    ]
    monkeypatch.setattr(gate_module.subprocess, "run", _fake_run(payloads))

    with pytest.raises(gate_module.GateError, match="unresolved review thread"):
        gate_module.collect_snapshot("acme/app", 42)


def test_collect_snapshot_blocks_stale_codex_review(gate_module, monkeypatch):
    head = "e" * 40
    payloads = _payloads(head)
    payloads[3][1]["body"] = f"Reviewed commit: `{'f' * 10}`"
    payloads[4][0]["commit_id"] = "f" * 40
    monkeypatch.setattr(gate_module.subprocess, "run", _fake_run(payloads))

    with pytest.raises(gate_module.GateError, match="Codex has not reviewed exact HEAD"):
        gate_module.collect_snapshot("acme/app", 42)


def test_collect_snapshot_blocks_latest_changes_requested_review(gate_module, monkeypatch):
    head = "9" * 40
    payloads = _payloads(head)
    payloads[4].append(
        {
            "user": {"login": "human-reviewer"},
            "commit_id": head,
            "submitted_at": "2026-07-11T10:10:00Z",
            "state": "CHANGES_REQUESTED",
        }
    )
    monkeypatch.setattr(gate_module.subprocess, "run", _fake_run(payloads))

    with pytest.raises(gate_module.GateError, match="changes requested by human-reviewer"):
        gate_module.collect_snapshot("acme/app", 42)


def test_matches_head_rejects_abbreviated_review_sha(gate_module):
    head = "abcdef1" + "0" * 33

    assert not gate_module._matches_head(head[:10], head)
    assert gate_module._matches_head(head, head)


def test_collect_snapshot_resolves_unique_codex_abbreviation(gate_module, monkeypatch):
    head = "abcdef1234" + "0" * 30
    payloads = _payloads(head)
    payloads[4][0]["commit_id"] = "f" * 40
    payloads[4][0]["submitted_at"] = "2026-07-11T10:00:00Z"
    payloads.append({"sha": head})
    monkeypatch.setattr(gate_module.subprocess, "run", _fake_run(payloads))

    snapshot = gate_module.collect_snapshot("acme/app", 42)

    assert snapshot.codex_commit == head


def test_graphql_checks_fetches_every_page(gate_module, monkeypatch):
    head = "7" * 40
    pages = [
        {
            "data": {
                "repository": {
                    "object": {
                        "statusCheckRollup": {
                            "contexts": {
                                "nodes": [
                                    {
                                        "__typename": "CheckRun",
                                        "name": "first",
                                        "status": "COMPLETED",
                                        "conclusion": "SUCCESS",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                            }
                        }
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "object": {
                        "statusCheckRollup": {
                            "contexts": {
                                "nodes": [
                                    {
                                        "__typename": "StatusContext",
                                        "context": "second",
                                        "state": "FAILURE",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }
        },
    ]
    seen: list[list[str]] = []

    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(pages.pop(0)), stderr="")

    monkeypatch.setattr(gate_module.subprocess, "run", run)

    checks = gate_module._graphql_checks("acme", "app", head)

    assert [check["name"] for check in checks] == ["first", "second"]
    assert "after=next" in seen[1]


def test_merge_uses_expected_sha_and_squash(gate_module, monkeypatch):
    head = "1" * 40
    snapshot = gate_module.GateSnapshot(
        repo="acme/app",
        number=42,
        head=head,
        mergeable="MERGEABLE",
        merge_state="CLEAN",
        unresolved_threads=0,
        checks=("pytest",),
        greptile_commit=head,
        greptile_score="5/5",
        codex_commit=head,
        review_decision="",
        state="OPEN",
    )
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({"merged": True, "sha": "merge-sha"}), stderr=""
        )

    monkeypatch.setattr(gate_module.subprocess, "run", run)

    assert gate_module.merge(snapshot) == "merge-sha"
    assert "merge_method=squash" in seen[0]
    assert f"sha={head}" in seen[0]


def test_alfred_pr_cli_forwards_fail_closed_gate_arguments(monkeypatch):
    loader = SourceFileLoader("alfred_cli_pr_gate", str(ROOT / "bin" / "alfred"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    rc = module.cmd_pr(
        SimpleNamespace(
            pr_action="merge",
            number=42,
            repo="acme/app",
            skip_greptile=False,
            skip_codex=False,
            json=True,
        )
    )

    assert rc == 0
    assert calls == [
        [
            sys.executable,
            str(ROOT / "bin" / "alfred-pr-gate.py"),
            "merge",
            "42",
            "--repo",
            "acme/app",
            "--json",
        ]
    ]
