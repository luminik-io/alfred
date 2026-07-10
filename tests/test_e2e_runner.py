"""Focused coverage for the Huntress E2E runner."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_huntress(monkeypatch: pytest.MonkeyPatch, tests_dir: Path, **env: str):
    monkeypatch.setenv("ALFRED_HOME", str(ROOT))
    monkeypatch.setenv("ALFRED_E2E_RUNNER_TESTS_DIR", str(tests_dir))
    monkeypatch.setenv("ALFRED_E2E_RUNNER_TARGET_URL", "https://staging.example.test")
    for key, value in env.items():
        if value:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location(
        "e2e_runner_under_test", ROOT / "bin" / "e2e-runner.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    spec.loader.exec_module(module)
    return module


def patch_common_runtime(monkeypatch: pytest.MonkeyPatch, huntress):
    events: list[tuple[str, dict]] = []
    slack_messages: list[str] = []
    increments: list[dict] = []
    sets: list[dict] = []

    class FakeEventLog:
        def __init__(self, *, agent: str):
            self.agent = agent

        def emit(self, name: str, **payload) -> None:
            events.append((name, payload))

    class FakeSpendState:
        def __init__(self, agent: str):
            self.agent = agent

        def increment(self, **kwargs) -> None:
            increments.append(kwargs)

        def set(self, **kwargs) -> None:
            sets.append(kwargs)

    monkeypatch.setattr(huntress, "with_lock", lambda agent: None)
    monkeypatch.setattr(huntress, "preflight", lambda spec: None)
    monkeypatch.setattr(huntress, "doctor_mode", lambda: False)
    monkeypatch.setattr(huntress, "EventLog", FakeEventLog)
    monkeypatch.setattr(huntress, "SpendState", FakeSpendState)
    monkeypatch.setattr(huntress, "slack_post", lambda msg: slack_messages.append(msg))
    return events, slack_messages, increments, sets


def test_huntress_success_redacts_playwright_logs_and_records_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests_dir = tmp_path / "huntress-tests"
    tests_dir.mkdir()
    run_dir = tmp_path / "run"
    secret = {"email": "smoke@example.test", "password": "s3cr3t"}
    huntress = load_huntress(
        monkeypatch,
        tests_dir,
        ALFRED_E2E_RUNNER_SECRET_ID="huntress/secret",
        ALFRED_E2E_RUNNER_ECS_CLUSTER="",
        ALFRED_E2E_RUNNER_ECS_SERVICES="",
    )
    events, slack_messages, increments, sets = patch_common_runtime(monkeypatch, huntress)

    monkeypatch.setattr(
        huntress,
        "_aws",
        lambda args, timeout=30: SimpleNamespace(
            returncode=0, stdout=json.dumps(secret), stderr=""
        ),
    )

    def fake_run(args, *, cwd, env, capture_output, text, timeout):
        assert args == ["npx", "playwright", "test", "--reporter=json"]
        assert cwd == str(tests_dir)
        assert env["HUNTRESS_BASE_URL"] == "https://staging.example.test"
        assert env["HUNTRESS_EMAIL"] == secret["email"]
        assert env["HUNTRESS_PASSWORD"] == secret["password"]
        assert env["HUNTRESS_RUN_DIR"] == str(run_dir)
        return SimpleNamespace(
            returncode=0,
            stdout=f"login {secret['email']} {secret['password']}",
            stderr=f"stderr {secret['password']}",
        )

    def fake_secure_run_dir(agent: str) -> Path:
        run_dir.mkdir()
        return run_dir

    monkeypatch.setattr(huntress, "secure_run_dir", fake_secure_run_dir)
    monkeypatch.setattr(huntress.subprocess, "run", fake_run)

    assert huntress.main() == 0

    assert "[SILENT]" in capsys.readouterr().out
    assert (run_dir / "stdout.json").read_text() == "login [REDACTED] [REDACTED]"
    assert (run_dir / "stderr.log").read_text() == "stderr [REDACTED]"
    assert slack_messages == []
    assert ("firing_complete", {"outcome": "green"}) in events
    assert {"firings_today": 1} in increments
    assert {"successes_today": 1} in increments
    assert {"consecutive_failures": 0} in sets


def test_huntress_blocks_on_malformed_test_account_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests_dir = tmp_path / "huntress-tests"
    tests_dir.mkdir()
    huntress = load_huntress(
        monkeypatch,
        tests_dir,
        ALFRED_E2E_RUNNER_SECRET_ID="huntress/secret",
        ALFRED_E2E_RUNNER_ECS_CLUSTER="",
        ALFRED_E2E_RUNNER_ECS_SERVICES="",
    )
    events, slack_messages, _increments, _sets = patch_common_runtime(monkeypatch, huntress)
    monkeypatch.setattr(
        huntress,
        "_aws",
        lambda args, timeout=30: SimpleNamespace(returncode=0, stdout="{not-json", stderr=""),
    )

    def fail_playwright(*_args, **_kwargs):
        raise AssertionError("Playwright should not run with a malformed secret")

    monkeypatch.setattr(huntress.subprocess, "run", fail_playwright)

    assert huntress.main() == 0

    out = capsys.readouterr().out
    assert "[E2E-RUNNER-BLOCKED] malformed test account secret" in out
    assert slack_messages == [out.strip()]
    assert ("firing_complete", {"outcome": "blocked-bad-secret"}) in events


def test_huntress_blocks_when_ecs_service_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests_dir = tmp_path / "huntress-tests"
    tests_dir.mkdir()
    huntress = load_huntress(
        monkeypatch,
        tests_dir,
        ALFRED_E2E_RUNNER_SECRET_ID="huntress/secret",
        ALFRED_E2E_RUNNER_ECS_CLUSTER="staging",
        ALFRED_E2E_RUNNER_ECS_SERVICES="web",
    )
    events, slack_messages, _increments, _sets = patch_common_runtime(monkeypatch, huntress)

    monkeypatch.setattr(
        huntress,
        "_aws",
        lambda args, timeout=30: SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"name": "web", "running": 1, "desired": 2}]),
            stderr="",
        ),
    )

    def fail_playwright(*_args, **_kwargs):
        raise AssertionError("Playwright should not run while staging is not ready")

    monkeypatch.setattr(huntress.subprocess, "run", fail_playwright)

    assert huntress.main() == 0

    out = capsys.readouterr().out
    assert "[E2E-RUNNER-STAGING-NOT-READY] web running=1 desired=2" in out
    assert slack_messages == [out.strip()]
    assert (
        "firing_complete",
        {"outcome": "blocked-staging-not-ready", "service": "web"},
    ) in events
