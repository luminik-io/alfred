"""Canonical coding-engine descriptors and fail-closed protocol probes.

Executable presence is not enough for an autonomous fleet. A CLI must expose
the exact non-interactive, output, permission, and authentication contracts
Alfred relies on before setup may call it ready. Candidate engines remain
visible in inventory while dispatch stays disabled until their deeper mutation
boundary has contract coverage.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

_ENGINE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PROBE_TIMEOUT_SECONDS = 4.0
_CACHE_TTL_SECONDS = 15.0
_SAFE_PROBE_ENV_VARS = frozenset(
    {
        "COLORTERM",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "NO_COLOR",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)


class EngineCapability(StrEnum):
    """One independently verifiable engine behavior."""

    TEXT = "text"
    REPOSITORY_READ = "repository-read"
    WORKTREE_WRITE = "worktree-write"
    MODEL_SELECTION = "model-selection"
    STREAMING = "streaming"
    TOOL_ALLOWLIST = "tool-allowlist"
    MAX_TURNS = "max-turns"
    RESUME = "resume"
    SANDBOX = "sandbox"
    EXTRA_DIRECTORIES = "extra-directories"
    STRUCTURED_OUTPUT = "structured-output"
    NON_INTERACTIVE = "non-interactive"


@dataclass(frozen=True)
class ProbeCommand:
    """A bounded command whose exit code and public flags form a contract."""

    args: tuple[str, ...]
    markers: tuple[str, ...] = ()
    reason: str = "protocol_mismatch"
    env_vars: frozenset[str] = frozenset()
    satisfying_env_vars: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EngineDescriptor:
    """Stable metadata for one coding harness."""

    id: str
    display_name: str
    binary_env: str
    default_binary: str
    capabilities: frozenset[EngineCapability]
    protocol_commands: tuple[ProbeCommand, ...]
    auth_command: ProbeCommand | None = None
    dispatchable: bool = False

    def __post_init__(self) -> None:
        if not _ENGINE_ID.fullmatch(self.id):
            raise ValueError(f"invalid engine id: {self.id!r}")
        if not self.display_name.strip():
            raise ValueError("engine display name must not be blank")
        if not self.binary_env.strip() or not self.default_binary.strip():
            raise ValueError("engine binary contract must not be blank")


@dataclass(frozen=True)
class EngineProbeResult:
    """Sanitized readiness result safe for local APIs and logs."""

    descriptor: EngineDescriptor
    installed: bool
    protocol_compatible: bool
    ready: bool
    state: str
    detail: str
    binary: str | None
    version: str | None
    failures: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.descriptor.id,
            "display_name": self.descriptor.display_name,
            "installed": self.installed,
            "protocol_compatible": self.protocol_compatible,
            "ready": self.ready,
            "dispatchable": self.descriptor.dispatchable,
            "state": self.state,
            "detail": self.detail,
            "path": self.binary,
            "version": self.version,
            "capabilities": sorted(capability.value for capability in self.descriptor.capabilities),
            "failures": list(self.failures),
        }


_CLAUDE_CAPABILITIES = frozenset(
    {
        EngineCapability.TEXT,
        EngineCapability.REPOSITORY_READ,
        EngineCapability.WORKTREE_WRITE,
        EngineCapability.MODEL_SELECTION,
        EngineCapability.STREAMING,
        EngineCapability.TOOL_ALLOWLIST,
        EngineCapability.MAX_TURNS,
        EngineCapability.RESUME,
        EngineCapability.STRUCTURED_OUTPUT,
        EngineCapability.NON_INTERACTIVE,
    }
)
_CODEX_CAPABILITIES = frozenset(
    {
        EngineCapability.TEXT,
        EngineCapability.REPOSITORY_READ,
        EngineCapability.WORKTREE_WRITE,
        EngineCapability.MODEL_SELECTION,
        EngineCapability.SANDBOX,
        EngineCapability.EXTRA_DIRECTORIES,
        EngineCapability.STRUCTURED_OUTPUT,
        EngineCapability.NON_INTERACTIVE,
    }
)

ENGINE_DESCRIPTORS: tuple[EngineDescriptor, ...] = (
    EngineDescriptor(
        id="claude",
        display_name="Claude Code",
        binary_env="CLAUDE_BIN",
        default_binary="claude",
        capabilities=_CLAUDE_CAPABILITIES,
        protocol_commands=(
            ProbeCommand(("--version",), reason="version_failed"),
            ProbeCommand(
                ("--help",),
                markers=("--output-format", "--permission-mode", "--allowedtools"),
            ),
        ),
        auth_command=ProbeCommand(
            ("auth", "status"),
            reason="auth_required",
            env_vars=frozenset(
                {
                    "ANTHROPIC_API_KEY",
                    "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CONFIG_DIR",
                }
            ),
        ),
        dispatchable=True,
    ),
    EngineDescriptor(
        id="codex",
        display_name="Codex",
        binary_env="CODEX_BIN",
        default_binary="codex",
        capabilities=_CODEX_CAPABILITIES,
        protocol_commands=(
            ProbeCommand(("--version",), reason="version_failed"),
            ProbeCommand(
                ("exec", "--help"),
                markers=("--output-last-message", "--sandbox", "--cd"),
            ),
        ),
        auth_command=ProbeCommand(
            ("login", "status"),
            reason="auth_required",
            env_vars=frozenset({"CODEX_HOME"}),
            satisfying_env_vars=frozenset({"OPENAI_API_KEY"}),
        ),
        dispatchable=True,
    ),
    EngineDescriptor(
        id="opencode",
        display_name="OpenCode",
        binary_env="OPENCODE_BIN",
        default_binary="opencode",
        capabilities=frozenset(
            {
                EngineCapability.TEXT,
                EngineCapability.REPOSITORY_READ,
                EngineCapability.MODEL_SELECTION,
                EngineCapability.STRUCTURED_OUTPUT,
                EngineCapability.NON_INTERACTIVE,
            }
        ),
        protocol_commands=(
            ProbeCommand(("--version",), reason="version_failed"),
            ProbeCommand(
                ("run", "--help"),
                markers=("--format", "--model", "--dir", "--agent"),
            ),
        ),
    ),
    EngineDescriptor(
        id="cline",
        display_name="Cline",
        binary_env="CLINE_BIN",
        default_binary="cline",
        capabilities=frozenset(
            {
                EngineCapability.TEXT,
                EngineCapability.STRUCTURED_OUTPUT,
                EngineCapability.NON_INTERACTIVE,
            }
        ),
        protocol_commands=(
            ProbeCommand(("--version",), reason="version_failed"),
            ProbeCommand(("--help",), markers=("--json", "--timeout", "--yolo")),
        ),
    ),
)


class EngineRegistry:
    """Validated descriptor index and inventory facade."""

    def __init__(self, descriptors: Collection[EngineDescriptor]) -> None:
        rows = tuple(descriptors)
        by_id = {descriptor.id: descriptor for descriptor in rows}
        if len(by_id) != len(rows):
            raise ValueError("engine descriptor ids must be unique")
        self._descriptors = rows
        self._by_id = by_id

    @property
    def descriptors(self) -> tuple[EngineDescriptor, ...]:
        return self._descriptors

    @property
    def dispatchable_ids(self) -> frozenset[str]:
        return frozenset(row.id for row in self._descriptors if row.dispatchable)

    def descriptor(self, engine_id: str) -> EngineDescriptor:
        canonical = engine_id.strip().lower()
        try:
            return self._by_id[canonical]
        except KeyError as exc:
            raise ValueError(f"unknown engine: {engine_id!r}") from exc

    def supporting(self, required: Collection[EngineCapability]) -> tuple[EngineDescriptor, ...]:
        needed = frozenset(required)
        return tuple(row for row in self._descriptors if needed <= row.capabilities)

    def inventory(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        search_path: str | None = None,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        return [
            probe_engine(
                descriptor,
                environ=environ,
                search_path=search_path,
                use_cache=use_cache,
            ).as_dict()
            for descriptor in self._descriptors
        ]


DEFAULT_ENGINE_REGISTRY = EngineRegistry(ENGINE_DESCRIPTORS)

_ProbeCacheKey = tuple[str, str, int, int]
_probe_cache: dict[_ProbeCacheKey, tuple[float, EngineProbeResult]] = {}


def clear_engine_probe_cache() -> None:
    _probe_cache.clear()


def _resolve_binary(
    descriptor: EngineDescriptor,
    *,
    environ: Mapping[str, str],
    search_path: str | None,
    which: Callable[..., str | None] = shutil.which,
) -> str | None:
    configured = environ.get(descriptor.binary_env, "").strip()
    candidate = configured or descriptor.default_binary
    expanded = os.path.expanduser(candidate)
    if os.path.isabs(expanded):
        path = Path(expanded)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return which(candidate, path=search_path)


def _fingerprint(path: str) -> tuple[int, int]:
    try:
        stat = Path(path).stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _safe_version(output: str) -> str | None:
    for raw_line in output.splitlines():
        line = _ANSI_ESCAPE.sub("", raw_line).strip()
        if line:
            return line[:160]
    return None


def _run_probe(
    command: list[str],
    *,
    environ: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
    extra_env_vars: Collection[str] = (),
) -> subprocess.CompletedProcess[str] | None:
    allowed = _SAFE_PROBE_ENV_VARS | frozenset(extra_env_vars)
    child_env = {key: value for key, value in environ.items() if key in allowed}
    try:
        return runner(
            command,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            env=child_env,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None


def probe_engine(
    descriptor: EngineDescriptor,
    *,
    environ: Mapping[str, str] | None = None,
    search_path: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[..., str | None] = shutil.which,
    use_cache: bool = True,
) -> EngineProbeResult:
    """Probe one engine without retaining command output or account details."""

    env = environ if environ is not None else os.environ
    resolved_search_path = search_path if search_path is not None else env.get("PATH")
    binary = _resolve_binary(
        descriptor,
        environ=env,
        search_path=resolved_search_path,
        which=which,
    )
    if not binary:
        return EngineProbeResult(
            descriptor=descriptor,
            installed=False,
            protocol_compatible=False,
            ready=False,
            state="missing",
            detail=f"{descriptor.display_name} is not installed.",
            binary=None,
            version=None,
            failures=("missing_binary",),
        )

    mtime_ns, size = _fingerprint(binary)
    cache_key = (descriptor.id, binary, mtime_ns, size)
    cached = _probe_cache.get(cache_key) if use_cache else None
    cached_result = cached[1] if cached and cached[0] > time.monotonic() else None
    if cached_result and (not descriptor.dispatchable or not cached_result.protocol_compatible):
        return cached_result

    version = cached_result.version if cached_result else None
    failure: str | None = None
    if cached_result is None:
        for index, requirement in enumerate(descriptor.protocol_commands):
            completed = _run_probe(
                [binary, *requirement.args],
                environ=env,
                runner=runner,
            )
            if completed is None or completed.returncode != 0:
                failure = requirement.reason
                break
            combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
            if index == 0:
                version = _safe_version(combined)
            normalized = combined.lower()
            if any(marker.lower() not in normalized for marker in requirement.markers):
                failure = requirement.reason
                break

    if failure:
        result = EngineProbeResult(
            descriptor=descriptor,
            installed=True,
            protocol_compatible=False,
            ready=False,
            state="incompatible",
            detail=f"{descriptor.display_name} does not expose Alfred's required CLI protocol.",
            binary=binary,
            version=version,
            failures=(failure,),
        )
    elif not descriptor.dispatchable:
        result = EngineProbeResult(
            descriptor=descriptor,
            installed=True,
            protocol_compatible=True,
            ready=False,
            state="needs_validation",
            detail=(
                f"{descriptor.display_name} was detected, but autonomous dispatch stays disabled "
                "until its permission boundary passes a deep probe."
            ),
            binary=binary,
            version=version,
            failures=("deep_probe_required",),
        )
    else:
        auth = descriptor.auth_command
        auth_satisfied_by_env = bool(
            auth and any(env.get(name, "").strip() for name in auth.satisfying_env_vars)
        )
        completed = (
            _run_probe(
                [binary, *auth.args],
                environ=env,
                runner=runner,
                extra_env_vars=auth.env_vars,
            )
            if auth and not auth_satisfied_by_env
            else None
        )
        if auth and not auth_satisfied_by_env and (completed is None or completed.returncode != 0):
            result = EngineProbeResult(
                descriptor=descriptor,
                installed=True,
                protocol_compatible=True,
                ready=False,
                state="auth_required",
                detail=f"{descriptor.display_name} is installed but is not signed in.",
                binary=binary,
                version=version,
                failures=(auth.reason,),
            )
        else:
            result = EngineProbeResult(
                descriptor=descriptor,
                installed=True,
                protocol_compatible=True,
                ready=True,
                state="ready",
                detail=f"{descriptor.display_name} is compatible and signed in.",
                binary=binary,
                version=version,
            )

    if use_cache:
        _probe_cache[cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
    return result
