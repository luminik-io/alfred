"""Transaction tests for ``alfred batteries`` installs."""

from __future__ import annotations

import importlib.util
import json
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


def test_install_prepares_dependency_without_enabling(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import batteries

    installed_checks = iter([False, True])
    monkeypatch.setattr(batteries, "is_installed", lambda _battery, _env: next(installed_checks))
    monkeypatch.setattr(cli, "_battery_run_install", lambda _args, _battery, **_kwargs: 0)

    assert cli.main(["batteries", "install", "dense-embeddings", "--yes"]) == 0

    env_path = tmp_path / ".alfred" / ".env"
    assert not env_path.exists() or "ALFRED_MEMORY_SQLITE_DENSE=1" not in env_path.read_text()


def test_install_fails_when_command_succeeds_but_dependency_is_still_missing(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import batteries

    monkeypatch.setattr(batteries, "is_installed", lambda _battery, _env: False)
    monkeypatch.setattr(cli, "_run_subcommand", lambda *_args, **_kwargs: 0)

    assert cli.main(["batteries", "install", "code-memory-mcp", "--yes"]) == 1

    env_path = tmp_path / ".alfred" / ".env"
    assert not env_path.exists() or "ALFRED_CODE_MEMORY_MCP=1" not in env_path.read_text()


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


def test_daemon_enable_print_command_does_not_mutate_config(
    cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path = tmp_path / ".alfred" / ".env"

    assert cli.main(["batteries", "enable", "redis-ams", "--print-command"]) == 0

    output = capsys.readouterr().out
    assert "alfred batteries enable redis-ams --yes" in output
    assert not env_path.exists()


def test_disable_print_command_does_not_mutate_config(
    cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path = tmp_path / ".alfred" / ".env"
    env_path.parent.mkdir(parents=True)
    original = "ALFRED_CODE_MEMORY_MCP=1\nALFRED_CODE_MEMORY_AUTOFETCH=1\n"
    env_path.write_text(original, encoding="utf-8")

    assert cli.main(["batteries", "disable", "code-memory-mcp", "--print-command"]) == 0

    output = capsys.readouterr().out
    assert "alfred batteries disable code-memory-mcp --yes" in output
    assert env_path.read_text(encoding="utf-8") == original


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


def test_remove_refuses_to_change_an_enabled_battery(
    cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path = tmp_path / ".alfred" / ".env"
    env_path.parent.mkdir(parents=True)
    original = "ALFRED_COMPRESSION_ENGINE=headroom\n"
    env_path.write_text(original, encoding="utf-8")

    assert cli.main(["batteries", "remove", "headroom-compression", "--yes"]) == 1

    assert "disable headroom-compression first" in capsys.readouterr().out
    assert env_path.read_text(encoding="utf-8") == original


def test_remove_prints_exact_local_uninstall_without_mutating_config(
    cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path = tmp_path / ".alfred" / ".env"
    env_path.parent.mkdir(parents=True)
    original = "ALFRED_COMPRESSION_ENGINE=builtin\n"
    env_path.write_text(original, encoding="utf-8")

    assert cli.main(["batteries", "remove", "headroom-compression", "--print-command"]) == 0

    output = capsys.readouterr().out
    assert f"{sys.executable} -m pip uninstall -y headroom-ai" in output
    assert env_path.read_text(encoding="utf-8") == original


def test_remove_runs_local_uninstall_without_mutating_config(
    cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".alfred" / ".env"
    env_path.parent.mkdir(parents=True)
    original = "ALFRED_MEMORY_SQLITE_DENSE=0\n"
    env_path.write_text(original, encoding="utf-8")
    commands: list[list[str]] = []

    def run(command, *, timeout, env=None):
        assert timeout > 0
        commands.append(command)
        return 0

    monkeypatch.setattr(cli, "_run_subcommand", run)

    assert cli.main(["batteries", "remove", "dense-embeddings", "--yes"]) == 0
    assert commands == [[sys.executable, "-m", "pip", "uninstall", "-y", "sqlite-vec"]]
    assert env_path.read_text(encoding="utf-8") == original


def test_remove_external_service_prints_operator_guidance(
    cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path = tmp_path / ".alfred" / ".env"
    env_path.parent.mkdir(parents=True)
    original = "ALFRED_MEMORY_PROVIDERS=sqlite,fleet\n"
    env_path.write_text(original, encoding="utf-8")

    assert cli.main(["batteries", "remove", "redis-ams", "--yes"]) == 0

    assert "operator-managed service" in capsys.readouterr().out
    assert env_path.read_text(encoding="utf-8") == original


def test_list_uses_plain_setup_groups(
    cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import batteries

    monkeypatch.setattr(batteries, "_graphify_available", lambda _env: False)
    monkeypatch.setattr(batteries, "_ams_reachable", lambda _env: False)
    monkeypatch.setattr(batteries, "_headroom_available", lambda _env: False)
    monkeypatch.setattr(batteries, "_find_spec", lambda _name: False)
    build_manifest = batteries.manifest
    monkeypatch.setattr(
        batteries,
        "manifest",
        lambda: build_manifest({"ALFRED_HOME": "/missing"}),
    )

    assert cli.main(["batteries", "list"]) == 0
    output = capsys.readouterr().out
    assert "Included" in output
    assert "Optional local tools" in output
    assert "External services" in output
    assert "Advanced integrations" not in output


@pytest.mark.parametrize(
    "arguments",
    (
        ["batteries", "--json", "list"],
        ["batteries", "list", "--json"],
    ),
)
def test_list_accepts_json_before_or_after_subcommand(
    cli,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    assert cli.main(arguments) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 2
    assert payload["batteries"]
