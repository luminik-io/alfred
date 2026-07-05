"""Codename -> skill-pack role lookup, importable from ``lib`` without a cycle.

The fleet's canonical codename-to-role catalog lives in ``bin/alfred-init.py``
(``AGENT_CATALOG`` / ``CODENAME_TO_ROLE``), but ``bin/alfred-init.py`` is an
executable init script, not an importable package module, so ``lib`` code cannot
import it without dragging in the whole CLI. This module is the small,
dependency-free lookup ``lib`` needs: it maps each engineering codename to the
role identifier used by the skill-pack manifest (``skills/packs.toml``), so the
runner can derive a firing's role from its codename and inject role-scoped
skills for every existing caller (none of which pass an explicit role).

Two role vocabularies exist and must not be confused:

* The catalog role ids in ``bin/alfred-init.py`` use underscores
  (``feature_dev``, ``pr_review``).
* The skill-pack ``roles`` in ``skills/packs.toml`` use hyphens (``feature-dev``,
  ``pr-review``, ``deploy-monitor``, ``e2e-smoke``, ...).

This module returns the *pack-role* (hyphenated) vocabulary, because that is what
:func:`agent_runner.skills_context.skills_for_role` filters against. The mapping
below is kept deliberately explicit (not a mechanical underscore->hyphen swap)
because a few catalog roles map onto a differently named pack role
(``ci_repair`` -> ``review-fix``, ``smoke_runner`` -> ``e2e-smoke``,
``ops_morning`` -> ``deploy-monitor``). Codenames with no engineering skill role
(automerge, memory-harvest, fleet-recap, ...) are simply absent and resolve to
``None`` -- no skills are injected for them, which is correct.

If ``bin/alfred-init.py`` gains or renames an engineering role, update this map
in the same change; the test suite pins the overlap so a divergence fails loud.
"""

from __future__ import annotations

__all__ = ["CODENAME_TO_PACK_ROLE", "pack_role_for_codename"]

# codename -> skill-pack role (hyphenated, matching skills/packs.toml `roles`).
# Only the engineering codenames that map to a skill-relevant role are listed;
# operational codenames (automerge, memory-*, fleet-*, code-map-refresh, ...)
# are intentionally omitted so they inject nothing.
CODENAME_TO_PACK_ROLE: dict[str, str] = {
    "senior-dev": "feature-dev",
    "planner": "planner",
    "test-engineer": "test-coverage",
    "reviewer": "pr-review",
    "fixer": "review-fix",
    "triage": "bug-triage",
    "ops-watch": "deploy-monitor",
    "e2e-runner": "e2e-smoke",
}


def pack_role_for_codename(codename: str | None) -> str | None:
    """Return the skill-pack role for ``codename``, or ``None`` if it has none.

    Case-insensitive on the codename. Returns ``None`` for an empty codename or
    one with no engineering skill role (operational agents), so the caller
    injects no skills for it.
    """
    if not codename:
        return None
    return CODENAME_TO_PACK_ROLE.get(codename.strip().lower())
