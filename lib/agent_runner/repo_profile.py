"""Deterministic repo-profile injector (Phase 2 code memory).

A firing runs headless in a repo it may have never seen. Instead of making the
agent rediscover the project's shape every time, Alfred can inject a small,
DETERMINISTIC repo profile up front: the manifest(s) and package manager, the
exact test/lint/build commands to verify with, the agent-instruction files, and
a one-line structure summary. It is a convention-memory block, built from what
Alfred can already see on disk.

Ported IDEA (not code) from Hermes' ``coding_context.build_coding_workspace_block``
/ ``_project_facts``, adapted to Alfred's headless firing model:

* **No session lifecycle.** Hermes resolves an interactive posture (auto/focus/
  on/off) once per session and steers toolsets and the skill index. None of that
  applies to a headless firing, so it is deliberately NOT ported.
* **Deterministic.** Hermes' snapshot includes live ``git status`` (branch,
  dirty counts, recent commits), which drift between calls. The repo profile is
  built from files only, so the SAME tree always yields a byte-identical block.
  A firing that needs live git state runs ``git`` itself.
* **Budget-aware + config-gated.** Injection is OFF by default
  (``ALFRED_REPO_PROFILE``) and bounded to a character budget so "profile on"
  can never balloon the run prompt.

Nothing here recalls, writes, or migrates memory. It reads a handful of small
manifest files and stats a few paths; every read is guarded and total.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "REPO_PROFILE_ENV",
    "RepoProfile",
    "build_repo_profile",
    "format_repo_profile_block",
    "repo_profile_block",
    "repo_profile_enabled",
]

REPO_PROFILE_ENV = "ALFRED_REPO_PROFILE"
REPO_PROFILE_BUDGET_ENV = "ALFRED_REPO_PROFILE_MAX_CHARS"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}

# Default character budget for the injected block. Conservative: a repo profile
# is orientation, not the task, so it should never crowd out recalled lessons or
# the issue body. Env-overridable via ``ALFRED_REPO_PROFILE_MAX_CHARS``.
_DEFAULT_BUDGET = 1200

_MAX_FACT_FILE_BYTES = 256 * 1024

# Project manifests worth surfacing (a subset of Hermes' markers; agent-context
# files are handled separately). Order is the display order.
_MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "tsconfig.json",
    "deno.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "pubspec.yaml",
    "CMakeLists.txt",
    "Makefile",
    "Dockerfile",
)

_CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", ".cursorrules", "CONTRIBUTING.md")

_PY_LOCKFILES = (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"))
_JS_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)

_VERIFY_TARGETS = ("test", "tests", "lint", "typecheck", "check", "build", "fmt", "format")
_MAX_VERIFY_COMMANDS = 8
_MAX_MANIFESTS = 6
_MAX_STRUCTURE_DIRS = 10

# Directories that are never a meaningful structure signal.
_STRUCTURE_SKIP = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
}


def repo_profile_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether repo-profile injection is armed. OFF by default (opt-in).

    Arms only on a recognized truthy token so a config typo cannot silently turn
    it on. Mirrors the fail-closed opt-in used elsewhere in the memory layer.
    """
    raw = str((env or os.environ).get(REPO_PROFILE_ENV, "")).strip().lower()
    return raw in _TRUTHY


def _budget(env: Mapping[str, str] | None = None) -> int:
    raw = (env or os.environ).get(REPO_PROFILE_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return _DEFAULT_BUDGET
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_BUDGET
    return value if value > 0 else _DEFAULT_BUDGET


@dataclass(frozen=True)
class RepoProfile:
    """A deterministic, file-derived snapshot of a repo's shape."""

    root: Path
    manifests: tuple[str, ...] = ()
    package_managers: tuple[str, ...] = ()
    verify_commands: tuple[str, ...] = ()
    context_files: tuple[str, ...] = ()
    structure: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not (
            self.manifests
            or self.package_managers
            or self.verify_commands
            or self.context_files
            or self.structure
        )


def _read_small(path: Path) -> str:
    """Read a small text file, or ``""`` -- never raises, never reads huge files."""
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FACT_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_verify_commands(root: Path) -> tuple[str, ...]:
    """Detect test/lint/build commands, in a stable order. Deterministic."""
    verify: list[str] = []
    if (root / "scripts" / "run_tests.sh").is_file():
        verify.append("scripts/run_tests.sh")
    if (root / "package.json").is_file():
        try:
            scripts = json.loads(_read_small(root / "package.json") or "{}").get("scripts") or {}
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        js_pm = next((pm for lock, pm in _JS_LOCKFILES if (root / lock).is_file()), "npm")
        if isinstance(scripts, dict):
            verify.extend(f"{js_pm} run {name}" for name in _VERIFY_TARGETS if name in scripts)
    if (root / "pytest.ini").is_file() or "[tool.pytest" in _read_small(root / "pyproject.toml"):
        verify.append("pytest")
    makefile = _read_small(root / "Makefile")
    if makefile:
        verify.extend(
            f"make {name}"
            for name in _VERIFY_TARGETS
            if re.search(rf"^{re.escape(name)}\s*:", makefile, re.MULTILINE)
        )
    # Preserve first-seen order, dedupe, cap.
    return tuple(dict.fromkeys(verify))[:_MAX_VERIFY_COMMANDS]


def _detect_structure(root: Path) -> tuple[str, ...]:
    """Top-level directories that signal the repo's layout, sorted. Deterministic."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return ()
    dirs = sorted(
        e.name
        for e in entries
        if e.is_dir(follow_symlinks=False)
        and not e.name.startswith(".")
        and e.name not in _STRUCTURE_SKIP
    )
    return tuple(dirs[:_MAX_STRUCTURE_DIRS])


def build_repo_profile(root: str | Path) -> RepoProfile | None:
    """Build a deterministic profile for ``root``, or ``None`` if unreadable.

    Pure function of the tree's files: the same tree always yields an equal
    profile. Never raises; a missing/denied path returns ``None``.
    """
    try:
        base = Path(root).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None
    if not base.is_dir():
        return None

    manifests = tuple(m for m in _MANIFESTS if (base / m).is_file())[:_MAX_MANIFESTS]
    package_managers = tuple(
        dict.fromkeys(
            pm for lock, pm in (*_PY_LOCKFILES, *_JS_LOCKFILES) if (base / lock).is_file()
        )
    )
    verify = _detect_verify_commands(base)
    context_files = tuple(c for c in _CONTEXT_FILES if (base / c).is_file())
    structure = _detect_structure(base)
    return RepoProfile(
        root=base,
        manifests=manifests,
        package_managers=package_managers,
        verify_commands=verify,
        context_files=context_files,
        structure=structure,
    )


_HEADER = (
    "Repo profile (detected conventions and verify loop; treat as orientation, "
    "confirm against the code):"
)


def format_repo_profile_block(profile: RepoProfile | None, *, budget: int = _DEFAULT_BUDGET) -> str:
    """Render a profile as a convention-memory block, bounded to ``budget`` chars.

    All-or-nothing on the header: if even the header plus one fact would exceed
    the budget, returns ``""``. Fact lines are appended in priority order while
    they fit, so the block never exceeds ``budget`` characters.
    """
    if profile is None or profile.is_empty:
        return ""
    lines: list[str] = []
    if profile.manifests:
        line = f"- Project: {', '.join(profile.manifests)}"
        if profile.package_managers:
            line += f" ({'/'.join(profile.package_managers)})"
        lines.append(line)
    if profile.verify_commands:
        lines.append(f"- Verify: {'; '.join(profile.verify_commands)}")
    if profile.context_files:
        lines.append(f"- Context files (read first): {', '.join(profile.context_files)}")
    if profile.structure:
        lines.append(f"- Structure: {', '.join(profile.structure)}")
    if not lines:
        return ""

    kept = [_HEADER]

    def joined_len(rows: list[str]) -> int:
        return len("\n".join(rows))

    if joined_len(kept) + 1 + len(lines[0]) > budget:
        # Not even the header plus the first fact fits: inject nothing rather
        # than a lone header.
        return ""
    for line in lines:
        if joined_len([*kept, line]) <= budget:
            kept.append(line)
    if len(kept) <= 1:
        return ""
    return "\n".join(kept)


def repo_profile_block(
    root: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Gated convenience: the profile block for ``root``, or ``""``.

    Returns ``""`` unless ``ALFRED_REPO_PROFILE`` is armed (opt-in) and ``root``
    is a readable repo. Budget comes from ``ALFRED_REPO_PROFILE_MAX_CHARS``.
    """
    if not root or not repo_profile_enabled(env):
        return ""
    profile = build_repo_profile(root)
    return format_repo_profile_block(profile, budget=_budget(env))
