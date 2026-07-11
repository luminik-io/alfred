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

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
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
    "starter_packs",
]

# Env override for where installed skills land. Defaults to the Claude Code
# personal skills dir. An operator can point this at a project's
# ``.claude/skills`` for project-scoped installs.
DEFAULT_SKILLS_DIR_ENV = "ALFRED_SKILLS_DIR"
FETCH_PACK_TIMEOUT_S = 600

# Valid install shapes.
_VENDORED = "vendored"
_FETCH = "fetch"
# first_party: an Alfred-authored MIT skill living in ``skills/first_party/<name>/``.
# It installs exactly like a vendored pack (a local copytree, no network), but it
# is our own source rather than a copied upstream, so it carries no upstream
# attribution and can be flagged as part of the default "starter" set.
_FIRST_PARTY = "first_party"


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
    first_party_path: str | None = None
    reference_reason: str | None = None
    opt_in: bool = False
    default_install: bool = False

    @property
    def is_vendored(self) -> bool:
        return self.install == _VENDORED

    @property
    def is_fetch(self) -> bool:
        return self.install == _FETCH

    @property
    def is_first_party(self) -> bool:
        return self.install == _FIRST_PARTY

    @property
    def is_local_copy(self) -> bool:
        """True when installing this pack is a local copytree (no network).

        Both vendored and first-party packs copy a directory from this repo into
        the skills dir; the fetch shape is the only one that reaches the network.
        """
        return self.is_vendored or self.is_first_party


def skills_root() -> Path:
    """Absolute path to the ``skills/`` directory (manifest + vendored tree).

    Three layouts are supported:

    * Source checkout: ``lib/skill_packs.py`` -> repo root -> ``skills/``.
    * Deployed runtime: ``deploy.sh`` copies ``lib/`` to ``$ALFRED_HOME/lib``
      and ``skills/`` to ``$ALFRED_HOME/skills``, so the same parent-relative
      walk resolves (``$ALFRED_HOME/lib/skill_packs.py`` ->
      ``$ALFRED_HOME/skills``).
    * Installed wheel: ``sources = ["lib"]`` flattens ``lib`` to the wheel root,
      and ``force-include`` packages ``skills`` as a sibling of this module, so
      ``<module dir>/skills`` holds the manifest.

    The packaged sibling is checked first (it only exists in a wheel), then the
    parent (source checkout and deployed runtime). Whichever contains
    ``packs.toml`` wins; the parent is the fallback so a fresh checkout with no
    build still resolves.
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

    if install not in (_VENDORED, _FETCH, _FIRST_PARTY):
        raise ValueError(
            f"pack {name!r} has invalid install {install!r} (want vendored|fetch|first_party)"
        )

    vendored_path = table.get("vendored_path")
    fetch_cmd = table.get("fetch_cmd")
    first_party_path = table.get("first_party_path")
    if install == _VENDORED and not vendored_path:
        raise ValueError(f"vendored pack {name!r} must set vendored_path")
    if install == _FETCH and not fetch_cmd:
        raise ValueError(f"fetch pack {name!r} must set fetch_cmd")
    if install == _FIRST_PARTY and not first_party_path:
        raise ValueError(f"first_party pack {name!r} must set first_party_path")

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
        first_party_path=str(first_party_path) if first_party_path else None,
        reference_reason=(
            str(table["reference_reason"]) if table.get("reference_reason") else None
        ),
        opt_in=bool(table.get("opt_in", False)),
        default_install=bool(table.get("default_install", False)),
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


def _first_party_source(pack: Pack) -> Path:
    """Absolute path to a first-party pack's source directory in this repo."""
    assert pack.first_party_path is not None
    return skills_root() / "first_party" / pack.first_party_path


def _local_source(pack: Pack) -> Path:
    """Source directory for a local-copy pack (vendored or first-party)."""
    if pack.is_first_party:
        return _first_party_source(pack)
    return _vendored_source(pack)


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

    if pack.is_local_copy:
        src = _local_source(pack)
        if not src.is_dir():
            shape = "first-party" if pack.is_first_party else "vendored"
            raise FileNotFoundError(f"{shape} source missing for {pack.name!r}: {src}")
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
    # Shell-quote the expanded path: fetch commands run with shell=True, so an
    # unquoted ALFRED_SKILLS_DIR containing spaces or shell metacharacters
    # would be word-split (or interpreted as shell syntax) by the shell.
    # Quoting the whole segment keeps `{skills_dir}/gstack` valid too: the
    # shell concatenates the quoted path with the literal suffix.
    cmd = pack.fetch_cmd.replace("{skills_dir}", shlex.quote(str(skills_dir)))
    dest = skills_dir / pack.name
    if dry_run:
        return InstallResult(pack.name, pack.install, dest, fetched=cmd, dry_run=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    # Path.exists() follows symlinks, so a BROKEN symlink reports False and
    # would skip the backup; a failed reinstall would then delete the pack's
    # symlink reference outright. Treat any symlink as an existing install.
    dest_existed = dest.exists() or dest.is_symlink()
    backup_root: Path | None = None
    backup_dest: Path | None = None
    if dest_existed:
        backup_root = Path(tempfile.mkdtemp(prefix=f".{pack.name}-backup-", dir=skills_dir))
        backup_dest = backup_root / pack.name
        if dest.is_symlink():
            shutil.copy2(dest, backup_dest, follow_symlinks=False)
        elif dest.is_dir():
            shutil.copytree(dest, backup_dest, symlinks=True)
        else:
            shutil.copy2(dest, backup_dest, follow_symlinks=False)
    entries_before = {entry.name for entry in skills_dir.iterdir()}
    run = runner or _default_shell_runner
    try:
        code = run(cmd, skills_dir)
    except BaseException:
        _remove_new_partial_install(
            dest,
            skills_dir=skills_dir,
            entries_before=entries_before,
            dest_existed=dest_existed,
        )
        _restore_existing_destination(dest, backup_dest)
        if backup_root:
            shutil.rmtree(backup_root, ignore_errors=True)
        raise
    if code != 0:
        _remove_new_partial_install(
            dest,
            skills_dir=skills_dir,
            entries_before=entries_before,
            dest_existed=dest_existed,
        )
        _restore_existing_destination(dest, backup_dest)
        if backup_root:
            shutil.rmtree(backup_root, ignore_errors=True)
        raise RuntimeError(f"fetch for {pack.name!r} failed (exit {code}): {cmd}")
    if backup_root:
        shutil.rmtree(backup_root, ignore_errors=True)
    return InstallResult(pack.name, pack.install, dest, fetched=cmd)


def _restore_existing_destination(dest: Path, backup: Path | None) -> None:
    if backup is None:
        return
    if dest.is_dir() and not dest.is_symlink():
        shutil.rmtree(dest)
    elif dest.exists() or dest.is_symlink():
        dest.unlink()
    if backup.is_dir() and not backup.is_symlink():
        shutil.copytree(backup, dest, symlinks=True)
    else:
        shutil.copy2(backup, dest, follow_symlinks=False)


def _remove_new_partial_install(
    dest: Path,
    *,
    skills_dir: Path,
    entries_before: set[str],
    dest_existed: bool,
) -> None:
    """Roll back paths a failed fetch published from its new destination."""
    dest_path = Path(os.path.abspath(dest))
    for entry in skills_dir.iterdir():
        if entry.name in entries_before or not entry.is_symlink():
            continue
        target = Path(os.readlink(entry))
        if not target.is_absolute():
            target = entry.parent / target
        target_path = Path(os.path.abspath(target))
        if target_path == dest_path or dest_path in target_path.parents:
            entry.unlink()

    if dest_existed or not dest.exists():
        return
    if dest.is_dir():
        shutil.rmtree(dest)
    else:
        dest.unlink()


def _default_shell_runner(cmd: str, cwd: Path) -> int:
    """Real shell runner for fetch packs. Only used outside tests."""
    process = subprocess.Popen(cmd, shell=True, cwd=str(cwd), start_new_session=True)

    def _terminate_on_signal(signum: int, _frame) -> None:
        _terminate_process_group(process)
        raise SystemExit(128 + signum)

    previous_handlers = {
        signum: signal.signal(signum, _terminate_on_signal)
        for signum in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        return process.wait(timeout=FETCH_PACK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return 124
    except KeyboardInterrupt:
        _terminate_process_group(process)
        raise
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def _terminate_process_group(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


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


def starter_packs(packs: Sequence[Pack]) -> list[Pack]:
    """The default first-party set: local-copy packs flagged ``default_install``.

    This is what ``alfred skills install --starter`` installs. It is limited to
    local-copy shapes (no network) so a starter install is deterministic and
    offline: a fetch pack is never pulled in implicitly.
    """
    return [p for p in packs if p.default_install and p.is_local_copy]


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
