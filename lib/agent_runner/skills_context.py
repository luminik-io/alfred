"""Runner skill-injector: metadata-only progressive disclosure of skills.

The `alfred skills` CLI installs skills; this module makes a firing *aware* of
them without paying their full token cost. It reads ONLY the YAML frontmatter of
each ``SKILL.md`` (``name`` + ``description``), filters that set to the ones
recommended for the firing's role, and renders a compact block naming each skill
and the path to its body. The agent reads the full ``SKILL.md`` on demand when a
trigger matches -- progressive disclosure, mirroring how Claude Code itself
surfaces skills.

Why frontmatter-only: a ``SKILL.md`` body can be large (rules, examples,
procedures). Inlining every installed skill's body into every prompt would be
wasteful and would drown the task. The description doubles as the trigger (per
the SKILL.md convention), so name + description is exactly enough for the model
to decide whether to open the body.

Safety and cost bounds:

* Each file is read with a hard size cap (:data:`MAX_SKILL_FILE_SIZE`, mirroring
  deepagents) so a pathological or hostile file cannot blow up memory.
* Only the frontmatter block is parsed; the body is never loaded here.
* Parsing is a tiny hand-rolled ``key: value`` reader over the ``---`` fenced
  block (no YAML dependency, matching the repo's zero-new-dependency rule). It
  handles the two fields SKILL.md guarantees; anything else is ignored.

This module has no side effects on import and no network calls. It is wired into
prompt assembly by :mod:`agent_runner.process`, gated by ``ALFRED_SKILLS_INJECT``
(default on), so a fleet that does not want skill injection runs unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MAX_SKILL_FILE_SIZE",
    "SKILLS_INJECT_ENV",
    "SkillMeta",
    "discover_skills",
    "render_skills_block",
    "skills_context_for_role",
    "skills_for_role",
]

# Hard cap on how many bytes of a SKILL.md we read to parse frontmatter. Mirrors
# deepagents' MAX_SKILL_FILE_SIZE (10 MB). Frontmatter is tiny; the cap only
# exists to bound a pathological or hostile file. We read at most this many
# bytes and parse the frontmatter out of the head.
MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024

# Default-ON env gate, mirroring the ALFRED_*_MCP convention in process.py.
# Unset or truthy -> inject; explicitly falsy (0/false/no/off) -> skip.
SKILLS_INJECT_ENV = "ALFRED_SKILLS_INJECT"

_FRONTMATTER_FENCE = "---"


@dataclass(frozen=True)
class SkillMeta:
    """Frontmatter-only view of one installed skill.

    ``path`` is the ``SKILL.md`` the agent reads on demand for the full body.
    ``description`` doubles as the trigger (SKILL.md convention).
    """

    name: str
    description: str
    path: Path


def _parse_frontmatter(head: str) -> dict[str, str]:
    """Parse the leading ``---`` fenced ``key: value`` block from ``head``.

    Returns a dict of the flat scalar keys found. This is intentionally minimal:
    SKILL.md frontmatter is a flat mapping whose two guaranteed keys are ``name``
    and ``description``, both single-line scalars. We do not pull in a YAML
    dependency (repo rule: zero new third-party deps). Nested/list values and
    anything after the closing fence are ignored.
    """
    lines = head.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == _FRONTMATTER_FENCE:
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not key or key != key.lstrip():
            # Skip indented continuation / nested keys; we only want top-level.
            continue
        value = value.strip()
        # Strip a single layer of matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fields[key] = value
    return fields


def _read_skill_meta(skill_md: Path) -> SkillMeta | None:
    """Read one ``SKILL.md`` and return its frontmatter view, or ``None``.

    Reads at most :data:`MAX_SKILL_FILE_SIZE` bytes and parses only the
    frontmatter. Returns ``None`` when the file is unreadable or has no
    ``name``/``description`` frontmatter (not a valid skill for injection).
    """
    try:
        with skill_md.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(MAX_SKILL_FILE_SIZE)
    except OSError:
        return None
    fields = _parse_frontmatter(head)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name or not description:
        return None
    return SkillMeta(name=name, description=description, path=skill_md)


def discover_skills(dirs: Iterable[Path]) -> list[SkillMeta]:
    """Discover installed skills across ``dirs`` by frontmatter only.

    For each directory, finds every ``<dir>/<skill>/SKILL.md`` and parses its
    frontmatter. Missing directories are skipped silently. Results are
    deduplicated by skill ``name`` (first occurrence wins, so an earlier dir in
    the list shadows a later one) and returned sorted by name for stable output.
    """
    seen: dict[str, SkillMeta] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for skill_md in sorted(directory.glob("*/SKILL.md")):
            meta = _read_skill_meta(skill_md)
            if meta is None or meta.name in seen:
                continue
            seen[meta.name] = meta
    return sorted(seen.values(), key=lambda m: m.name)


def _roles_by_skill_name() -> dict[str, tuple[str, ...]]:
    """Map each manifest pack name to its recommended roles.

    Best-effort: if the manifest cannot be loaded (missing in some layouts), an
    empty map is returned and :func:`skills_for_role` degrades to "no role
    filter data", which yields an empty selection rather than raising.
    """
    try:
        import skill_packs
    except Exception:
        return {}
    try:
        packs = skill_packs.load_manifest()
    except Exception:
        return {}
    return {p.name: tuple(p.roles) for p in packs}


def skills_for_role(
    role: str | None,
    metas: Sequence[SkillMeta],
    *,
    roles_by_name: dict[str, tuple[str, ...]] | None = None,
) -> list[SkillMeta]:
    """Filter ``metas`` to the skills recommended for ``role`` in the manifest.

    A skill is offered to a firing when the firing's ``role`` is listed in the
    skill's manifest ``roles``. A skill with no manifest entry, or the manifest
    entry having empty roles, is never auto-offered to a role (opt-in by design;
    an operator can still install and name it explicitly). A ``None`` or empty
    role yields an empty list -- injection is role-scoped.

    ``roles_by_name`` is an injection seam for tests; when omitted it is read
    from the shipped manifest.
    """
    if not role:
        return []
    mapping = roles_by_name if roles_by_name is not None else _roles_by_skill_name()
    return [m for m in metas if role in mapping.get(m.name, ())]


def render_skills_block(metas: Sequence[SkillMeta]) -> str:
    """Render a compact, metadata-only skills block for a prompt.

    Lists each skill's name, its description (the trigger), and the path to its
    ``SKILL.md`` so the agent reads the body on demand. Returns ``""`` for an
    empty selection so the caller can append unconditionally without emitting a
    dangling header (behavior-preserving when no skills match).
    """
    if not metas:
        return ""
    lines = [
        "Available skills (invoke by name when the trigger matches; "
        "read the SKILL.md for the full procedure before you rely on it):"
    ]
    for meta in metas:
        lines.append(f"- {meta.name}: {meta.description} [read: {meta.path}]")
    return "\n".join(lines)


def _skills_inject_enabled(env: dict[str, str] | None = None) -> bool:
    """True unless ``ALFRED_SKILLS_INJECT`` is explicitly falsy (default on)."""
    envmap = env if env is not None else os.environ
    val = envmap.get(SKILLS_INJECT_ENV)
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off", ""}


def _default_skill_dirs() -> list[Path]:
    """Directories to scan for installed skills, in precedence order.

    ``ALFRED_SKILLS_DIR`` (or the Claude personal skills dir) first, then a
    project-local ``.claude/skills`` under the current working directory. The
    first occurrence of a skill name wins, so an explicitly configured dir
    shadows the project dir.
    """
    dirs: list[Path] = []
    try:
        import skill_packs

        dirs.append(skill_packs.default_skills_dir())
    except Exception:
        pass
    dirs.append(Path.cwd() / ".claude" / "skills")
    return dirs


def skills_context_for_role(
    role: str | None,
    *,
    dirs: Iterable[Path] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Return the ready-to-append skills block for a firing of ``role``.

    This is the single entry point the runner calls. It:

    1. no-ops (returns ``""``) when injection is disabled or ``role`` is unset;
    2. discovers installed skills by frontmatter only (bounded reads);
    3. filters to the role via the manifest;
    4. renders the compact block (``""`` when nothing matches).

    Returning ``""`` in every empty case keeps the runner change behavior-
    preserving: appending an empty string leaves the prompt untouched.
    """
    if not role or not _skills_inject_enabled(env):
        return ""
    scan_dirs = list(dirs) if dirs is not None else _default_skill_dirs()
    metas = discover_skills(scan_dirs)
    selected = skills_for_role(role, metas)
    return render_skills_block(selected)
