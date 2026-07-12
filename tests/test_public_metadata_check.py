from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_metadata_check", ROOT / "bin" / "public-metadata-check.py"
)
assert SPEC and SPEC.loader
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_concise_summary_is_clean() -> None:
    body = "## Summary\n\n- require an explicit setup decision\n\n## Verification\n\n- tests pass"
    assert CHECK.metadata_findings("fix: require setup decision", body) == []


def test_operator_home_path_is_rejected() -> None:
    path = "/" + "Users" + "/developer/work/private-repo/test.py"
    assert CHECK.metadata_findings("fix: setup", f"Failure at {path}") == ["local filesystem path"]


def test_bare_private_home_paths_are_rejected() -> None:
    paths = [
        "/home/alice",
        "/" + "Users" + "/bob",
        "C:/" + "Users" + "/carol",
    ]
    for path in paths:
        assert CHECK.metadata_findings("fix: setup", f"Failure at {path}") == [
            "local filesystem path"
        ]


def test_dotted_private_accounts_are_rejected() -> None:
    paths = [
        "/home/user.name/private-repo",
        "C:/" + "Users" + "/user.name/log.txt",
    ]
    for path in paths:
        assert CHECK.metadata_findings("fix: setup", f"Failure at {path}") == [
            "local filesystem path"
        ]


def test_forward_slash_windows_home_path_is_rejected() -> None:
    path = "C:/" + "Users" + "/alice/work/private-repo/test.py"
    assert CHECK.metadata_findings("fix: setup", f"Failure at {path}") == ["local filesystem path"]


def test_generic_home_examples_are_allowed() -> None:
    examples = [
        "/home/user/.local/bin",
        "/home/runner/work/project",
        "/" + "Users" + "/Shared/tool",
        "/home/user",
        "Install under /home/user.",
    ]
    assert CHECK.metadata_findings("docs: examples", "\n".join(examples)) == []


def test_workspace_and_temporary_paths_are_rejected() -> None:
    for path in ("/workspace/alfred/test.py", "/tmp/run.log"):
        assert CHECK.metadata_findings("fix: setup", f"Failure at {path}") == [
            "local filesystem path"
        ]


def test_raw_test_progress_is_rejected() -> None:
    body = "Verification\n" + "." * 40 + " [ 52%]"
    assert CHECK.metadata_findings("fix: setup", body) == [
        "raw command, test, compiler, or stack output"
    ]


def test_colon_prefixed_failure_output_is_rejected() -> None:
    for line in ("ERROR: command failed", "FAIL: tests/test_api.py::test_case"):
        assert CHECK.metadata_findings("fix: setup", line) == [
            "raw command, test, compiler, or stack output"
        ]


def test_pytest_failed_summary_is_rejected() -> None:
    line = "FAILED tests/test_api.py::test_case - AssertionError"
    assert CHECK.metadata_findings("fix: setup", line) == [
        "raw command, test, compiler, or stack output"
    ]


def test_error_handling_prose_is_allowed() -> None:
    assert CHECK.metadata_findings("refactor: errors", "ERROR handling is now centralized") == []
    assert CHECK.metadata_findings("refactor: errors", "ERROR: handling is now centralized") == []


def test_oversized_description_is_rejected() -> None:
    body = "line\n" * (CHECK.MAX_BODY_LINES + 1)
    assert CHECK.metadata_findings("fix: setup", body) == ["oversized PR description"]


def test_existing_private_identifier_scrub_applies_to_pr_metadata() -> None:
    private_repo = "luminik-" + "orchestrator"
    assert CHECK._existing_scrub_rejects("fix: setup", f"Validated in {private_repo}") is True


def test_clean_commit_messages_are_allowed() -> None:
    commits = "fix: tighten metadata detection\n\nfix: catch pytest failure summaries\n"
    assert CHECK.commit_findings(commits) == []


def test_commit_message_with_local_path_is_rejected() -> None:
    path = "/" + "Users" + "/developer/work/private-repo/test.py"
    assert CHECK.commit_findings(f"fix: setup\n\nReproduced at {path}\n") == [
        "local filesystem path in a commit message"
    ]


def test_commit_message_with_raw_output_is_rejected() -> None:
    commits = "fix: setup\n\nFAILED tests/test_api.py::test_case - AssertionError\n"
    assert CHECK.commit_findings(commits) == [
        "raw command, test, compiler, or stack output in a commit message"
    ]


def test_commit_scrub_catches_private_identifier() -> None:
    private_repo = "luminik-" + "orchestrator"
    assert CHECK._scrub_rejects_text(f"fix: validated in {private_repo}\n") is True
