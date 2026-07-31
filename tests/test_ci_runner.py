from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

import ci_runner  # noqa: E402


def write_config(tmp_path: Path, replacement: tuple[str, str] | None = None) -> Path:
    template = tmp_path / "lima.yaml"
    template.write_text(
        "\n".join(
            [
                "plain: true",
                "vmType: vz",
                "arch: aarch64",
                "mounts: []",
                "portForwards: []",
                "user:",
                "  passwordlessSudo: false",
                "ssh:",
                "  forwardAgent: false",
                "propagateProxyEnv: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "runner.toml"
    content = """
[runner]
organization = "luminik-io"
repository = "luminik-io/alfred"
runner_group = "mac-mini-disposable"
instance_prefix = "alfred-ci"
job_label_prefix = "alfred-job"
cpus = 4
memory_gib = 6
disk_gib = 40
job_timeout_minutes = 90
lima_template = "lima.yaml"
runner_version = "2.336.0"
runner_sha256 = "58b758e420b87093fbd4bfddd368074960053e2f1388f01848c82624b90f27d1"
uv_version = "0.12.0"
uv_sha256 = "2c5d6e3092cc5223b10ff403880cc75121bf64e84644e7a0c69f643b0d89ac95"

[fallback]
context = "Hermes / Local CI"
commands = [["python3", "-m", "pytest", "tests/", "-q"]]
""".lstrip()
    if replacement is not None:
        content = content.replace(*replacement)
    config.write_text(content, encoding="utf-8")
    return config


def completed(
    arguments: list[str] | tuple[str, ...],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


def test_load_config_accepts_bounded_allowlisted_values(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    assert config.organization == "luminik-io"
    assert config.repository == "luminik-io/alfred"
    assert config.runner_group == "mac-mini-disposable"
    assert config.cpus == 4
    assert config.memory_gib == 6
    assert config.disk_gib == 40
    assert config.job_timeout_minutes == 90
    assert config.job_label_prefix == "alfred-job"
    assert config.uv_version == "0.12.0"
    assert config.fallback.commands == (("python3", "-m", "pytest", "tests/", "-q"),)
    assert config.repository_url == "https://github.com/luminik-io/alfred"
    assert config.organization_url == "https://github.com/luminik-io"
    assert config.clone_url == "https://github.com/luminik-io/alfred.git"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('repository = "luminik-io/alfred"', 'repository = "../wrong"', "owner/name"),
        (
            'organization = "luminik-io"',
            'organization = "other-org"',
            "configured organization",
        ),
        (
            'runner_group = "mac-mini-disposable"',
            'runner_group = "Unsafe Group"',
            "runner_group",
        ),
        ("cpus = 4", "cpus = 5", "cpus"),
        ("memory_gib = 6", "memory_gib = 7", "memory_gib"),
        ("disk_gib = 40", "disk_gib = 41", "disk_gib"),
        ("job_timeout_minutes = 90", "job_timeout_minutes = 91", "job_timeout"),
        ('job_label_prefix = "alfred-job"', 'job_label_prefix = "Bad label"', "job_label"),
        ('job_label_prefix = "alfred-job"', f'job_label_prefix = "{"a" * 33}"', "job_label"),
        (
            'runner_sha256 = "58b758e420b87093fbd4bfddd368074960053e2f1388f01848c82624b90f27d1"',
            'runner_sha256 = "short"',
            "SHA-256",
        ),
        ('uv_version = "0.12.0"', 'uv_version = "latest"', "uv_version"),
        (
            'uv_sha256 = "2c5d6e3092cc5223b10ff403880cc75121bf64e84644e7a0c69f643b0d89ac95"',
            'uv_sha256 = "short"',
            "SHA-256",
        ),
        (
            'commands = [["python3", "-m", "pytest", "tests/", "-q"]]',
            'commands = [["python3", "bad\\nargument"]]',
            "unsafe argument",
        ),
    ],
)
def test_load_config_rejects_unsafe_or_unbounded_values(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    with pytest.raises(ci_runner.ConfigurationError, match=message):
        ci_runner.load_config(write_config(tmp_path, (old, new)))


def test_load_config_requires_existing_lima_template(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        ('lima_template = "lima.yaml"', 'lima_template = "missing.yaml"'),
    )

    with pytest.raises(ci_runner.ConfigurationError, match="does not exist"):
        ci_runner.load_config(config_path)


def test_effective_template_contract_uses_canonical_parsed_settings() -> None:
    output = """
vmType: vz
vmOpts:
  vz:
    rosetta:
      enabled: false
arch: aarch64
ssh:
  forwardAgent: false
containerd:
  system: false
  user: false
propagateProxyEnv: false
plain: true
user:
  passwordlessSudo: false
""".lstrip()

    assert ci_runner._effective_template_errors(output) == []


def test_effective_template_contract_rejects_comments_and_unsafe_values() -> None:
    output = """
plain: false
description: '# plain: true and mounts: []'
vmType: vz
arch: aarch64
mounts:
- location: /Users/operator
ssh:
  forwardAgent: true
containerd:
  system: false
  user: false
vmOpts:
  vz:
    rosetta:
      enabled: false
propagateProxyEnv: false
user:
  passwordlessSudo: false
""".lstrip()

    errors = ci_runner._effective_template_errors(output)

    assert any("plain must equal true" in error for error in errors)
    assert any("ssh.forwardAgent must equal false" in error for error in errors)
    assert any("mounts must be empty" in error for error in errors)


def test_preflight_is_read_only_when_lima_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    monkeypatch.setattr(ci_runner.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ci_runner.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        ci_runner.shutil,
        "which",
        lambda tool: None if tool == "limactl" else f"/usr/bin/{tool}",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: calls.append(list(arguments)),
    )

    assert ci_runner.preflight(config) == 2
    output = capsys.readouterr()
    assert "brew install lima" in output.out
    assert "missing required tools: limactl" in output.err
    assert calls == []


def test_preflight_validates_lima_effective_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    monkeypatch.setattr(ci_runner.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ci_runner.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(ci_runner.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(ci_runner, "_runner_group_errors", lambda config: [])
    calls: list[list[str]] = []
    effective = """
vmType: vz
vmOpts:
  vz:
    rosetta:
      enabled: false
arch: aarch64
ssh:
  forwardAgent: false
containerd:
  system: false
  user: false
propagateProxyEnv: false
plain: true
user:
  passwordlessSudo: false
""".lstrip()

    def fake_run(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        if arguments == ["limactl", "--version"]:
            return completed(arguments, stdout="limactl version 2.2.0")
        return completed(arguments, stdout=effective)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert ci_runner.preflight(config) == 0
    assert ["limactl", "validate", "--fill", str(config.lima_template)] in calls


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("limactl version 2.2.0", (2, 2, 0)),
        ("limactl 2.0.0", (2, 0, 0)),
        ("unexpected", None),
    ],
)
def test_lima_version_parser(
    output: str,
    expected: tuple[int, int, int] | None,
) -> None:
    assert ci_runner._lima_version(output) == expected


def test_lima_start_is_non_interactive(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    arguments = ci_runner._start_arguments(config, "alfred-ci-exact")

    assert arguments[:3] == ["limactl", "start", "--tty=false"]
    assert "--name=alfred-ci-exact" in arguments


def test_runner_group_policy_accepts_only_main_workflow_and_alfred_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    group = {
        "id": 4,
        "name": "mac-mini-disposable",
        "visibility": "selected",
        "allows_public_repositories": True,
        "restricted_to_workflows": True,
        "selected_workflows": [
            "luminik-io/alfred/.github/workflows/mac-mini-ci.yml@refs/heads/main"
        ],
    }

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[-2:] == ["--jq", "[.repositories[].full_name]"]:
            return completed(arguments, stdout='["luminik-io/alfred"]')
        return completed(arguments, stdout=json.dumps(group))

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert ci_runner._runner_group_errors(config) == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("visibility", "all", "visibility"),
        ("allows_public_repositories", False, "selected public"),
        ("restricted_to_workflows", False, "restricted"),
        ("selected_workflows", [], "allow only"),
    ],
)
def test_runner_group_policy_rejects_broader_group_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    group: dict[str, object] = {
        "id": 4,
        "name": "mac-mini-disposable",
        "visibility": "selected",
        "allows_public_repositories": True,
        "restricted_to_workflows": True,
        "selected_workflows": [
            "luminik-io/alfred/.github/workflows/mac-mini-ci.yml@refs/heads/main"
        ],
    }
    group[field] = value

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[-2:] == ["--jq", "[.repositories[].full_name]"]:
            return completed(arguments, stdout='["luminik-io/alfred"]')
        return completed(arguments, stdout=json.dumps(group))

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert any(message in error for error in ci_runner._runner_group_errors(config))


def test_runner_group_policy_rejects_extra_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    group = {
        "id": 4,
        "visibility": "selected",
        "allows_public_repositories": True,
        "restricted_to_workflows": True,
        "selected_workflows": [
            "luminik-io/alfred/.github/workflows/mac-mini-ci.yml@refs/heads/main"
        ],
    }

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[-2:] == ["--jq", "[.repositories[].full_name]"]:
            return completed(
                arguments,
                stdout='["luminik-io/alfred","luminik-io/other"]',
            )
        return completed(arguments, stdout=json.dumps(group))

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert ci_runner._runner_group_errors(config) == [
        "runner group must select only luminik-io/alfred"
    ]


def test_exclusive_lock_refuses_second_process(tmp_path: Path) -> None:
    ci_runner._ensure_private_directory(tmp_path)
    lock_path = tmp_path / "control.lock"

    with ci_runner.exclusive_control_lock(tmp_path):
        other = os.open(lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)


def test_exclusive_lock_refuses_symlink(tmp_path: Path) -> None:
    ci_runner._ensure_private_directory(tmp_path)
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")
    (tmp_path / "control.lock").symlink_to(target)

    with (
        pytest.raises(ci_runner.ControlPlaneError, match="cannot open runner lock"),
        ci_runner.exclusive_control_lock(tmp_path),
    ):
        pass


def test_private_state_directory_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    state = tmp_path / "state"
    state.symlink_to(target, target_is_directory=True)

    with pytest.raises(ci_runner.ControlPlaneError, match="must not be a symlink"):
        ci_runner._ensure_private_directory(state)


def test_serve_one_dry_run_never_calls_external_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda *args, **kwargs: pytest.fail("dry run called an external command"),
    )

    assert (
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=True,
            approve_registration=False,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "same-repository PR #123" in output
    assert "workflow" in output
    assert "--ephemeral" in output
    assert "registration token over stdin" in output
    assert "delete --force alfred-ci-exact" in output


def test_serve_one_requires_explicit_registration_approval(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="approve-registration"):
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=False,
            approve_registration=False,
        )


def test_registration_token_uses_organization_endpoint_and_is_not_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        return completed(arguments, stdout="short-lived-token\n")

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert ci_runner._registration_token(config) == "short-lived-token"
    assert calls == [
        [
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            "orgs/luminik-io/actions/runners/registration-token",
            "--jq",
            ".token",
        ]
    ]
    assert "short-lived-token" not in " ".join(calls[0])


def test_verified_pull_request_accepts_only_open_same_repository_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    response = (
        '{"state":"open","draft":false,"base_ref":"main",'
        '"base_repo":"luminik-io/alfred","head_repo":"luminik-io/alfred",'
        f'"head_sha":"{sha}"}}'
    )
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: completed(arguments, stdout=response),
    )

    assert ci_runner._verified_pull_request(config, 598) == ci_runner.PullRequestTarget(
        number=598,
        sha=sha,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("state", "closed", "open and ready"),
        ("draft", True, "open and ready"),
        ("base_ref", "release", "target main"),
        ("base_repo", "other/alfred", "base repository"),
        ("head_repo", "attacker/alfred", "fork pull requests"),
        ("head_sha", "short", "invalid head SHA"),
    ],
)
def test_verified_pull_request_rejects_unsafe_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    payload: dict[str, object] = {
        "state": "open",
        "draft": False,
        "base_ref": "main",
        "base_repo": "luminik-io/alfred",
        "head_repo": "luminik-io/alfred",
        "head_sha": "a" * 40,
    }
    payload[field] = value
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: completed(
            arguments,
            stdout=json.dumps(payload),
        ),
    )

    with pytest.raises(ci_runner.ControlPlaneError, match=message):
        ci_runner._verified_pull_request(config, 598)


def test_verified_pull_request_rejects_nonpositive_number(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="positive"):
        ci_runner._verified_pull_request(config, 0)


def test_dispatch_workflow_uses_trusted_main_exact_sha_and_one_use_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    target = ci_runner.PullRequestTarget(number=598, sha="a" * 40)
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(
        arguments: list[str],
        *,
        input_text: str | None = None,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((list(arguments), input_text))
        return completed(arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    ci_runner._dispatch_workflow(config, target, "alfred-job-abcd1234")

    assert len(calls) == 1
    arguments, payload = calls[0]
    assert "repos/luminik-io/alfred/actions/workflows/mac-mini-ci.yml/dispatches" in arguments
    assert payload is not None
    assert '"ref":"main"' in payload
    assert f'"sha":"{target.sha}"' in payload
    assert '"runner_label":"alfred-job-abcd1234"' in payload
    assert '"pr_number":"598"' in payload


def test_guest_lockdown_uses_guest_only_password_and_invalidates_sudo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: calls.append(list(arguments)),
    )

    ci_runner._lock_guest_privileges("alfred-ci-exact")

    assert len(calls) == 1
    command = calls[0][-1]
    assert calls[0][:3] == ["limactl", "shell", "alfred-ci-exact"]
    assert 'sudo -S -p \'\' passwd --lock "$(id -un)" <"$password_file"' in command
    assert "sudo -K" in command
    assert 'rm -f "$password_file"' in command
    assert command.startswith("#!/usr/bin/env bash\n")


@pytest.mark.parametrize("token", ["", "token with spaces", "x" * 513])
def test_registration_token_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: completed(arguments, stdout=token),
    )

    with pytest.raises(ci_runner.ControlPlaneError, match="invalid"):
        ci_runner._registration_token(config)


def test_serve_one_passes_token_only_via_guest_stdin_and_always_deletes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    state_directory = tmp_path / "state"
    calls: list[tuple[list[str], str | None]] = []
    deleted: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: state_directory)
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_new_job_label", lambda prefix: "alfred-job-exact")
    monkeypatch.setattr(
        ci_runner,
        "_verified_pull_request",
        lambda config, number: ci_runner.PullRequestTarget(number=number, sha="a" * 40),
    )
    monkeypatch.setattr(ci_runner, "_runner_group_errors", lambda config: [])
    dispatches: list[tuple[int, str]] = []
    monkeypatch.setattr(
        ci_runner,
        "_dispatch_workflow",
        lambda config, target, label: dispatches.append((target.number, label)),
    )
    monkeypatch.setattr(ci_runner, "_registration_token", lambda config: "private-token")
    monkeypatch.setattr(ci_runner, "_capture_diagnostics", lambda instance, state: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(ci_runner, "_delete_runner_registration", lambda config, instance: None)
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)

    def fake_run(
        arguments: list[str],
        *,
        input_text: str | None = None,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((list(arguments), input_text))
        return completed(arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert (
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=False,
            approve_registration=True,
        )
        == 0
    )
    assert deleted == ["alfred-ci-exact"]
    assert dispatches == [(123, "alfred-job-exact")]
    token_calls = [(arguments, stdin) for arguments, stdin in calls if stdin]
    assert len(token_calls) == 1
    assert token_calls[0][1] == "private-token\n"
    assert all("private-token" not in argument for argument in token_calls[0][0])
    assert "alfred-job-exact" in token_calls[0][0]
    assert "--ephemeral" not in token_calls[0][0]


def test_serve_one_deletes_guest_when_runner_command_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    deleted: list[str] = []
    call_count = 0

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_new_job_label", lambda prefix: "alfred-job-exact")
    monkeypatch.setattr(
        ci_runner,
        "_verified_pull_request",
        lambda config, number: ci_runner.PullRequestTarget(number=number, sha="a" * 40),
    )
    monkeypatch.setattr(ci_runner, "_runner_group_errors", lambda config: [])
    monkeypatch.setattr(ci_runner, "_dispatch_workflow", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_registration_token", lambda config: "private-token")
    monkeypatch.setattr(ci_runner, "_capture_diagnostics", lambda instance, state: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(ci_runner, "_delete_runner_registration", lambda config, instance: None)
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return completed(arguments)
        raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=False,
            approve_registration=True,
        )
    assert deleted == ["alfred-ci-exact"]


def test_serve_one_attempts_cleanup_when_guest_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    deleted: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_new_job_label", lambda prefix: "alfred-job-exact")
    monkeypatch.setattr(
        ci_runner,
        "_verified_pull_request",
        lambda config, number: ci_runner.PullRequestTarget(number=number, sha="a" * 40),
    )
    monkeypatch.setattr(ci_runner, "_capture_diagnostics", lambda instance, state: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(ci_runner, "_delete_runner_registration", lambda config, instance: None)
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, arguments)
        ),
    )

    with pytest.raises(subprocess.CalledProcessError):
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=False,
            approve_registration=True,
        )

    assert deleted == ["alfred-ci-exact"]


def test_serve_one_attempts_all_cleanup_when_diagnostic_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    deleted: list[str] = []
    runner_cleanup: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_new_job_label", lambda prefix: "alfred-job-exact")
    monkeypatch.setattr(
        ci_runner,
        "_verified_pull_request",
        lambda config, number: ci_runner.PullRequestTarget(number=number, sha="a" * 40),
    )
    monkeypatch.setattr(ci_runner, "_runner_group_errors", lambda config: [])
    monkeypatch.setattr(ci_runner, "_dispatch_workflow", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_registration_token", lambda config: "private-token")
    monkeypatch.setattr(ci_runner, "_lock_guest_privileges", lambda instance: None)
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(
        ci_runner,
        "_capture_diagnostics",
        lambda instance, state: (_ for _ in ()).throw(OSError("diagnostics failed")),
    )
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(
        ci_runner,
        "_delete_runner_registration",
        lambda config, instance: runner_cleanup.append(instance),
    )
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: completed(arguments),
    )

    with pytest.raises(OSError, match="diagnostics failed"):
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=False,
            approve_registration=True,
        )

    assert deleted == ["alfred-ci-exact"]
    assert runner_cleanup == ["alfred-ci-exact"]


@pytest.mark.parametrize(("job_returncode", "expected"), [(0, 2), (17, 17)])
def test_serve_one_preserves_job_result_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    job_returncode: int,
    expected: int,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    runner_cleanup: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_new_job_label", lambda prefix: "alfred-job-exact")
    monkeypatch.setattr(
        ci_runner,
        "_verified_pull_request",
        lambda config, number: ci_runner.PullRequestTarget(number=number, sha="a" * 40),
    )
    monkeypatch.setattr(ci_runner, "_runner_group_errors", lambda config: [])
    monkeypatch.setattr(ci_runner, "_dispatch_workflow", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_registration_token", lambda config: "private-token")
    monkeypatch.setattr(ci_runner, "_capture_diagnostics", lambda instance, state: None)
    monkeypatch.setattr(ci_runner, "_lock_guest_privileges", lambda instance: None)
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(
        ci_runner,
        "_delete_instance",
        lambda instance: (_ for _ in ()).throw(ci_runner.ControlPlaneError("VM remained")),
    )
    monkeypatch.setattr(
        ci_runner,
        "_delete_runner_registration",
        lambda config, instance: runner_cleanup.append(instance),
    )

    def fake_run(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return completed(
            arguments,
            returncode=job_returncode if "run" in arguments else 0,
        )

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert (
        ci_runner.serve_one(
            config,
            pull_request=123,
            dry_run=False,
            approve_registration=True,
        )
        == expected
    )
    assert runner_cleanup == ["alfred-ci-exact"]
    assert "cleanup incomplete" in capsys.readouterr().err


def test_runner_guest_script_pins_digest_and_ephemeral_mode() -> None:
    script = ci_runner._runner_guest_script()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "sha256sum --check --status" in script
    assert "--ephemeral" in script
    assert "--disableupdate" in script
    assert "--no-default-labels" in script
    assert '--runnergroup "$runner_group"' in script
    assert "IFS= read -r registration_token" in script
    assert '--token "$registration_token"' in script
    assert "timeout" in script
    assert '>"$HOME/.alfred-ci/runner-console.log"' in script


def test_runner_registration_cleanup_deletes_only_exact_named_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    runner_name = "alfred-ci-20260729120000-1234-abcd"
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        if "--jq" in arguments:
            return completed(
                arguments,
                stdout=f'[{{"id":77,"name":"{runner_name}"}}]',
            )
        return completed(arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    ci_runner._delete_runner_registration(config, runner_name)

    assert len(calls) == 2
    assert f"name={runner_name}" in calls[0]
    assert "orgs/luminik-io/actions/runners/77" in calls[1]
    assert "DELETE" in calls[1]


def test_instance_cleanup_is_noop_when_exact_vm_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        return completed(arguments, stdout="other-instance\n")

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    ci_runner._delete_instance("alfred-ci-exact")

    assert calls == [["limactl", "list", "--format", "{{.Name}}"]]


def test_instance_cleanup_deletes_only_exact_present_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        if "list" in arguments:
            return completed(arguments, stdout="alfred-ci-exact\n")
        return completed(arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    ci_runner._delete_instance("alfred-ci-exact")

    assert calls == [
        ["limactl", "list", "--format", "{{.Name}}"],
        ["limactl", "delete", "--force", "alfred-ci-exact"],
    ]


@pytest.mark.parametrize("failure_step", ["stop", "retry"])
def test_instance_cleanup_normalizes_force_delete_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    delete_calls = 0

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal delete_calls
        if "list" in arguments:
            return completed(arguments, stdout="alfred-ci-exact\n")
        if "delete" in arguments:
            delete_calls += 1
            if delete_calls == 1:
                return completed(arguments, returncode=1)
            if failure_step == "retry":
                raise subprocess.TimeoutExpired(arguments, timeout=1)
        if "stop" in arguments and failure_step == "stop":
            raise OSError("stop failed")
        return completed(arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    with pytest.raises(ci_runner.ControlPlaneError, match="cannot force-delete"):
        ci_runner._delete_instance("alfred-ci-exact")


def test_runner_diagnostics_archive_is_bounded_and_extractable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest_home = tmp_path / "guest"
    source = guest_home / "actions-runner" / "_diag"
    source.mkdir(parents=True)
    (source / "Runner.log").write_text("safe diagnostic\n", encoding="utf-8")
    state = tmp_path / "state"
    original_run = subprocess.run

    def execute_guest_script(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        script = arguments[-1]
        return original_run(
            ["python3", "-c", script],
            check=False,
            capture_output=True,
            timeout=30,
            env={**os.environ, "HOME": str(guest_home)},
        )

    monkeypatch.setattr(ci_runner.subprocess, "run", execute_guest_script)

    archive = ci_runner._capture_diagnostics("alfred-ci-exact", state)

    assert archive is not None
    assert archive.stat().st_size <= ci_runner.MAX_DIAGNOSTIC_BYTES
    with tarfile.open(archive, "r:gz") as captured:
        member = captured.extractfile("_diag/Runner.log")
        assert member is not None
        assert member.read() == b"safe diagnostic\n"


def test_runner_registration_cleanup_is_noop_after_ephemeral_deregistration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        return completed(arguments, stdout="[]")

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    ci_runner._delete_runner_registration(
        config,
        "alfred-ci-20260729120000-1234-abcd",
    )

    assert len(calls) == 1


def test_runner_registration_cleanup_rejects_unsafe_name(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="unsafe"):
        ci_runner._delete_runner_registration(config, 'alfred-ci-";del(.)')


@pytest.mark.parametrize(
    "sha",
    [
        "abc123",
        "A" * 40,
        "g" * 40,
        "a" * 39,
        "a" * 41,
        "main",
    ],
)
def test_verified_commit_rejects_non_exact_shas(
    tmp_path: Path,
    sha: str,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="exactly 40"):
        ci_runner._verified_commit(config, sha)


def test_verified_commit_requires_github_to_return_same_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    requested = "a" * 40
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: completed(arguments, stdout=f"{'b' * 40}\n"),
    )

    with pytest.raises(ci_runner.ControlPlaneError, match="exact commit"):
        ci_runner._verified_commit(config, requested)


def test_fallback_guest_script_fetches_and_checks_exact_sha(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40

    script = ci_runner._fallback_guest_script(config, sha)

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "git -c protocol.version=2 fetch" in script
    assert "--depth=1" in script
    assert f"expected_sha={sha}" in script
    assert 'if [[ "$actual_sha" != "$expected_sha" ]]' in script
    assert "python3 -m pytest tests/ -q" in script
    assert "GITHUB_TOKEN" not in script
    assert 'exec >"$HOME/.alfred-ci/fallback-console.log" 2>&1' in script
    assert "uv-aarch64-unknown-linux-gnu.tar.gz" in script
    assert "sha256sum --check --status" in script
    assert 'test "$(uv --version | awk \'{print $2}\')" = "$uv_version"' in script


def test_fallback_guest_script_logs_each_command_on_its_own_line(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    script = ci_runner._fallback_guest_script(config, "a" * 40)
    function_start = script.index("run_check() {")
    function_end = script.index("\n}\n", function_start) + 3
    run_check = script[function_start:function_end]

    completed_process = subprocess.run(
        ["bash", "-c", f"set -Eeuo pipefail\n{run_check}\nrun_check printf sample"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed_process.stdout == "==> printf sample \nsample"


def test_fallback_dry_run_never_calls_external_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda *args, **kwargs: pytest.fail("dry run called an external command"),
    )

    assert (
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=True,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert f"commits/{sha}" in output
    assert "[guest" in output
    assert "Hermes / Local CI" in output


def test_fallback_publishes_pending_and_success_only_from_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    statuses: list[tuple[str, str]] = []
    deleted: list[str] = []
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_capture_guest_file", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(
        ci_runner,
        "_publish_status",
        lambda config, sha, state, description: statuses.append((state, description)),
    )

    def fake_run(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((list(arguments), dict(kwargs)))
        return completed(arguments)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert (
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=False,
        )
        == 0
    )
    assert [state for state, _ in statuses] == ["pending", "success"]
    assert deleted == ["alfred-ci-exact"]
    fallback_calls = [call for call in calls if "fallback.sh" in " ".join(call[0])]
    assert len(fallback_calls) == 1
    assert "timeout --signal=TERM --kill-after=30s" in " ".join(fallback_calls[0][0])
    assert fallback_calls[0][1]["timeout"] == (90 * 60) + 60


def test_fallback_publishes_failure_for_nonzero_guest_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    statuses: list[str] = []
    calls = 0

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_capture_guest_file", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: None)
    monkeypatch.setattr(
        ci_runner,
        "_publish_status",
        lambda config, sha, state, description: statuses.append(state),
    )

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return completed(arguments, returncode=0 if calls == 1 else 17)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert (
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=False,
        )
        == 17
    )
    assert statuses == ["pending", "failure"]


@pytest.mark.parametrize(
    (
        "guest_returncode",
        "cleanup_error",
        "expected_returncode",
        "expected_final_status",
    ),
    [
        (0, ci_runner.ControlPlaneError("delete failed"), 2, "error"),
        (2, ci_runner.ControlPlaneError("delete failed"), 2, "failure"),
        (17, ci_runner.ControlPlaneError("delete failed"), 17, "failure"),
        (17, OSError("delete failed"), 17, "failure"),
        (17, subprocess.TimeoutExpired(["limactl"], timeout=1), 17, "failure"),
    ],
)
def test_fallback_preserves_guest_result_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    guest_returncode: int,
    cleanup_error: BaseException,
    expected_returncode: int,
    expected_final_status: str,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    statuses: list[tuple[str, str]] = []
    calls = 0

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_capture_guest_file", lambda *args: None)
    monkeypatch.setattr(
        ci_runner,
        "_delete_instance",
        lambda instance: (_ for _ in ()).throw(cleanup_error),
    )
    monkeypatch.setattr(
        ci_runner,
        "_publish_status",
        lambda config, sha, state, description: statuses.append((state, description)),
    )

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return completed(arguments, returncode=0 if calls == 1 else guest_returncode)

    monkeypatch.setattr(ci_runner, "_run", fake_run)

    assert (
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=False,
        )
        == expected_returncode
    )
    assert [state for state, _ in statuses] == ["pending", expected_final_status]
    assert "cleanup incomplete for alfred-ci-exact" in capsys.readouterr().err


def test_fallback_deletes_instance_when_diagnostic_directory_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    statuses: list[str] = []
    deleted: list[str] = []
    calls = 0
    original_ensure_private_directory = ci_runner._ensure_private_directory

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(
        ci_runner,
        "_publish_status",
        lambda config, sha, state, description: statuses.append(state),
    )

    def ensure_private_directory(path: Path) -> None:
        if path.name == "diagnostics":
            raise OSError("diagnostic directory failed")
        original_ensure_private_directory(path)

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return completed(arguments, returncode=0 if calls == 1 else 17)

    monkeypatch.setattr(ci_runner, "_ensure_private_directory", ensure_private_directory)
    monkeypatch.setattr(ci_runner, "_run", fake_run)

    with pytest.raises(OSError, match="diagnostic directory failed"):
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=False,
        )

    assert deleted == ["alfred-ci-exact"]
    assert statuses == ["pending", "error"]


def test_fallback_publishes_error_when_operator_interrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    statuses: list[str] = []
    deleted: list[str] = []
    captured: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(
        ci_runner,
        "_capture_guest_file",
        lambda instance, guest, local: captured.append(guest),
    )
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(
        ci_runner,
        "_publish_status",
        lambda config, sha, state, description: statuses.append(state),
    )

    def interrupted_run(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["limactl", "start"]:
            return completed(arguments)
        raise KeyboardInterrupt

    monkeypatch.setattr(ci_runner, "_run", interrupted_run)

    with pytest.raises(KeyboardInterrupt):
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=False,
        )

    assert statuses == ["pending", "error"]
    assert deleted == ["alfred-ci-exact"]
    assert captured == [".alfred-ci/fallback-console.log"]


def test_fallback_cleanup_failure_does_not_mask_operator_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    statuses: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_capture_guest_file", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_install_guest_helper", lambda *args: None)
    monkeypatch.setattr(
        ci_runner,
        "_delete_instance",
        lambda instance: (_ for _ in ()).throw(ci_runner.ControlPlaneError("delete failed")),
    )
    monkeypatch.setattr(
        ci_runner,
        "_publish_status",
        lambda config, sha, state, description: statuses.append(state),
    )

    def interrupted_run(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["limactl", "start"]:
            return completed(arguments)
        raise KeyboardInterrupt

    monkeypatch.setattr(ci_runner, "_run", interrupted_run)

    with pytest.raises(KeyboardInterrupt):
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=True,
            dry_run=False,
        )

    assert statuses == ["pending", "error"]
    assert "cleanup incomplete for alfred-ci-exact" in capsys.readouterr().err


def test_fallback_attempts_cleanup_when_guest_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    deleted: list[str] = []

    monkeypatch.setattr(ci_runner, "preflight", lambda config, **kwargs: 0)
    monkeypatch.setattr(ci_runner, "_verified_commit", lambda config, value: value)
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_new_instance_name", lambda prefix: f"{prefix}-exact")
    monkeypatch.setattr(ci_runner, "_capture_guest_file", lambda *args: None)
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda instance: deleted.append(instance))
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, arguments)
        ),
    )

    with pytest.raises(subprocess.CalledProcessError):
        ci_runner.fallback(
            config,
            sha=sha,
            publish_status=False,
            dry_run=False,
        )

    assert deleted == ["alfred-ci-exact"]


def test_publish_status_uses_fixed_repository_context_and_host_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    sha = "a" * 40
    calls: list[list[str]] = []
    monkeypatch.setattr(
        ci_runner,
        "_run",
        lambda arguments, **kwargs: calls.append(list(arguments)),
    )

    ci_runner._publish_status(config, sha, "success", "checks passed")

    assert len(calls) == 1
    assert f"repos/luminik-io/alfred/statuses/{sha}" in calls[0]
    assert "state=success" in calls[0]
    assert "context=Hermes / Local CI" in calls[0]
    assert "description=checks passed" in calls[0]


def test_publish_status_rejects_unknown_state(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="unsupported"):
        ci_runner._publish_status(config, "a" * 40, "neutral", "not allowed")


@pytest.mark.parametrize(
    "instance",
    [
        "alfred-ci",
        "other-ci-20260729",
        "alfred-ci-../../victim",
        "ALFRED-CI-unsafe",
        "alfred-ci-",
    ],
)
def test_cleanup_rejects_nonprefixed_or_unsafe_targets(
    tmp_path: Path,
    instance: str,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="cleanup target"):
        ci_runner.cleanup(config, instance=instance, approve_delete=True)


def test_cleanup_requires_explicit_delete_approval(tmp_path: Path) -> None:
    config = ci_runner.load_config(write_config(tmp_path))

    with pytest.raises(ci_runner.ControlPlaneError, match="approve-delete"):
        ci_runner.cleanup(
            config,
            instance="alfred-ci-20260729120000-1234-abcd",
            approve_delete=False,
        )


def test_cleanup_deletes_one_exact_prefixed_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ci_runner.load_config(write_config(tmp_path))
    instance = "alfred-ci-20260729120000-1234-abcd"
    deleted: list[str] = []
    monkeypatch.setattr(ci_runner.shutil, "which", lambda tool: "/opt/homebrew/bin/limactl")
    monkeypatch.setattr(ci_runner, "_state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(ci_runner, "_delete_instance", lambda value: deleted.append(value))
    monkeypatch.setattr(ci_runner, "_delete_runner_registration", lambda config, value: None)

    assert ci_runner.cleanup(config, instance=instance, approve_delete=True) == 0
    assert deleted == [instance]


def test_workflow_runs_only_by_trusted_dispatch() -> None:
    workflow = REPOSITORY_ROOT / ".github" / "workflows" / "mac-mini-ci.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in content
    assert "pull_request:" not in content
    assert "pull_request_target" not in content
    assert "ref: ${{ inputs.sha }}" in content
    assert 'labels: "${{ inputs.runner_label }}"' in content
    assert "self-hosted" not in content
    assert "luminik-disposable" not in content
    assert "group: mac-mini-disposable" in content
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' in content
    assert "permissions:\n  contents: read" in content
    assert "persist-credentials: false" in content
    assert 'version: "0.12.0"' in content
    assert "pytest==9.1.1" in content
    assert "fastapi==0.140.13" in content
    assert "bash bin/alfred-ci-shellcheck.sh" in content
    assert "secrets:" not in content
    assert "environment:" not in content
    assert "deploy" not in content.lower()


def test_lima_template_has_plain_mode_and_no_host_exposure() -> None:
    template = REPOSITORY_ROOT / "examples" / "ci-runner" / "lima.yaml"
    content = template.read_text(encoding="utf-8")

    assert "plain: true" in content
    assert "mounts: []" in content
    assert "portForwards: []" in content
    assert "forwardAgent: false" in content
    assert "propagateProxyEnv: false" in content
    assert "system: false" in content
    assert "user: false" in content
    assert "enabled: false" in content
    assert "cpus: 4" in content
    assert 'memory: "6GiB"' in content
    assert 'disk: "40GiB"' in content
    assert "sha256:2eaec7286c49fdea" in content
    assert "passwordlessSudo: false" in content
    assert '"${resolver}/32"' in content
    assert '"ff00::/8"' in content
    assert '-d "$cidr" -p udp --dport 53 -j ACCEPT' not in content


def test_shellcheck_scanner_includes_all_embedded_guest_scripts() -> None:
    scanner = REPOSITORY_ROOT / "bin" / "alfred-ci-shellcheck.sh"
    content = scanner.read_text(encoding="utf-8")

    assert "_guest_privilege_lock_script" in content
    assert "_runner_guest_script" in content
    assert "_fallback_guest_script" in content
    assert "lima_provision_scripts" in content
    assert "shellcheck -S warning" in content
