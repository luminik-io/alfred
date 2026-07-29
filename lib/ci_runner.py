"""Disposable Lima control plane for Alfred CI.

The macOS host is trusted. Repository code runs only inside a new plain-mode
Linux guest. GitHub credentials stay on the host and every mutating operation
requires an explicit CLI flag.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import textwrap
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path

GITHUB_API_VERSION = "2022-11-28"
MAX_CPUS = 4
MAX_MEMORY_GIB = 6
MAX_DISK_GIB = 40
MAX_JOB_MINUTES = 90
MAX_DIAGNOSTIC_BYTES = 16 * 1024 * 1024
MINIMUM_LIMA_VERSION = (2, 0, 0)
STATE_DIRECTORY = Path.home() / ".local" / "state" / "alfred-ci-runner"

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SAFE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})$")
SAFE_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,62})$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ConfigurationError(ValueError):
    """Raised when the trusted runner configuration fails closed."""


class ControlPlaneError(RuntimeError):
    """Raised when a host or guest control operation fails."""


@dataclasses.dataclass(frozen=True)
class FallbackConfig:
    context: str
    commands: tuple[tuple[str, ...], ...]


@dataclasses.dataclass(frozen=True)
class PullRequestTarget:
    number: int
    sha: str


@dataclasses.dataclass(frozen=True)
class RunnerConfig:
    config_path: Path
    organization: str
    repository: str
    runner_group: str
    instance_prefix: str
    job_label_prefix: str
    cpus: int
    memory_gib: int
    disk_gib: int
    job_timeout_minutes: int
    runner_version: str
    runner_sha256: str
    uv_version: str
    uv_sha256: str
    lima_template: Path
    fallback: FallbackConfig

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.repository}"

    @property
    def organization_url(self) -> str:
        return f"https://github.com/{self.organization}"

    @property
    def clone_url(self) -> str:
        return f"{self.repository_url}.git"


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{field} must be a TOML table")
    if not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{field} contains a non-string TOML key")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _string(table: dict[str, object], field: str) -> str:
    value = table.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(table: dict[str, object], field: str) -> int:
    value = table.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field} must be an integer")
    return value


def load_config(path: Path) -> RunnerConfig:
    """Load and strictly validate the trusted TOML configuration."""

    resolved_path = path.expanduser().resolve()
    try:
        with resolved_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load {resolved_path}: {exc}") from exc

    runner = _mapping(raw.get("runner"), "runner")
    fallback = _mapping(raw.get("fallback"), "fallback")

    organization = _string(runner, "organization")
    repository = _string(runner, "repository")
    if not re.fullmatch(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})$", organization):
        raise ConfigurationError("organization must be a GitHub owner name")
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ConfigurationError("repository must use the owner/name form")
    if not repository.startswith(f"{organization}/"):
        raise ConfigurationError("repository must belong to the configured organization")

    runner_group = _string(runner, "runner_group")
    if not SAFE_NAME_PATTERN.fullmatch(runner_group) or len(runner_group) > 64:
        raise ConfigurationError(
            "runner_group must be 1 to 64 lowercase letters, digits, or hyphens"
        )

    instance_prefix = _string(runner, "instance_prefix")
    if not SAFE_NAME_PATTERN.fullmatch(instance_prefix) or len(instance_prefix) > 32:
        raise ConfigurationError(
            "instance_prefix must be 1 to 32 lowercase letters, digits, or hyphens"
        )

    job_label_prefix = _string(runner, "job_label_prefix")
    if not SAFE_NAME_PATTERN.fullmatch(job_label_prefix) or len(job_label_prefix) > 32:
        raise ConfigurationError(
            "job_label_prefix must be 1 to 32 lowercase letters, digits, or hyphens"
        )

    cpus = _integer(runner, "cpus")
    memory_gib = _integer(runner, "memory_gib")
    disk_gib = _integer(runner, "disk_gib")
    job_timeout_minutes = _integer(runner, "job_timeout_minutes")
    if not 1 <= cpus <= MAX_CPUS:
        raise ConfigurationError(f"cpus must be between 1 and {MAX_CPUS}")
    if not 2 <= memory_gib <= MAX_MEMORY_GIB:
        raise ConfigurationError(f"memory_gib must be between 2 and {MAX_MEMORY_GIB}")
    if not 10 <= disk_gib <= MAX_DISK_GIB:
        raise ConfigurationError(f"disk_gib must be between 10 and {MAX_DISK_GIB}")
    if not 1 <= job_timeout_minutes <= MAX_JOB_MINUTES:
        raise ConfigurationError(f"job_timeout_minutes must be between 1 and {MAX_JOB_MINUTES}")

    runner_version = _string(runner, "runner_version")
    runner_sha256 = _string(runner, "runner_sha256").lower()
    uv_version = _string(runner, "uv_version")
    uv_sha256 = _string(runner, "uv_sha256").lower()
    if not VERSION_PATTERN.fullmatch(runner_version):
        raise ConfigurationError("runner_version must use the x.y.z form")
    if not DIGEST_PATTERN.fullmatch(runner_sha256):
        raise ConfigurationError("runner_sha256 must be a lowercase SHA-256 digest")
    if not VERSION_PATTERN.fullmatch(uv_version):
        raise ConfigurationError("uv_version must use the x.y.z form")
    if not DIGEST_PATTERN.fullmatch(uv_sha256):
        raise ConfigurationError("uv_sha256 must be a lowercase SHA-256 digest")

    lima_template_value = _string(runner, "lima_template")
    lima_template = (resolved_path.parent / lima_template_value).resolve()
    if not lima_template.is_file():
        raise ConfigurationError(f"Lima template does not exist: {lima_template}")

    context = _string(fallback, "context")
    if len(context) > 100 or any(character in context for character in "\r\n"):
        raise ConfigurationError("fallback context must be one line and at most 100 characters")

    commands_value = fallback.get("commands")
    if not isinstance(commands_value, list) or not commands_value:
        raise ConfigurationError("fallback commands must be a non-empty array")
    commands: list[tuple[str, ...]] = []
    for index, command_value in enumerate(commands_value):
        if not isinstance(command_value, list) or not command_value:
            raise ConfigurationError(f"fallback command {index} must be a non-empty array")
        command: list[str] = []
        for argument in command_value:
            if (
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                or "\n" in argument
                or "\r" in argument
            ):
                raise ConfigurationError(f"fallback command {index} contains an unsafe argument")
            command.append(argument)
        commands.append(tuple(command))

    return RunnerConfig(
        config_path=resolved_path,
        organization=organization,
        repository=repository,
        runner_group=runner_group,
        instance_prefix=instance_prefix,
        job_label_prefix=job_label_prefix,
        cpus=cpus,
        memory_gib=memory_gib,
        disk_gib=disk_gib,
        job_timeout_minutes=job_timeout_minutes,
        runner_version=runner_version,
        runner_sha256=runner_sha256,
        uv_version=uv_version,
        uv_sha256=uv_sha256,
        lima_template=lima_template,
        fallback=FallbackConfig(context=context, commands=tuple(commands)),
    )


def _run(
    arguments: Sequence[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        check=check,
        capture_output=capture_output,
        input=input_text,
        text=True,
        timeout=timeout,
    )


def _print_command(arguments: Sequence[str]) -> None:
    print(f"+ {shlex.join(arguments)}")


def _effective_yaml_settings(output: str) -> dict[tuple[str, ...], str | None]:
    """Parse scalar paths from Lima's canonical `validate --fill` output."""

    settings: dict[tuple[str, ...], str | None] = {}
    parents: list[tuple[int, str]] = []
    pattern = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9]*):(?:\s+(.*))?$")
    for line in output.splitlines():
        match = pattern.fullmatch(line)
        if match is None:
            continue
        indentation = len(match.group(1))
        while parents and parents[-1][0] >= indentation:
            parents.pop()
        key = match.group(2)
        value = match.group(3)
        path = (*(parent_key for _, parent_key in parents), key)
        settings[path] = value
        if value is None:
            parents.append((indentation, key))
    return settings


def _effective_template_errors(output: str) -> list[str]:
    settings = _effective_yaml_settings(output)
    required = {
        ("plain",): "true",
        ("vmType",): "vz",
        ("arch",): "aarch64",
        ("ssh", "forwardAgent"): "false",
        ("user", "passwordlessSudo"): "false",
        ("containerd", "system"): "false",
        ("containerd", "user"): "false",
        ("vmOpts", "vz", "rosetta", "enabled"): "false",
        ("propagateProxyEnv",): "false",
    }
    errors = [
        f"Lima effective setting {'.'.join(path)} must equal {expected}"
        for path, expected in required.items()
        if settings.get(path) != expected
    ]
    for path in (("mounts",), ("portForwards",)):
        if path in settings and settings[path] != "[]":
            errors.append(f"Lima effective setting {path[0]} must be empty")
    return errors


def _lima_version(version_output: str) -> tuple[int, int, int] | None:
    match = re.search(r"\b([0-9]+)\.([0-9]+)\.([0-9]+)\b", version_output)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _runner_group_errors(config: RunnerConfig) -> list[str]:
    expected_workflow = f"{config.repository}/.github/workflows/mac-mini-ci.yml@refs/heads/main"
    try:
        group_result = _run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
                f"orgs/{config.organization}/actions/runner-groups",
                "--jq",
                f'.runner_groups[] | select(.name == "{config.runner_group}")',
            ],
            capture_output=True,
        )
        if not group_result.stdout.strip():
            return [f"runner group does not exist: {config.runner_group}"]
        group = json.loads(group_result.stdout)
        if not isinstance(group, dict) or isinstance(group.get("id"), bool):
            return ["GitHub returned invalid runner-group metadata"]
        group_id = group.get("id")
        if not isinstance(group_id, int) or group_id < 1:
            return ["GitHub returned invalid runner-group metadata"]
        repository_result = _run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
                (f"orgs/{config.organization}/actions/runner-groups/{group_id}/repositories"),
                "--jq",
                "[.repositories[].full_name]",
            ],
            capture_output=True,
        )
        repositories = json.loads(repository_result.stdout)
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError) as exc:
        return [f"cannot verify runner-group policy: {exc}"]

    errors: list[str] = []
    if group.get("visibility") != "selected":
        errors.append("runner group visibility must be selected")
    if group.get("allows_public_repositories") is not True:
        errors.append("runner group must explicitly allow its selected public repository")
    if group.get("restricted_to_workflows") is not True:
        errors.append("runner group must be restricted to trusted workflows")
    selected_workflows = group.get("selected_workflows")
    if selected_workflows != [expected_workflow]:
        errors.append(f"runner group must allow only {expected_workflow}")
    if repositories != [config.repository]:
        errors.append(f"runner group must select only {config.repository}")
    return errors


def preflight(config: RunnerConfig, *, require_runner_group: bool = True) -> int:
    """Run read-only host and configuration checks."""

    errors: list[str] = []
    if platform.system() != "Darwin":
        errors.append("the supported control host is macOS")
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        errors.append("the supplied guest image and runner are ARM64-only")

    required_tools = ("git", "gh", "python3", "limactl")
    missing = [tool for tool in required_tools if shutil.which(tool) is None]
    if missing:
        errors.append(f"missing required tools: {', '.join(missing)}")

    print(f"repository: {config.repository}")
    print(
        "resource cap: "
        f"{config.cpus} CPU, {config.memory_gib} GiB RAM, "
        f"{config.disk_gib} GiB disk, {config.job_timeout_minutes} minutes"
    )
    print(f"Lima template: {config.lima_template}")
    print("workflow dispatch target: trusted main")
    print("GitHub auth scopes inspected or changed: no")

    if "limactl" in missing:
        print("Lima is not installed. Manual preflight only:")
        print("  brew info lima")
        print("  brew install lima")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    version_output = _run(["limactl", "--version"], capture_output=True).stdout.strip()
    version = _lima_version(version_output)
    if version is None or version < MINIMUM_LIMA_VERSION:
        print(
            f"ERROR: Lima 2.0.0 or newer is required, got {version_output!r}",
            file=sys.stderr,
        )
        return 2
    try:
        validated = _run(
            ["limactl", "validate", "--fill", str(config.lima_template)],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: Lima template validation failed: {exc}", file=sys.stderr)
        return 2
    effective_errors = _effective_template_errors(validated.stdout)
    if effective_errors:
        for error in effective_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if require_runner_group:
        group_errors = _runner_group_errors(config)
        if group_errors:
            for error in group_errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 2
    print(f"Lima: {version_output}")
    if require_runner_group:
        print(f"runner group: {config.organization}/{config.runner_group}")
    else:
        print("runner group: not required for isolated fallback")
    print("preflight passed")
    return 0


def _state_directory() -> Path:
    return STATE_DIRECTORY


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise ControlPlaneError(f"cannot create private state directory: {path}") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ControlPlaneError(f"state directory must not be a symlink: {path}") from exc
    try:
        os.fchmod(descriptor, 0o700)
        mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
        if mode != 0o700:
            raise ControlPlaneError(f"state directory must have mode 0700: {path}")
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def exclusive_control_lock(state_directory: Path) -> Iterator[None]:
    """Hold one non-blocking process lock for the full VM lifetime."""

    _ensure_private_directory(state_directory)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(state_directory / "control.lock", flags, 0o600)
    except OSError as exc:
        raise ControlPlaneError(f"cannot open runner lock: {exc}") from exc

    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlPlaneError("another Alfred CI guest is already active") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        os.fsync(descriptor)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _new_instance_name(prefix: str) -> str:
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(2)
    value = f"{prefix}-{timestamp}-{os.getpid()}-{suffix}"
    if len(value) > 63 or not SAFE_NAME_PATTERN.fullmatch(value):
        raise ControlPlaneError("generated Lima instance name is invalid")
    return value


def _start_arguments(config: RunnerConfig, instance: str) -> list[str]:
    return [
        "limactl",
        "start",
        "--tty=false",
        f"--name={instance}",
        f"--cpus={config.cpus}",
        f"--memory={config.memory_gib}",
        f"--disk={config.disk_gib}",
        str(config.lima_template),
    ]


def _install_guest_helper(instance: str, name: str, content: str) -> None:
    command = (
        'set -euo pipefail; umask 077; mkdir -p "$HOME/.alfred-ci"; '
        f'cat > "$HOME/.alfred-ci/{name}"; chmod 0700 "$HOME/.alfred-ci/{name}"'
    )
    _run(["limactl", "shell", instance, "bash", "-c", command], input_text=content)


def _lock_guest_privileges(instance: str) -> None:
    """Discard Lima's bootstrap password before repository code enters the guest."""

    command = textwrap.dedent(
        """\
        set -Eeuo pipefail
        password_file="$HOME/password"
        test -s "$password_file"
        sudo -S -p '' passwd --lock "$(id -un)" <"$password_file"
        sudo -K
        rm -f "$password_file"
        if sudo -n true 2>/dev/null; then
          echo "guest sudo lockdown failed" >&2
          exit 70
        fi
        """
    )
    _run(["limactl", "shell", instance, "bash", "-c", command])


def _registration_token(config: RunnerConfig) -> str:
    completed = _run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"orgs/{config.organization}/actions/runners/registration-token",
            "--jq",
            ".token",
        ],
        capture_output=True,
    )
    token = completed.stdout.strip()
    if not token or len(token) > 512 or any(character.isspace() for character in token):
        raise ControlPlaneError("GitHub returned an invalid runner registration token")
    return token


def _verified_pull_request(config: RunnerConfig, number: int) -> PullRequestTarget:
    if number < 1:
        raise ControlPlaneError("pull-request number must be positive")
    completed = _run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{config.repository}/pulls/{number}",
            "--jq",
            (
                "{state,draft,base_ref:.base.ref,"
                "base_repo:.base.repo.full_name,"
                "head_repo:.head.repo.full_name,head_sha:.head.sha}"
            ),
        ],
        capture_output=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ControlPlaneError("GitHub returned invalid pull-request metadata") from exc
    if not isinstance(payload, dict):
        raise ControlPlaneError("GitHub returned invalid pull-request metadata")
    if payload.get("state") != "open" or payload.get("draft") is not False:
        raise ControlPlaneError("pull request must be open and ready for review")
    if payload.get("base_ref") != "main":
        raise ControlPlaneError("pull request must target main")
    if payload.get("base_repo") != config.repository:
        raise ControlPlaneError("pull-request base repository is not allowlisted")
    if payload.get("head_repo") != config.repository:
        raise ControlPlaneError("fork pull requests may not use this runner")
    sha = payload.get("head_sha")
    if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
        raise ControlPlaneError("pull request has an invalid head SHA")
    return PullRequestTarget(number=number, sha=sha)


def _dispatch_workflow(
    config: RunnerConfig,
    target: PullRequestTarget,
    job_label: str,
) -> None:
    if not SAFE_LABEL_PATTERN.fullmatch(job_label):
        raise ControlPlaneError("generated job label is unsafe")
    payload = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "sha": target.sha,
                "runner_label": job_label,
                "pr_number": str(target.number),
            },
        },
        separators=(",", ":"),
    )
    _run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{config.repository}/actions/workflows/mac-mini-ci.yml/dispatches",
            "--input",
            "-",
        ],
        input_text=payload,
    )


def _new_job_label(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(12)}"


def _runner_guest_script() -> str:
    return textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -Eeuo pipefail
        umask 077

        mode=$1
        shift
        runner_home="$HOME/actions-runner"

        case "$mode" in
          configure)
            repository_url=$1
            runner_group=$2
            runner_name=$3
            labels=$4
            runner_version=$5
            expected_sha256=$6

            IFS= read -r registration_token
            if [[ -z "$registration_token" || ${#registration_token} -gt 512 ]]; then
              echo "invalid registration token" >&2
              exit 64
            fi

            archive="$HOME/actions-runner.tar.gz"
            mkdir -p "$runner_home"
            curl --fail --location --proto '=https' --tlsv1.2 \
              --output "$archive" \
              "https://github.com/actions/runner/releases/download/v${runner_version}/actions-runner-linux-arm64-${runner_version}.tar.gz"
            printf '%s  %s\n' "$expected_sha256" "$archive" | sha256sum --check --status
            tar --extract --gzip --file "$archive" --directory "$runner_home"
            rm -f "$archive"

            cd "$runner_home"
            ./config.sh \
              --unattended \
              --ephemeral \
              --disableupdate \
              --no-default-labels \
              --url "$repository_url" \
              --runnergroup "$runner_group" \
              --token "$registration_token" \
              --name "$runner_name" \
              --labels "$labels" \
              --work _work
            unset registration_token
            ;;
          run)
            timeout_seconds=$1
            cd "$runner_home"
            timeout \
              --signal=TERM \
              --kill-after=30s \
              "${timeout_seconds}s" \
              ./run.sh \
              >"$HOME/.alfred-ci/runner-console.log" \
              2>&1
            ;;
          *)
            echo "unsupported runner helper mode" >&2
            exit 64
            ;;
        esac
        """
    )


def _capture_diagnostics(instance: str, state_directory: Path) -> Path | None:
    diagnostics = state_directory / "diagnostics"
    _ensure_private_directory(diagnostics)
    archive = diagnostics / f"{instance}.tar.gz"
    source_limit = MAX_DIAGNOSTIC_BYTES // 2
    script = textwrap.dedent(
        f"""\
        import io
        import pathlib
        import sys
        import tarfile

        source = pathlib.Path.home() / "actions-runner" / "_diag"
        remaining = {source_limit}
        files = 0
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            if source.is_dir():
                for path in sorted(source.rglob("*")):
                    if files >= 128 or remaining <= 0 or not path.is_file():
                        continue
                    try:
                        with path.open("rb") as handle:
                            data = handle.read(remaining)
                    except OSError:
                        continue
                    info = tarfile.TarInfo(str(path.relative_to(source.parent)))
                    info.size = len(data)
                    info.mode = 0o600
                    archive.addfile(info, io.BytesIO(data))
                    remaining -= len(data)
                    files += 1
        """
    )
    try:
        completed = subprocess.run(
            ["limactl", "shell", instance, "python3", "-c", script],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    if len(completed.stdout) > MAX_DIAGNOSTIC_BYTES:
        return None

    return _write_diagnostic(archive, completed.stdout)


def _capture_guest_file(instance: str, guest_path: str, local_path: Path) -> Path | None:
    if (
        not re.fullmatch(r"[A-Za-z0-9._/-]+", guest_path)
        or guest_path.startswith("/")
        or ".." in Path(guest_path).parts
    ):
        raise ControlPlaneError("diagnostic guest path is unsafe")
    command = f'head -c {MAX_DIAGNOSTIC_BYTES} "$HOME/{guest_path}"'
    try:
        completed = subprocess.run(
            ["limactl", "shell", instance, "bash", "-c", command],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not completed.stdout:
        return None
    return _write_diagnostic(local_path, completed.stdout)


def _write_diagnostic(path: Path, content: bytes) -> Path | None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content[:MAX_DIAGNOSTIC_BYTES])
    except OSError:
        return None
    return path


def _delete_instance(instance: str) -> None:
    try:
        listed = _run(
            ["limactl", "list", "--format", "{{.Name}}"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlPlaneError(f"cannot list Lima instances: {exc}") from exc
    if instance not in listed.stdout.splitlines():
        return
    try:
        completed = _run(
            ["limactl", "delete", "--force", instance],
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlPlaneError(f"cannot delete Lima instance {instance}: {exc}") from exc
    if completed.returncode == 0:
        return
    try:
        _run(["limactl", "stop", "--force", instance], check=False, capture_output=True)
        retry = _run(
            ["limactl", "delete", "--force", instance],
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlPlaneError(f"cannot force-delete Lima instance {instance}: {exc}") from exc
    if retry.returncode != 0:
        detail = retry.stderr.strip() or completed.stderr.strip() or "unknown Lima error"
        raise ControlPlaneError(f"failed to delete {instance}: {detail}")


def _delete_runner_registration(config: RunnerConfig, runner_name: str) -> None:
    """Remove one exact stale org runner if ephemeral deregistration did not."""

    if not SAFE_NAME_PATTERN.fullmatch(runner_name) or not runner_name.startswith(
        f"{config.instance_prefix}-"
    ):
        raise ControlPlaneError("refusing unsafe runner registration cleanup target")
    try:
        completed = _run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
                f"orgs/{config.organization}/actions/runners",
                "-f",
                f"name={runner_name}",
                "--jq",
                f'[.runners[] | select(.name == "{runner_name}") | {{id,name}}]',
            ],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlPlaneError(f"cannot list org runners: {exc}") from exc
    try:
        runners = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ControlPlaneError("GitHub returned invalid runner cleanup metadata") from exc
    if not isinstance(runners, list):
        raise ControlPlaneError("GitHub returned invalid runner cleanup metadata")
    if not runners:
        return
    if len(runners) != 1 or not isinstance(runners[0], dict):
        raise ControlPlaneError(f"refusing ambiguous runner cleanup for {runner_name}")
    runner_id = runners[0].get("id")
    if isinstance(runner_id, bool) or not isinstance(runner_id, int) or runner_id < 1:
        raise ControlPlaneError("GitHub returned an invalid runner ID")
    try:
        _run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
                f"orgs/{config.organization}/actions/runners/{runner_id}",
            ]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlPlaneError(f"cannot delete org runner {runner_name}: {exc}") from exc


def serve_one(
    config: RunnerConfig,
    *,
    pull_request: int,
    dry_run: bool,
    approve_registration: bool,
) -> int:
    """Create one ephemeral runner guest, process at most one job, then delete it."""

    instance = _new_instance_name(config.instance_prefix)
    job_label = _new_job_label(config.job_label_prefix)
    start_arguments = _start_arguments(config, instance)
    if dry_run:
        print("dry run, no VM or GitHub runner will be created")
        print(f"+ verify open same-repository PR #{pull_request} targets {config.repository}:main")
        _print_command(start_arguments)
        print(
            f"+ gh api --method POST orgs/{config.organization}/actions/runners/registration-token"
        )
        print(
            "+ gh api --method POST "
            f"repos/{config.repository}/actions/workflows/mac-mini-ci.yml/dispatches "
            f"[exact PR SHA; one-use label {job_label}]"
        )
        print(
            f"+ limactl shell {instance} [verified runner {config.runner_version}; "
            "registration token over stdin; --ephemeral; one job]"
        )
        _print_command(["limactl", "delete", "--force", instance])
        return 0

    if not approve_registration:
        raise ControlPlaneError(
            "runner registration requires --approve-registration; use --dry-run first"
        )
    if preflight(config, require_runner_group=True) != 0:
        raise ControlPlaneError("preflight failed")

    target = _verified_pull_request(config, pull_request)
    state_directory = _state_directory()
    with exclusive_control_lock(state_directory):
        start_attempted = False
        diagnostic_path: Path | None = None
        job_returncode: int | None = None
        try:
            start_attempted = True
            _run(start_arguments)
            _lock_guest_privileges(instance)
            _install_guest_helper(instance, "serve-one.sh", _runner_guest_script())
            current_target = _verified_pull_request(config, pull_request)
            if current_target != target:
                raise ControlPlaneError(
                    "pull-request head changed while the disposable guest was starting"
                )
            group_errors = _runner_group_errors(config)
            if group_errors:
                raise ControlPlaneError(
                    "runner-group policy changed after preflight: " + "; ".join(group_errors)
                )
            token = _registration_token(config)
            configure_command = [
                "limactl",
                "shell",
                instance,
                "bash",
                "-lc",
                'exec "$HOME/.alfred-ci/serve-one.sh" "$@"',
                "--",
                "configure",
                config.organization_url,
                config.runner_group,
                instance,
                job_label,
                config.runner_version,
                config.runner_sha256,
            ]
            _run(configure_command, input_text=f"{token}\n")
            _dispatch_workflow(config, current_target, job_label)
            print(
                f"queued trusted workflow for PR #{target.number} at {target.sha} "
                f"with label {job_label}"
            )
            timeout_seconds = config.job_timeout_minutes * 60
            run_command = [
                "limactl",
                "shell",
                instance,
                "bash",
                "-lc",
                'exec "$HOME/.alfred-ci/serve-one.sh" "$@"',
                "--",
                "run",
                str(timeout_seconds),
            ]
            completed = _run(run_command, check=False)
            job_returncode = completed.returncode
        finally:
            active_exception = sys.exception()
            if start_attempted:
                try:
                    diagnostic_path = _capture_diagnostics(instance, state_directory)
                except ControlPlaneError as exc:
                    print(f"WARNING: diagnostic capture failed: {exc}", file=sys.stderr)
                cleanup_errors: list[str] = []
                try:
                    _delete_instance(instance)
                except ControlPlaneError as exc:
                    cleanup_errors.append(str(exc))
                try:
                    _delete_runner_registration(config, instance)
                except ControlPlaneError as exc:
                    cleanup_errors.append(str(exc))
                if cleanup_errors:
                    detail = "; ".join(cleanup_errors)
                    message = (
                        f"cleanup incomplete for {instance}: {detail}; recover with "
                        f"cleanup --instance {instance} --approve-delete"
                    )
                    print(f"ERROR: {message}", file=sys.stderr)
                    if active_exception is None:
                        if job_returncode is None:
                            raise ControlPlaneError(message)
                        if job_returncode == 0:
                            job_returncode = 2
            if diagnostic_path is not None:
                print(f"diagnostics: {diagnostic_path}")
        if job_returncode is None:
            raise ControlPlaneError("runner stopped without a job result")
        return job_returncode


def _verified_commit(config: RunnerConfig, sha: str) -> str:
    if not SHA_PATTERN.fullmatch(sha):
        raise ControlPlaneError("commit SHA must be exactly 40 lowercase hexadecimal characters")
    completed = _run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{config.repository}/commits/{sha}",
            "--jq",
            ".sha",
        ],
        capture_output=True,
    )
    resolved = completed.stdout.strip().lower()
    if resolved != sha:
        raise ControlPlaneError("GitHub did not resolve the requested exact commit")
    return resolved


def _fallback_guest_script(config: RunnerConfig, sha: str) -> str:
    command_lines = "\n".join(
        f"run_check {shlex.join(command)}" for command in config.fallback.commands
    )
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -Eeuo pipefail
        umask 077
        export CI=true
        exec >"$HOME/.alfred-ci/fallback-console.log" 2>&1

        repository_url={shlex.quote(config.clone_url)}
        expected_sha={shlex.quote(sha)}
        uv_version={shlex.quote(config.uv_version)}
        uv_sha256={shlex.quote(config.uv_sha256)}
        checkout="$HOME/fallback-checkout"
        uv_archive="$HOME/uv.tar.gz"

        mkdir -p "$HOME/.local/bin"
        curl --fail --location --proto '=https' --tlsv1.2 \
          --output "$uv_archive" \
          "https://github.com/astral-sh/uv/releases/download/${{uv_version}}/uv-aarch64-unknown-linux-gnu.tar.gz"
        printf '%s  %s\n' "$uv_sha256" "$uv_archive" | sha256sum --check --status
        tar --extract --gzip --file "$uv_archive" \
          --directory "$HOME/.local/bin" \
          --strip-components=1
        rm -f "$uv_archive"
        export PATH="$HOME/.local/bin:$PATH"
        test "$(uv --version | awk '{{print $2}}')" = "$uv_version"

        mkdir "$checkout"
        git init --quiet "$checkout"
        cd "$checkout"
        git remote add origin "$repository_url"
        git -c protocol.version=2 fetch \
          --quiet \
          --no-tags \
          --depth=1 \
          origin "$expected_sha"
        git checkout --quiet --detach FETCH_HEAD
        actual_sha=$(git rev-parse HEAD)
        if [[ "$actual_sha" != "$expected_sha" ]]; then
          echo "fetched commit does not match requested SHA" >&2
          exit 65
        fi

        run_check() {{
          printf '==> '
          printf '%q ' "$@"
          printf '\\n'
          "$@"
        }}

        {command_lines}
        """
    )


def _publish_status(config: RunnerConfig, sha: str, state: str, description: str) -> None:
    if state not in {"pending", "success", "failure", "error"}:
        raise ControlPlaneError(f"unsupported commit status state: {state}")
    if len(description) > 140:
        raise ControlPlaneError("commit status description exceeds 140 characters")
    _run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{config.repository}/statuses/{sha}",
            "-f",
            f"state={state}",
            "-f",
            f"context={config.fallback.context}",
            "-f",
            f"description={description}",
        ]
    )


def fallback(
    config: RunnerConfig,
    *,
    sha: str,
    publish_status: bool,
    dry_run: bool,
) -> int:
    """Run trusted local check commands at an exact SHA inside a fresh VM."""

    if not SHA_PATTERN.fullmatch(sha):
        raise ControlPlaneError("commit SHA must be exactly 40 lowercase hexadecimal characters")
    instance = _new_instance_name(config.instance_prefix)
    start_arguments = _start_arguments(config, instance)
    if dry_run:
        print("dry run, no VM or commit status will be created")
        print(f"+ gh api repos/{config.repository}/commits/{sha}")
        _print_command(start_arguments)
        for command in config.fallback.commands:
            print(f"+ [guest at {sha}] {shlex.join(command)}")
        if publish_status:
            print(
                f"+ [host after explicit approval] publish {config.fallback.context!r} "
                "as pending and final"
            )
        _print_command(["limactl", "delete", "--force", instance])
        return 0

    if preflight(config, require_runner_group=False) != 0:
        raise ControlPlaneError("preflight failed")
    verified_sha = _verified_commit(config, sha)
    state_directory = _state_directory()

    if publish_status:
        _publish_status(
            config,
            verified_sha,
            "pending",
            "Disposable Mac Mini checks are running",
        )

    result: int | None = None
    cleanup_failed_after_success = False
    start_attempted = False
    diagnostic_path: Path | None = None
    try:
        with exclusive_control_lock(state_directory):
            try:
                start_attempted = True
                _run(start_arguments)
                _lock_guest_privileges(instance)
                _install_guest_helper(
                    instance,
                    "fallback.sh",
                    _fallback_guest_script(config, verified_sha),
                )
                completed = _run(
                    [
                        "limactl",
                        "shell",
                        instance,
                        "bash",
                        "-lc",
                        (
                            'exec timeout --signal=TERM --kill-after=30s "$1" '
                            '"$HOME/.alfred-ci/fallback.sh"'
                        ),
                        "--",
                        f"{config.job_timeout_minutes * 60}s",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=(config.job_timeout_minutes * 60) + 60,
                )
                result = completed.returncode
            finally:
                if start_attempted:
                    try:
                        try:
                            diagnostics = state_directory / "diagnostics"
                            _ensure_private_directory(diagnostics)
                            diagnostic_path = _capture_guest_file(
                                instance,
                                ".alfred-ci/fallback-console.log",
                                diagnostics / f"{instance}-fallback.log",
                            )
                        except ControlPlaneError as exc:
                            print(f"WARNING: diagnostic capture failed: {exc}", file=sys.stderr)
                    finally:
                        active_exception = sys.exception()
                        try:
                            _delete_instance(instance)
                        except (
                            ControlPlaneError,
                            OSError,
                            subprocess.SubprocessError,
                        ) as exc:
                            message = (
                                f"cleanup incomplete for {instance}: {exc}; recover with "
                                f"cleanup --instance {instance} --approve-delete"
                            )
                            print(f"ERROR: {message}", file=sys.stderr)
                            if active_exception is None:
                                if result is None:
                                    raise ControlPlaneError(message) from exc
                                if result == 0:
                                    result = 2
                                    cleanup_failed_after_success = True
    except (ControlPlaneError, KeyboardInterrupt, OSError, subprocess.SubprocessError):
        if publish_status:
            _publish_status(
                config,
                verified_sha,
                "error",
                "Disposable Mac Mini checks could not complete",
            )
        raise

    if diagnostic_path is not None:
        print(f"diagnostics: {diagnostic_path}")
    if result is None:
        raise ControlPlaneError("fallback stopped without a job result")
    if publish_status:
        if cleanup_failed_after_success:
            _publish_status(
                config,
                verified_sha,
                "error",
                "Checks passed but VM cleanup is incomplete",
            )
        elif result == 0:
            _publish_status(
                config,
                verified_sha,
                "success",
                "Disposable Mac Mini checks passed",
            )
        else:
            _publish_status(
                config,
                verified_sha,
                "failure",
                "Disposable Mac Mini checks failed",
            )
    return result


def cleanup(config: RunnerConfig, *, instance: str, approve_delete: bool) -> int:
    """Delete one exact stale VM after an explicit operator approval."""

    if not approve_delete:
        raise ControlPlaneError("cleanup requires --approve-delete")
    if (
        not SAFE_NAME_PATTERN.fullmatch(instance)
        or not instance.startswith(f"{config.instance_prefix}-")
        or len(instance) == len(config.instance_prefix) + 1
    ):
        raise ControlPlaneError(
            f"cleanup target must be one exact {config.instance_prefix}-* instance"
        )
    if shutil.which("limactl") is None:
        raise ControlPlaneError("limactl is not installed")
    state_directory = _state_directory()
    with exclusive_control_lock(state_directory):
        cleanup_errors: list[str] = []
        try:
            _delete_instance(instance)
        except ControlPlaneError as exc:
            cleanup_errors.append(str(exc))
        try:
            _delete_runner_registration(config, instance)
        except ControlPlaneError as exc:
            cleanup_errors.append(str(exc))
        if cleanup_errors:
            raise ControlPlaneError("; ".join(cleanup_errors))
    print(f"deleted Lima instance and stale runner registration if present: {instance}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alfred-ci-runner",
        description="Run one Alfred CI job in a disposable Lima guest.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("examples/ci-runner/runner.toml"),
        help="trusted runner TOML (default: examples/ci-runner/runner.toml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("preflight", help="run read-only host and config checks")

    serve = subparsers.add_parser("serve-one", help="register one ephemeral job runner")
    serve.add_argument(
        "--pr",
        type=int,
        required=True,
        help="open same-repository pull-request number",
    )
    serve.add_argument("--dry-run", action="store_true", help="print mutations only")
    serve.add_argument(
        "--approve-registration",
        action="store_true",
        help="explicitly approve repository runner registration",
    )

    fallback_parser = subparsers.add_parser(
        "fallback",
        help="run configured checks at one exact SHA in a disposable guest",
    )
    fallback_parser.add_argument("--sha", required=True, help="full lowercase commit SHA")
    fallback_parser.add_argument(
        "--publish-status",
        action="store_true",
        help="publish pending and final commit statuses from the host",
    )
    fallback_parser.add_argument("--dry-run", action="store_true", help="print mutations only")

    cleanup_parser = subparsers.add_parser("cleanup", help="delete one stale runner VM")
    cleanup_parser.add_argument("--instance", required=True, help="exact Lima instance name")
    cleanup_parser.add_argument(
        "--approve-delete",
        action="store_true",
        help="explicitly approve deletion of the named instance",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(arguments)
    try:
        config = load_config(namespace.config)
        if namespace.command == "preflight":
            return preflight(config)
        if namespace.command == "serve-one":
            return serve_one(
                config,
                pull_request=namespace.pr,
                dry_run=namespace.dry_run,
                approve_registration=namespace.approve_registration,
            )
        if namespace.command == "fallback":
            return fallback(
                config,
                sha=namespace.sha,
                publish_status=namespace.publish_status,
                dry_run=namespace.dry_run,
            )
        if namespace.command == "cleanup":
            return cleanup(
                config,
                instance=namespace.instance,
                approve_delete=namespace.approve_delete,
            )
    except (ConfigurationError, ControlPlaneError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
