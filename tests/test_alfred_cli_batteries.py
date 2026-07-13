"""Transaction tests for ``alfred batteries`` installs."""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "alfred"
sys.path.insert(0, str(ROOT / "lib"))


@pytest.fixture()
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    loader = SourceFileLoader("alfred_cli_batteries", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


def test_failed_install_does_not_write_enabled_flag(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import batteries

    monkeypatch.setattr(batteries, "is_installed", lambda _battery, _env: False)
    monkeypatch.setattr(cli, "_battery_run_install", lambda _args, _battery, **_kwargs: 1)

    assert cli.main(["batteries", "enable", "dense-embeddings", "--yes"]) == 1

    env_path = tmp_path / ".alfred" / ".env"
    assert not env_path.exists() or "ALFRED_MEMORY_SQLITE_DENSE=1" not in env_path.read_text()


def test_reenable_autofetch_battery_uses_prospective_env(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import batteries

    env_path = tmp_path / ".alfred" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "ALFRED_CODE_MEMORY_MCP=0\nALFRED_CODE_MEMORY_AUTOFETCH=0\n",
        encoding="utf-8",
    )
    installed_checks = iter([False, True])
    monkeypatch.setattr(batteries, "is_installed", lambda _battery, _env: next(installed_checks))
    seen_env: dict[str, str] = {}

    def run_install(_command, *, timeout, env=None):
        assert timeout > 0
        seen_env.update(env or {})
        return 0

    monkeypatch.setattr(cli, "_run_subcommand", run_install)

    assert cli.main(["batteries", "enable", "code-memory-mcp", "--yes"]) == 0

    assert seen_env["ALFRED_CODE_MEMORY_MCP"] == "1"
    assert seen_env["ALFRED_CODE_MEMORY_AUTOFETCH"] == "1"
    saved = batteries.load_env({"ALFRED_HOME": str(tmp_path / ".alfred")})
    assert saved["ALFRED_CODE_MEMORY_MCP"] == "1"
    assert saved["ALFRED_CODE_MEMORY_AUTOFETCH"] == "1"


def test_autofetch_print_command_includes_prospective_env(
    cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import batteries

    monkeypatch.setattr(batteries, "is_installed", lambda _battery, _env: False)

    assert cli.main(["batteries", "enable", "code-memory-mcp", "--print-command"]) == 0

    output = capsys.readouterr().out
    assert "ALFRED_CODE_MEMORY_AUTOFETCH=1" in output
    assert "&& alfred batteries enable code-memory-mcp --yes" in output


def test_print_command_includes_follow_up_enable_without_mutating_config(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import batteries

    monkeypatch.setattr(batteries, "is_installed", lambda _battery, _env: False)

    assert cli.main(["batteries", "enable", "dense-embeddings", "--print-command"]) == 0

    output = capsys.readouterr().out
    assert "&& alfred batteries enable dense-embeddings --yes" in output
    env_path = tmp_path / ".alfred" / ".env"
    assert not env_path.exists() or "ALFRED_MEMORY_SQLITE_DENSE=1" not in env_path.read_text()
