"""Tests for the codename -> skill-pack role lookup (`lib/agent_roster.py`).

Beyond the basic lookup, this pins two invariants that would silently break skill
injection if they drifted:

1. Every pack role the roster maps to is a real role in ``skills/packs.toml``.
2. The roster stays consistent with the fleet's canonical
   ``CODENAME_TO_ROLE`` in ``bin/alfred-init.py`` (allowing for the
   underscore -> hyphen and a few renamed roles), so adding or renaming an
   engineering role there without updating the roster fails loud here.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import agent_roster  # noqa: E402
import skill_packs  # noqa: E402


def test_lookup_maps_known_codenames() -> None:
    assert agent_roster.pack_role_for_codename("senior-dev") == "feature-dev"
    assert agent_roster.pack_role_for_codename("planner") == "planner"
    assert agent_roster.pack_role_for_codename("test-engineer") == "test-coverage"
    assert agent_roster.pack_role_for_codename("reviewer") == "pr-review"


def test_lookup_is_case_insensitive_and_trims() -> None:
    assert agent_roster.pack_role_for_codename("  Senior-Dev  ") == "feature-dev"


def test_lookup_unknown_or_empty_is_none() -> None:
    assert agent_roster.pack_role_for_codename("automerge") is None
    assert agent_roster.pack_role_for_codename("") is None
    assert agent_roster.pack_role_for_codename(None) is None


def test_every_mapped_role_exists_in_the_manifest() -> None:
    manifest_roles = {r for p in skill_packs.load_manifest() for r in p.roles}
    # Roster targets must be roles some pack actually declares, or injection for
    # that codename would always be empty.
    for codename, role in agent_roster.CODENAME_TO_PACK_ROLE.items():
        assert role in manifest_roles, (
            f"{codename} maps to {role!r}, which no pack declares in packs.toml"
        )


def _catalog_codename_to_role() -> dict[str, str]:
    """Parse ``AGENT_CATALOG`` from bin/alfred-init.py without importing it.

    bin/alfred-init.py is an executable script, not an importable module, so we
    read its ``AGENT_CATALOG`` literal statically. Returns codename -> catalog
    role (the underscored vocabulary).
    """
    src = (REPO_ROOT / "bin" / "alfred-init.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        target_name = None
        value = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    target_name = t.id
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            # AGENT_CATALOG is annotated (`AGENT_CATALOG: dict[...] = {...}`).
            target_name = node.target.id
            value = node.value
        if target_name == "AGENT_CATALOG" and value is not None:
            catalog = ast.literal_eval(value)
            return {entry[0]: role for role, entry in catalog.items()}
    raise AssertionError("AGENT_CATALOG not found in bin/alfred-init.py")


def test_roster_agrees_with_the_canonical_catalog() -> None:
    """Each roster codename must map to the catalog's role for that codename.

    The catalog role is underscored (feature_dev); the roster is hyphenated
    (feature-dev) and renames a few (ci_repair -> review-fix, smoke_runner ->
    e2e-smoke, ops_morning -> deploy-monitor). We assert consistency modulo those
    known renames, so a codename pointed at the wrong role is caught.
    """
    catalog = _catalog_codename_to_role()
    renamed = {
        "ci_repair": "review-fix",
        "smoke_runner": "e2e-smoke",
        "ops_morning": "deploy-monitor",
    }
    for codename, pack_role in agent_roster.CODENAME_TO_PACK_ROLE.items():
        assert codename in catalog, f"{codename} is not a known catalog codename"
        catalog_role = catalog[codename]
        expected = renamed.get(catalog_role, catalog_role.replace("_", "-"))
        assert pack_role == expected, (
            f"{codename}: roster says {pack_role!r} but catalog role is "
            f"{catalog_role!r} (expected {expected!r})"
        )


def test_catalog_parse_sanity() -> None:
    """The static parser actually found the engineering codenames it should."""
    catalog = _catalog_codename_to_role()
    assert catalog.get("senior-dev") == "feature_dev"
    assert re.match(r"^[a-z_]+$", catalog["senior-dev"])  # underscored vocabulary
