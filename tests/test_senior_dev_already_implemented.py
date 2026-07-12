from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recovery_identity", ROOT / "lib" / "agent_runner" / "recovery_identity.py"
)
assert SPEC and SPEC.loader
RECOVERY_IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY_IDENTITY)


def test_already_implemented_can_close_when_base_contains_the_work() -> None:
    assert (
        RECOVERY_IDENTITY.already_implemented_disposition(
            "[ALREADY-IMPLEMENTED] src/example.py:12", 0
        )
        == "shipped-on-base"
    )


def test_any_ahead_work_is_quarantined() -> None:
    assert (
        RECOVERY_IDENTITY.already_implemented_disposition(
            "[ALREADY-IMPLEMENTED] src/example.py:12", 1
        )
        == "quarantine-ahead-work"
    )


def test_multiple_ahead_commits_are_quarantined() -> None:
    assert (
        RECOVERY_IDENTITY.already_implemented_disposition(
            "[ALREADY-IMPLEMENTED] src/example.py:12", 3
        )
        == "quarantine-ahead-work"
    )


def test_unmarked_result_uses_normal_flow() -> None:
    assert (
        RECOVERY_IDENTITY.already_implemented_disposition("[OK] commit abc123", 1) == "not-marked"
    )
