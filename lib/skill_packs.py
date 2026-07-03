"""Curated skill-pack registry: manifest parsing and install-path resolution.

alfred-os curates a small set of Claude Code skills (see `skills/packs.toml`)
and knows how to place them where the Claude Code CLI discovers them. This
module is the pure, testable core behind the `alfred skills` CLI subcommand.

Two install shapes, declared per pack in the manifest:

* ``vendored`` -- the skill is copied into this repo under
  ``skills/vendored/<path>`` with its upstream license intact. Installing copies
  that directory into the target skills dir. No network, deterministic, works
  offline and in CI.
* ``fetch`` -- the skill is NOT in this repo (it is large, or a heavy dependency
  best pinned to upstream). Installing runs the pack's ``fetch_cmd`` to pull it
  from source. Network required; the CLI is explicit that this reaches out.

Skill discovery in headless mode: the fleet invokes ``claude -p`` without
``--bare``, so Claude Code auto-discovers skills in ``~/.claude/skills/`` and
``<project>/.claude/skills/`` exactly as an interactive session does. Because
``--bare`` is slated to become the ``-p`` default (and would skip discovery),
the reliable long-term path is to also NAME the skill in the agent prompt (or
inline its ``SKILL.md`` body). :func:`skill_prompt_snippet` supports that. See
``docs/SKILLS.md``.

This module has no side effects on import and no network calls except inside
:func:`install_pack` when a pack's shape is ``fetch`` (and only via an injected
runner, so tests stub it). Manifest parsing uses stdlib ``tomllib`` (3.11+).
"""

from __future__ import annotations

import os
import shutil
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_SKILLS_DIR_ENV",
    "InstallResult",
    "Pack",
    "default_skills_dir",
    "install_pack",
    "installed_packs",
    "load_manifest",
    "manifest_path",
    "skill_prompt_snippet",
    "skills_root",
]

# Env override for where installed skills land. Defaults to the Claude Code
# personal skills dir. An operator can point this at a project's
# ``.claude/skills`` for project-scoped installs.
DEFAULT_SKILLS_DIR_ENV = "ALFRED_SKILLS_DIR"

# Valid install shapes.
_VENDORED = "vendored"
_FETCH = "fetch"


@dataclass(frozen=True)
class Pack:
    """One curated skill pack, parsed from a ``[[pack]]`` table.

    ``install`` is ``"vendored"`` or ``"fetch"``. For vendored packs,
    ``vendored_path`` is the subdirectory under ``skills/vendored/``. For fetch
    packs, ``fetch_cmd`` is the shell command (with ``{skills_dir}`` expanded)
    that pulls the skill from ``source`` at install time.
    """

    name: str
    summary: str
    source: str
    ref: str
    license: str
    attribution: str
    install: str
    roles: tuple[str, ...]
    vendored_path: str | None = None
    fetch_cmd: str | None = None
    reference_reason: str | None = None
    opt_in: bool = False

    @property
    def is_vendored(self) -> bool:
        return self.install == _VENDORED

    @property
    def is_fetch(self) -> bool:
        return self.install == _FETCH


def skills_root() -> Path:
    """Absolute path to the ``skills/`` directory (manifest + vendored tree).

    Two layouts are supported:

    * Source checkout: ``lib/skill_packs.py`` -> repo root -> ``skills/``.
    * Installed wheel: ``sources = ["lib"]`` flattens ``lib`` to the wheel root,
      and ``force-include`` packages ``skills`` as a sibling of this module, so
      ``<module dir>/skills`` holds the manifest.

    The packaged sibling is checked first (it only exists in a wheel), then the
    source-checkout parent. Whichever contains ``packs.toml`` wins; the source
    parent is the fallback so a fresh checkout with no build still resolves.
    """
    here = Path(__file__).resolve().parent
    packaged = here / "skills"
    if (packaged / "packs.toml").is_file():
        return packaged
    return here.parent / "skills"


def manifest_path() -> Path:
    """Path to ``skills/packs.toml``."""
    return skills_root() / "packs.toml"


def default_skills_dir() -> Path:
    """Where installs land. ``$ALFRED_SKILLS_DIR`` or ``~/.claude/skills``."""
    override = os.environ.get(DEFAULT_SKILLS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(os.path.expanduser("~/.claude/skills"))


def _coerce_roles(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(r) for r in raw)
    raise ValueError(f"roles must be a list, got {type(raw).__name__}")


def _parse_pack(table: dict[str, object]) -> Pack:
    """Build a :class:`Pack` from one manifest table, validating shape."""
    try:
        name = str(table["name"])
        install = str(table["install"])
    except KeyError as exc:
        raise ValueError(f"pack is missing required key {exc}") from exc

    if install not in (_VENDORED, _FETCH):
        raise ValueError(f"pack {name!r} has invalid install {install!r} (want vendored|fetch)")

    vendored_path = table.get("vendored_path")
    fetch_cmd = table.get("fetch_cmd")
    if install == _VENDORED and not vendored_path:
        raise ValueError(f"vendored pack {name!r} must set vendored_path")
    if install == _FETCH and not fetch_cmd:
        raise ValueError(f"fetch pack {name!r} must set fetch_cmd")

    return Pack(
        name=name,
        summary=str(table.get("summary", "")),
        source=str(table.get("source", "")),
        ref=str(table.get("ref", "")),
        license=str(table.get("license", "")),
        attribution=str(table.get("attribution", "")),
        install=install,
        roles=_coerce_roles(table.get("roles")),
        vendored_path=str(vendored_path) if vendored_path else None,
        fetch_cmd=str(fetch_cmd) if fetch_cmd else None,
        reference_reason=(
            str(table["reference_reason"]) if table.get("reference_reason") else None
        ),
        opt_in=bool(table.get("opt_in", False)),
    )


def load_manifest(path: Path | None = None) -> list[Pack]:
    """Parse the manifest into a list of :class:`Pack`.

    Raises ``FileNotFoundError`` if the manifest is missing and ``ValueError``
    on a malformed pack (duplicate name, bad install shape, missing key). The
    validation is strict so a typo fails loud at list-time, not at install-time.
    """
    path = path or manifest_path()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_packs = data.get("pack", [])
    if not isinstance(raw_packs, list):
        raise ValueError("manifest [[pack]] must be an array of tables")

    packs = [_parse_pack(t) for t in raw_packs]

    seen: set[str] = set()
    for pack in packs:
        if pack.name in seen:
            raise ValueError(f"duplicate pack name {pack.name!r} in manifest")
        seen.add(pack.name)
    return packs


def _vendored_source(pack: Pack) -> Path:
    """Absolute path to a vendored pack's source directory in this repo."""
    assert pack.vendored_path is not None
    return skills_root() / "vendored" / pack.vendored_path


@dataclass(frozen=True)
class InstallResult:
    """Outcome of one install attempt.

    ``fetched`` is the shell command a fetch pack ran (or would run in dry-run);
    ``None`` for vendored packs. ``dry_run`` records whether anything was
    actually written / executed.
    """

    pack: str
    install: str
    dest: Path
    fetched: str | None = None
    dry_run: bool = False


def install_pack(
    pack: Pack,
    *,
    skills_dir: Path | None = None,
    dry_run: bool = False,
    runner: Callable[[str, Path], int] | None = None,
) -> InstallResult:
    """Install one pack into ``skills_dir`` (default: :func:`default_skills_dir`).

    Vendored packs are copied from ``skills/vendored/<path>`` to
    ``<skills_dir>/<name>``. An existing destination is replaced (idempotent
    re-install). Fetch packs run ``fetch_cmd`` (with ``{skills_dir}`` expanded)
    via ``runner`` -- injected so tests never touch the network. The default
    runner shells out; CI always passes a stub.

    ``dry_run`` writes nothing and executes nothing; it returns the plan.
    """
    skills_dir = skills_dir or default_skills_dir()

    if pack.is_vendored:
        src = _vendored_source(pack)
        if not src.is_dir():
            raise FileNotFoundError(f"vendored source missing for {pack.name!r}: {src}")
        dest = skills_dir / pack.name
        if dry_run:
            return InstallResult(pack.name, pack.install, dest, dry_run=True)
        skills_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return InstallResult(pack.name, pack.install, dest)

    # fetch shape
    assert pack.fetch_cmd is not None
    cmd = pack.fetch_cmd.replace("{skills_dir}", str(skills_dir))
    dest = skills_dir / pack.name
    if dry_run:
        return InstallResult(pack.name, pack.install, dest, fetched=cmd, dry_run=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    run = runner or _default_shell_runner
    code = run(cmd, skills_dir)
    if code != 0:
        raise RuntimeError(f"fetch for {pack.name!r} failed (exit {code}): {cmd}")
    return InstallResult(pack.name, pack.install, dest, fetched=cmd)


def _default_shell_runner(cmd: str, cwd: Path) -> int:
    """Real shell runner for fetch packs. Only used outside tests."""
    import subprocess

    return subprocess.run(cmd, shell=True, cwd=str(cwd)).returncode


def installed_packs(packs: Sequence[Pack], *, skills_dir: Path | None = None) -> set[str]:
    """Names of packs whose skill directory exists under ``skills_dir``.

    A pack is "installed" when ``<skills_dir>/<name>`` is a directory. For
    vendored packs this is the copied skill; for gstack the setup script creates
    a ``gstack`` directory of the same name. Packs that install as a bare
    library (headroom, which has no skills dir) are never reported installed by
    this check -- they are opt-in and tracked by the operator, so a missing dir
    is not a defect.
    """
    skills_dir = skills_dir or default_skills_dir()
    present: set[str] = set()
    for pack in packs:
        if (skills_dir / pack.name).is_dir():
            present.add(pack.name)
    return present


def skill_prompt_snippet(pack: Pack, *, skills_dir: Path | None = None) -> str:
    """Return an instruction line naming this skill for a ``claude -p`` prompt.

    The reliable, ``--bare``-proof way to get a skill into a headless run is to
    name it in the prompt. This returns a one-line directive a prompt builder
    can append. Returns ``""`` for a pack that is not a skill (e.g. headroom,
    the token-compression library, which is wired at the framework layer, not
    invoked as a skill).
    """
    if pack.opt_in and not pack.is_vendored and pack.name == "headroom":
        return ""
    return (
        f"Use the `{pack.name}` skill where relevant: {pack.summary} "
        f"(installed under {(skills_dir or default_skills_dir()) / pack.name})."
    )
