from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_cli_module():
    loader = importlib.machinery.SourceFileLoader("alfred_cli_for_test", str(ROOT / "bin/alfred"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_serve_module():
    loader = importlib.machinery.SourceFileLoader(
        "alfred_serve_for_test",
        str(ROOT / "bin/alfred-serve.py"),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_serve_parser_defaults_to_desktop_port():
    serve = load_serve_module()

    args = serve._build_parser().parse_args(["--no-browser"])

    assert args.host == "127.0.0.1"
    assert args.port == 7010
    assert args.no_browser is True


def test_serve_forwards_supported_server_args(tmp_path, monkeypatch):
    cli = load_cli_module()
    calls = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            self.command = command

        def wait(self, timeout=None):
            calls.append((self.command, False, timeout))
            return 0

    monkeypatch.delenv("ALFRED_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)

    assert (
        cli.main(
            [
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "7010",
                "--no-browser",
                "--log-level",
                "debug",
            ]
        )
        == 0
    )

    assert calls == [
        (
            [
                sys.executable,
                str(ROOT / "bin/alfred-serve.py"),
                "--host",
                "127.0.0.1",
                "--port",
                "7010",
                "--no-browser",
                "--log-level",
                "debug",
            ],
            False,
            None,
        )
    ]


def test_serve_uses_managed_alfred_venv_when_present(tmp_path, monkeypatch):
    cli = load_cli_module()
    alfred_home = tmp_path / "alfred"
    venv_python = alfred_home / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    venv_python.chmod(0o755)
    calls = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            self.command = command

        def wait(self, timeout=None):
            calls.append((self.command, False, timeout))
            return 0

    monkeypatch.setenv("ALFRED_HOME", str(alfred_home))
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)

    assert cli.main(["serve", "--no-browser"]) == 0

    assert calls == [
        (
            [
                str(venv_python),
                str(ROOT / "bin/alfred-serve.py"),
                "--no-browser",
            ],
            False,
            None,
        )
    ]


def test_bounded_subcommand_returns_timeout_status(monkeypatch, capsys):
    cli = load_cli_module()

    class FakeProcess:
        pid = 123

        def __init__(self, command, **_kwargs):
            self.command = command
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise cli.subprocess.TimeoutExpired(self.command, timeout)
            return -15

        def poll(self):
            return None

    signals = []
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    assert cli._run_subcommand(["slow-command"], timeout=2) == 124
    assert "timed out after 2s" in capsys.readouterr().err
    assert signals == [(123, cli.signal.SIGTERM), (123, cli.signal.SIGKILL)]


def test_bounded_subcommand_timeout_redacts_secret_args(monkeypatch, capsys):
    cli = load_cli_module()

    class FakeProcess:
        pid = 123

        def __init__(self, command, **_kwargs):
            self.command = command
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise cli.subprocess.TimeoutExpired(self.command, timeout)
            return -15

        def poll(self):
            return None

    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: None)

    cmd = [
        "alfred-setup-token.py",
        "--token",
        "xoxb-super-secret",
        "--slack-bot-token=xoxb-other-secret",
        "--force",
    ]
    assert cli._run_subcommand(cmd, timeout=2) == 124
    err = capsys.readouterr().err
    assert "xoxb-super-secret" not in err
    assert "xoxb-other-secret" not in err
    assert "--token" in err
    assert "--slack-bot-token=[redacted]" in err
    assert "--force" in err


def test_redact_secret_args_preserves_non_secret_arguments():
    cli = load_cli_module()

    cmd = ["script.py", "--token", "s3cret", "--mode", "fast", "--slack-bot-token=abc"]
    assert cli._redact_secret_args(cmd) == [
        "script.py",
        "--token",
        "[redacted]",
        "--mode",
        "fast",
        "--slack-bot-token=[redacted]",
    ]
    assert cmd[2] == "s3cret"


def test_bounded_subcommand_cleans_process_group_on_interrupt(monkeypatch):
    cli = load_cli_module()

    class FakeProcess:
        pid = 321

        def __init__(self, _command, **_kwargs):
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            return -15

    signals = []
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(KeyboardInterrupt):
        cli._run_subcommand(["interrupt-me"], timeout=2)

    assert signals == [(321, cli.signal.SIGTERM), (321, cli.signal.SIGKILL)]


def test_persistent_subcommand_cleans_process_group_on_sigterm(monkeypatch):
    cli = load_cli_module()
    handlers = {}

    def fake_signal(signum, handler):
        previous = handlers.get(signum, cli.signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    class FakeProcess:
        pid = 432

        def __init__(self, _command, **_kwargs):
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                handlers[cli.signal.SIGTERM](cli.signal.SIGTERM, None)
            return -15

    signals = []
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cli.signal, "signal", fake_signal)
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(SystemExit) as exc:
        cli._run_subcommand(["persistent-service"], timeout=None)

    assert exc.value.code == 128 + cli.signal.SIGTERM
    assert signals == [(432, cli.signal.SIGTERM), (432, cli.signal.SIGKILL)]
    assert handlers[cli.signal.SIGTERM] == cli.signal.SIG_DFL


def test_code_memory_serve_is_unbounded(monkeypatch):
    cli = load_cli_module()
    calls = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            self.command = command

        def wait(self, timeout=None):
            calls.append((self.command, timeout))
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)

    args = SimpleNamespace(code_memory_args=["serve"])
    assert cli.cmd_code_memory(args) == 0
    assert calls == [([str(ROOT / "bin/code-memory-mcp"), "serve"], None)]


@pytest.mark.parametrize(
    ("forwarded", "expected_timeout"),
    [
        ([], None),
        (["serve"], None),
        (["tools"], 600),
        (["export"], 600),
    ],
)
def test_memory_mcp_only_leaves_server_unbounded(monkeypatch, forwarded, expected_timeout):
    cli = load_cli_module()
    calls = []
    monkeypatch.setattr(
        cli,
        "_run_subcommand",
        lambda command, *, timeout, env=None: calls.append((command, timeout)) or 0,
    )

    args = SimpleNamespace(mcp_args=forwarded)
    assert cli.cmd_mcp(args) == 0

    expected_args = forwarded or ["serve"]
    assert calls == [
        (
            [sys.executable, str(ROOT / "bin/alfred-mcp.py"), *expected_args],
            expected_timeout,
        )
    ]


@pytest.mark.parametrize("command", ["index", "refresh"])
def test_code_memory_rebuilds_get_large_repo_budget(monkeypatch, command):
    cli = load_cli_module()
    calls = []
    monkeypatch.setattr(
        cli,
        "_run_subcommand",
        lambda argv, *, timeout, env=None: calls.append((argv, timeout)) or 0,
    )

    args = SimpleNamespace(code_memory_args=[command])
    assert cli.cmd_code_memory(args) == 0
    assert calls == [
        ([str(ROOT / "bin/code-memory-mcp"), command], cli._CODE_MEMORY_INDEX_TIMEOUT_S)
    ]
    assert calls[0][1] > cli._DELEGATED_COMMAND_TIMEOUT_S


def test_benchmark_wrapper_allows_full_bounded_ab_run(monkeypatch):
    cli = load_cli_module()
    calls = []
    monkeypatch.setattr(
        cli,
        "_run_subcommand",
        lambda command, *, timeout, env=None: calls.append((command, timeout, env)) or 0,
    )

    args = SimpleNamespace(benchmark_args=["memory", "--engine", "claude"])
    assert cli.cmd_benchmark(args) == 0

    assert calls[0][1] == 8 * 60 * 60
    assert calls[0][1] > cli._DELEGATED_COMMAND_TIMEOUT_S
