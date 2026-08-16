"""Canonical coding-engine descriptors and fail-closed protocol probes.

Executable presence is not enough for an autonomous fleet. A CLI must expose
the exact non-interactive, output, permission, and authentication contracts
Alfred relies on before setup may call it ready. Candidate engines remain
visible in inventory while dispatch stays disabled until their deeper mutation
boundary has contract coverage.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

_ENGINE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SEMVER = re.compile(r"\b(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\b")
_PROBE_TIMEOUT_SECONDS = 4.0
_INVENTORY_DEADLINE_SECONDS = 8.0
_CACHE_TTL_SECONDS = 15.0
_DEFAULT_PROBE_RUNNER = subprocess.run
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


class EngineProbeState(StrEnum):
    """One closed readiness state shared by setup and dispatch policy."""

    MISSING = "missing"
    NEEDS_VALIDATION = "needs_validation"
    PROBE_FAILED = "probe_failed"
    INCOMPATIBLE = "incompatible"
    AUTH_REQUIRED = "auth_required"
    READY = "ready"


@dataclass(frozen=True)
class ProbeCommand:
    """A bounded command whose exit code and public flags form a contract."""

    args: tuple[str, ...]
    markers: tuple[str, ...] = ()
    reason: str = "protocol_mismatch"
    env_vars: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EngineDescriptor:
    """Stable metadata for one coding harness."""

    id: str
    display_name: str
    binary_env: str
    default_binary: str
    capabilities: frozenset[EngineCapability]
    protocol_commands: tuple[ProbeCommand, ...]
    minimum_version: tuple[int, int, int] | None = None
    auth_command: ProbeCommand | None = None
    dispatchable: bool = False

    def __post_init__(self) -> None:
        if not _ENGINE_ID.fullmatch(self.id):
            raise ValueError(f"invalid engine id: {self.id!r}")
        if not self.display_name.strip():
            raise ValueError("engine display name must not be blank")
        if not self.binary_env.strip() or not self.default_binary.strip():
            raise ValueError("engine binary contract must not be blank")
        if self.minimum_version is not None and any(part < 0 for part in self.minimum_version):
            raise ValueError("minimum engine version must not be negative")


@dataclass(frozen=True)
class EngineProbeResult:
    """Sanitized readiness result safe for local APIs and logs."""

    descriptor: EngineDescriptor
    installed: bool
    protocol_compatible: bool
    ready: bool
    state: EngineProbeState
    detail: str
    binary: str | None
    version: str | None
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, EngineProbeState):
            object.__setattr__(self, "state", EngineProbeState(self.state))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.descriptor.id,
            "display_name": self.descriptor.display_name,
            "installed": self.installed,
            "protocol_compatible": self.protocol_compatible,
            "ready": self.ready,
            "dispatchable": self.descriptor.dispatchable,
            "state": self.state.value,
            "detail": self.detail,
            "path": self.binary,
            "version": self.version,
            "minimum_version": (
                ".".join(str(part) for part in self.descriptor.minimum_version)
                if self.descriptor.minimum_version
                else None
            ),
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
            # Claude documents that top-level help is intentionally incomplete,
            # so missing flag text cannot be used as a compatibility signal.
            # The stable version command proves the executable boundary and the
            # supported major version owns Alfred's invocation contract.
            ProbeCommand(("--version",), markers=("claude",), reason="version_failed"),
        ),
        minimum_version=(2, 1, 41),
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
                markers=(
                    "--output-last-message",
                    "--sandbox",
                    "--cd",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ephemeral",
                    "-c",
                    "--model",
                    "--add-dir",
                    "--dangerously-bypass-approvals-and-sandbox",
                ),
            ),
        ),
        auth_command=ProbeCommand(
            ("login", "status"),
            reason="auth_required",
            env_vars=frozenset(
                {
                    "CODEX_ACCESS_TOKEN",
                    "CODEX_CA_CERTIFICATE",
                    "CODEX_HOME",
                    "SSL_CERT_DIR",
                    "SSL_CERT_FILE",
                }
            ),
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
    """Validated descriptor index and bounded local inventory."""

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

    def resolve_binary(
        self,
        engine_id: str,
        *,
        environ: Mapping[str, str] | None = None,
        search_path: str | None = None,
        which: Callable[..., str | None] = shutil.which,
    ) -> str | None:
        """Resolve one engine executable without running the CLI."""

        env = environ if environ is not None else os.environ
        resolved_search_path = search_path if search_path is not None else env.get("PATH")
        return _resolve_binary(
            self.descriptor(engine_id),
            environ=env,
            search_path=resolved_search_path,
            which=which,
        )

    def inventory(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        search_path: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = _DEFAULT_PROBE_RUNNER,
        which: Callable[..., str | None] = shutil.which,
        clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = _INVENTORY_DEADLINE_SECONDS,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        deadline = clock() + max(0.0, deadline_seconds)
        dispatchable = [row for row in self._descriptors if row.dispatchable]
        probed: dict[str, EngineProbeResult] = {}
        if dispatchable:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(dispatchable)) as pool:
                futures = {
                    pool.submit(
                        probe_engine,
                        descriptor,
                        environ=environ,
                        search_path=search_path,
                        runner=runner,
                        which=which,
                        use_cache=use_cache,
                        refresh_auth=False,
                        deadline=deadline,
                        clock=clock,
                    ): descriptor
                    for descriptor in dispatchable
                }
                for future in concurrent.futures.as_completed(futures):
                    descriptor = futures[future]
                    try:
                        probed[descriptor.id] = future.result()
                    except Exception:
                        probed[descriptor.id] = _unexpected_probe_failure(
                            descriptor,
                            environ=environ,
                            search_path=search_path,
                            which=which,
                        )

        rows: list[dict[str, Any]] = []
        for descriptor in self._descriptors:
            result = (
                probed[descriptor.id]
                if descriptor.dispatchable
                else detect_candidate_engine(
                    descriptor,
                    environ=environ,
                    search_path=search_path,
                    which=which,
                )
            )
            rows.append(result.as_dict())
        return rows


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
    resolved: str | None
    if os.path.isabs(expanded):
        resolved = expanded
    else:
        resolved = which(candidate, path=search_path)
    if not resolved:
        return None
    try:
        path = Path(resolved).resolve(strict=True)
    except OSError:
        return None
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def detect_candidate_engine(
    descriptor: EngineDescriptor,
    *,
    environ: Mapping[str, str] | None = None,
    search_path: str | None = None,
    which: Callable[..., str | None] = shutil.which,
) -> EngineProbeResult:
    """Detect a candidate harness without running it during setup inventory."""

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
            state=EngineProbeState.MISSING,
            detail=f"{descriptor.display_name} is not installed.",
            binary=None,
            version=None,
            failures=("missing_binary",),
        )
    return EngineProbeResult(
        descriptor=descriptor,
        installed=True,
        protocol_compatible=False,
        ready=False,
        state=EngineProbeState.NEEDS_VALIDATION,
        detail=(
            f"{descriptor.display_name} was detected, but autonomous dispatch stays disabled "
            "until its permission boundary passes a deep probe."
        ),
        binary=binary,
        version=None,
        failures=("deep_probe_required",),
    )


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


def _semantic_version(output: str) -> tuple[int, int, int] | None:
    match = _SEMVER.search(output)
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _has_protocol_marker(output: str, marker: str) -> bool:
    """Match CLI options as complete tokens and prose markers as substrings."""

    if not marker.startswith("-"):
        return marker.lower() in output.lower()
    return re.search(rf"(?<![\w-]){re.escape(marker)}(?![\w-])", output) is not None


def _unexpected_probe_failure(
    descriptor: EngineDescriptor,
    *,
    environ: Mapping[str, str] | None,
    search_path: str | None,
    which: Callable[..., str | None],
) -> EngineProbeResult:
    """Keep one broken probe from hiding healthy engines in the inventory."""

    env = environ if environ is not None else os.environ
    try:
        binary = _resolve_binary(
            descriptor,
            environ=env,
            search_path=search_path if search_path is not None else env.get("PATH"),
            which=which,
        )
    except Exception:
        binary = None
    return EngineProbeResult(
        descriptor=descriptor,
        installed=binary is not None,
        protocol_compatible=False,
        ready=False,
        state=EngineProbeState.PROBE_FAILED,
        detail=f"Alfred could not verify {descriptor.display_name} readiness.",
        binary=binary,
        version=None,
        failures=("unexpected_probe_failure",),
    )


def _process_group_member_pids(group_id: int) -> tuple[int, ...]:
    """Return a best-effort snapshot of one POSIX process group."""

    members: set[int] = set()
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for stat_path in proc_root.glob("[0-9]*/stat"):
            try:
                raw = stat_path.read_text(encoding="utf-8")
                fields = raw[raw.rfind(")") + 2 :].split()
                if len(fields) >= 3 and int(fields[2]) == group_id:
                    members.add(int(stat_path.parent.name))
            except (OSError, ValueError):
                continue
        return tuple(sorted(members))

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, process_group = (int(value) for value in fields)
        except ValueError:
            continue
        if process_group == group_id:
            members.add(pid)
    return tuple(sorted(members))


def _terminate_probe_process_group(process: subprocess.Popen[str]) -> None:
    """Kill a timed-out probe and every helper process it started, then reap it."""

    if os.name == "nt":
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()
    else:
        parent_stopped = False
        try:
            os.kill(process.pid, signal.SIGSTOP)
            parent_stopped = True
        except (ProcessLookupError, OSError):
            pass
        if parent_stopped:
            for pid in _process_group_member_pids(process.pid):
                if pid == process.pid:
                    continue
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError, OSError):
                os.kill(process.pid, signal.SIGCONT)
        else:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(process.pid, signal.SIGKILL)
    try:
        process.communicate(timeout=1)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        with contextlib.suppress(ProcessLookupError, OSError):
            process.terminate()
        try:
            process.communicate(timeout=1)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                process.wait(timeout=1)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()


def _run_production_probe(
    command: list[str],
    *,
    child_env: Mapping[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str] | None:
    """Run a real probe in its own process group so timeout cleanup is complete."""

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        start_new_session=(os.name != "nt"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_probe_process_group(process)
        return None
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout or "",
        stderr or "",
    )


def _run_probe(
    command: list[str],
    *,
    environ: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout_seconds: float = _PROBE_TIMEOUT_SECONDS,
    extra_env_vars: Collection[str] = (),
) -> subprocess.CompletedProcess[str] | None:
    allowed = _SAFE_PROBE_ENV_VARS | frozenset(extra_env_vars)
    child_env = {key: value for key, value in environ.items() if key in allowed}
    try:
        if runner is _DEFAULT_PROBE_RUNNER:
            return _run_production_probe(
                command,
                child_env=child_env,
                timeout_seconds=timeout_seconds,
            )
        return runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=child_env,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None


def probe_engine(
    descriptor: EngineDescriptor,
    *,
    environ: Mapping[str, str] | None = None,
    search_path: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _DEFAULT_PROBE_RUNNER,
    which: Callable[..., str | None] = shutil.which,
    use_cache: bool = True,
    refresh_auth: bool = True,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
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
            state=EngineProbeState.MISSING,
            detail=f"{descriptor.display_name} is not installed.",
            binary=None,
            version=None,
            failures=("missing_binary",),
        )

    mtime_ns, size = _fingerprint(binary)
    cache_key = (descriptor.id, binary, mtime_ns, size)
    cached = _probe_cache.get(cache_key) if use_cache else None
    cached_result = cached[1] if cached and cached[0] > time.monotonic() else None
    if cached_result and (
        not descriptor.dispatchable or not cached_result.protocol_compatible or not refresh_auth
    ):
        return cached_result

    version = cached_result.version if cached_result else None
    failure: str | None = None
    probe_failed = False
    if cached_result is None:
        for index, requirement in enumerate(descriptor.protocol_commands):
            remaining = (
                _PROBE_TIMEOUT_SECONDS
                if deadline is None
                else min(_PROBE_TIMEOUT_SECONDS, deadline - clock())
            )
            if remaining <= 0:
                probe_failed = True
                break
            completed = _run_probe(
                [binary, *requirement.args],
                environ=env,
                runner=runner,
                timeout_seconds=remaining,
            )
            if completed is None:
                probe_failed = True
                break
            if completed.returncode != 0:
                failure = requirement.reason
                break
            combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
            if index == 0:
                version = _safe_version(combined)
                parsed_version = _semantic_version(version or "")
                if descriptor.minimum_version is not None and (
                    parsed_version is None or parsed_version < descriptor.minimum_version
                ):
                    failure = "unsupported_version"
                    break
            if any(not _has_protocol_marker(combined, marker) for marker in requirement.markers):
                failure = requirement.reason
                break

    if probe_failed:
        result = EngineProbeResult(
            descriptor=descriptor,
            installed=True,
            protocol_compatible=False,
            ready=False,
            state=EngineProbeState.PROBE_FAILED,
            detail=f"Alfred could not verify {descriptor.display_name}'s required CLI protocol.",
            binary=binary,
            version=version,
            failures=("protocol_probe_failed",),
        )
    elif failure:
        result = EngineProbeResult(
            descriptor=descriptor,
            installed=True,
            protocol_compatible=False,
            ready=False,
            state=EngineProbeState.INCOMPATIBLE,
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
            state=EngineProbeState.NEEDS_VALIDATION,
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
        remaining = (
            _PROBE_TIMEOUT_SECONDS
            if deadline is None
            else min(_PROBE_TIMEOUT_SECONDS, deadline - clock())
        )
        completed = None
        if auth is not None and remaining > 0:
            completed = _run_probe(
                [binary, *auth.args],
                environ=env,
                runner=runner,
                timeout_seconds=remaining,
                extra_env_vars=auth.env_vars,
            )
        if auth and completed is None:
            result = EngineProbeResult(
                descriptor=descriptor,
                installed=True,
                protocol_compatible=True,
                ready=False,
                state=EngineProbeState.PROBE_FAILED,
                detail=f"Alfred could not verify {descriptor.display_name} authentication.",
                binary=binary,
                version=version,
                failures=("auth_probe_failed",),
            )
        elif auth is not None and completed is not None and completed.returncode != 0:
            result = EngineProbeResult(
                descriptor=descriptor,
                installed=True,
                protocol_compatible=True,
                ready=False,
                state=EngineProbeState.AUTH_REQUIRED,
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
                state=EngineProbeState.READY,
                detail=f"{descriptor.display_name} is compatible and signed in.",
                binary=binary,
                version=version,
            )

    if use_cache and result.state != "probe_failed":
        _probe_cache[cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
    return result
