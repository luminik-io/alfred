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
    assert CHECK.metadata_findings("fix: setup", f"Failure at {path}") == [
        "local home-directory path"
    ]


def test_raw_test_progress_is_rejected() -> None:
    body = "Verification\n" + "." * 40 + " [ 52%]"
    assert CHECK.metadata_findings("fix: setup", body) == [
        "raw command, test, compiler, or stack output"
    ]


def test_oversized_description_is_rejected() -> None:
    body = "line\n" * (CHECK.MAX_BODY_LINES + 1)
    assert CHECK.metadata_findings("fix: setup", body) == ["oversized PR description"]


def test_existing_private_identifier_scrub_applies_to_pr_metadata() -> None:
    private_repo = "luminik-" + "orchestrator"
    assert CHECK._existing_scrub_rejects("fix: setup", f"Validated in {private_repo}") is True
