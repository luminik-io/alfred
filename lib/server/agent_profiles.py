"""Public agent display profiles for Alfred surfaces.

The set of codenames Alfred knows is owned by the shared roster manifest
(``lib/roster_manifest.json``), the same file the desktop client imports for
its theme and name layer. To keep the two surfaces from drifting on which
agents exist, ``AGENT_PROFILES`` is built by iterating the manifest's codenames
and pairing each with its profile-only display data defined here (display name,
role title, purpose, theme, and stable sort order). The manifest is the single
source for membership; this module owns only the presentation fields the
manifest does not carry.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from custom_agents import CustomAgentStore

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "roster_manifest.json"


@dataclass(frozen=True)
class AgentProfile:
    codename: str
    display_name: str
    role_title: str
    purpose: str
    theme: str
    theme_label: str
    theme_accent: str
    order: int


@dataclass(frozen=True)
class _ProfileDisplay:
    """Presentation fields the roster manifest does not carry."""

    display_name: str
    role_title: str
    purpose: str
    theme: str
    theme_label: str
    theme_accent: str
    order: int


# Profile-only display data, keyed by the codename the manifest owns. Every
# manifest codename must have an entry here; ``_build_profiles`` asserts the two
# stay in lockstep at import time.
_PROFILE_DISPLAY: dict[str, _ProfileDisplay] = {
    "architect": _ProfileDisplay(
        display_name="Batman",
        role_title="Architect",
        purpose="Plans and coordinates multi-repo work with approval.",
        theme="architect",
        theme_label="Architecture",
        theme_accent="#3B82F6",
        order=10,
    ),
    "senior-dev": _ProfileDisplay(
        display_name="Lucius",
        role_title="Senior Developer",
        purpose="Ships scoped implementation issues as pull requests.",
        theme="builder",
        theme_label="Implementation",
        theme_accent="#7CE2B0",
        order=20,
    ),
    "planner": _ProfileDisplay(
        display_name="Drake",
        role_title="Planner",
        purpose="Turns specs and loose requests into implementation-ready issues.",
        theme="planner",
        theme_label="Planning",
        theme_accent="#00E5C7",
        order=30,
    ),
    "reviewer": _ProfileDisplay(
        display_name="Ras al Ghul",
        role_title="Reviewer",
        purpose="Reviews PR diffs, tests, and posts P0/P1 findings.",
        theme="reviewer",
        theme_label="Review",
        theme_accent="#A78BFA",
        order=40,
    ),
    "test-engineer": _ProfileDisplay(
        display_name="Bane",
        role_title="Test Engineer",
        purpose="Adds or strengthens tests around shipped code paths.",
        theme="quality",
        theme_label="Tests",
        theme_accent="#F59E0B",
        order=50,
    ),
    "fixer": _ProfileDisplay(
        display_name="Nightwing",
        role_title="Fixer",
        purpose="Applies high-priority review and CI feedback.",
        theme="fixer",
        theme_label="Review fixes",
        theme_accent="#8FA6C9",
        order=60,
    ),
    "triage": _ProfileDisplay(
        display_name="Robin",
        role_title="Bug Triage",
        purpose="Labels and scopes bug reports for the fleet.",
        theme="triage",
        theme_label="Triage",
        theme_accent="#F87171",
        order=70,
    ),
    "spec-planner": _ProfileDisplay(
        display_name="Damian",
        role_title="Spec Planner",
        purpose="Plans spec-level bundles before implementation starts.",
        theme="planner",
        theme_label="Spec planning",
        theme_accent="#14B8A6",
        order=80,
    ),
    "e2e-runner": _ProfileDisplay(
        display_name="Huntress",
        role_title="QA Runner",
        purpose="Runs end-to-end smoke checks and reports failures.",
        theme="qa",
        theme_label="QA",
        theme_accent="#EC4899",
        order=90,
    ),
    "ops-watch": _ProfileDisplay(
        display_name="Gordon",
        role_title="Ops Watch",
        purpose="Checks uptime, incidents, and operational health.",
        theme="ops",
        theme_label="Operations",
        theme_accent="#38BDF8",
        order=100,
    ),
    "automerge": _ProfileDisplay(
        display_name="Automerge",
        role_title="Merge Sweeper",
        purpose="Merges approved low-risk PRs when policy allows.",
        theme="release",
        theme_label="Release",
        theme_accent="#22C55E",
        order=110,
    ),
    "agent-cleanup": _ProfileDisplay(
        display_name="Agent Cleanup",
        role_title="Workspace Janitor",
        purpose="Sweeps stale worktrees and local branch leftovers.",
        theme="ops",
        theme_label="Cleanup",
        theme_accent="#94A3B8",
        order=120,
    ),
    "memory-harvest": _ProfileDisplay(
        display_name="Memory Harvest",
        role_title="Memory Curator",
        purpose="Queues repeated lessons for review before recall.",
        theme="memory",
        theme_label="Memory",
        theme_accent="#C084FC",
        order=130,
    ),
    "memory-auto-promote": _ProfileDisplay(
        display_name="Memory Auto-Promote",
        role_title="Memory Judge",
        purpose="Promotes high-confidence repeated lessons into recall.",
        theme="memory",
        theme_label="Memory",
        theme_accent="#C084FC",
        order=135,
    ),
    "fleet-doctor": _ProfileDisplay(
        display_name="Fleet Doctor",
        role_title="Health Check",
        purpose="Reports fleet health, pauses, locks, and runner gates.",
        theme="ops",
        theme_label="Health",
        theme_accent="#60A5FA",
        order=140,
    ),
    "code-map-refresh": _ProfileDisplay(
        display_name="Code Map",
        role_title="Repo Indexer",
        purpose="Refreshes repo maps for planners and reviewers.",
        theme="indexing",
        theme_label="Indexing",
        theme_accent="#FBBF24",
        order=150,
    ),
    "agent-morning-brief": _ProfileDisplay(
        display_name="Morning Brief",
        role_title="Daily Brief",
        purpose="Prepares the operator's morning fleet summary.",
        theme="ops",
        theme_label="Briefing",
        theme_accent="#38BDF8",
        order=155,
    ),
    "fleet-recap-morning": _ProfileDisplay(
        display_name="Fleet Recap Morning",
        role_title="Fleet Recap",
        purpose="Publishes the morning activity recap.",
        theme="ops",
        theme_label="Recap",
        theme_accent="#60A5FA",
        order=170,
    ),
    "fleet-recap-evening": _ProfileDisplay(
        display_name="Fleet Recap Evening",
        role_title="Fleet Recap",
        purpose="Publishes the evening activity recap.",
        theme="ops",
        theme_label="Recap",
        theme_accent="#60A5FA",
        order=171,
    ),
    "shipped-summary-daily": _ProfileDisplay(
        display_name="Shipped Summary Daily",
        role_title="Shipping Digest",
        purpose="Summarizes merged work for the daily shipped board.",
        theme="release",
        theme_label="Shipped",
        theme_accent="#22C55E",
        order=180,
    ),
    "shipped-summary-weekly": _ProfileDisplay(
        display_name="Shipped Summary Weekly",
        role_title="Shipping Digest",
        purpose="Summarizes merged work for the weekly shipped board.",
        theme="release",
        theme_label="Shipped",
        theme_accent="#22C55E",
        order=181,
    ),
    "proof-telemetry": _ProfileDisplay(
        display_name="Proof Telemetry",
        role_title="Impact Reporter",
        purpose="Sends anonymous aggregate usage totals when configured.",
        theme="impact",
        theme_label="Impact",
        theme_accent="#00E5C7",
        order=190,
    ),
}


def _manifest_codenames() -> tuple[str, ...]:
    """Read the codename roster from the shared manifest."""
    payload = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return tuple(agent["codename"] for agent in payload["agents"])


def _build_profiles() -> tuple[AgentProfile, ...]:
    """Pair each manifest codename with its local profile display data.

    The manifest owns the codename set; ``_PROFILE_DISPLAY`` owns the
    presentation fields. Any drift between the two (a codename in one but not
    the other) is a hard import-time error, so the desktop and server surfaces
    cannot silently disagree on which agents exist.
    """
    manifest = _manifest_codenames()
    manifest_set = set(manifest)
    profile_set = set(_PROFILE_DISPLAY)
    missing_display = manifest_set - profile_set
    if missing_display:
        raise RuntimeError(
            "roster manifest codenames without a display profile: "
            + ", ".join(sorted(missing_display))
        )
    extra_display = profile_set - manifest_set
    if extra_display:
        raise RuntimeError(
            "display profiles without a roster manifest codename: "
            + ", ".join(sorted(extra_display))
        )
    profiles = [
        AgentProfile(codename=codename, **asdict(_PROFILE_DISPLAY[codename]))
        for codename in manifest
    ]
    # Preserve the historical roster ordering (Batman, Lucius, Drake first),
    # which the profiles own via ``order`` rather than manifest position.
    profiles.sort(key=lambda profile: profile.order)
    return tuple(profiles)


AGENT_PROFILES: tuple[AgentProfile, ...] = _build_profiles()

_PROFILE_BY_CODENAME = {profile.codename: profile for profile in AGENT_PROFILES}
_UNKNOWN_ORDER = 10_000
_CUSTOM_ORDER = 9_000


def agent_profile(codename: str) -> AgentProfile | None:
    """Return the public display profile for a codename, if Alfred knows it."""
    return _PROFILE_BY_CODENAME.get(_normalize_codename(codename))


def profile_payload(codename: str, *, state_root: Path | None = None) -> dict[str, Any]:
    """Return serializable display metadata for a codename."""
    profile = agent_profile(codename)
    if profile is None:
        custom = _custom_agent(codename, state_root=state_root)
        return custom.profile_payload() if custom is not None else {}
    payload = asdict(profile)
    payload.pop("codename", None)
    payload.pop("order", None)
    return payload


def profile_order(codename: str, *, state_root: Path | None = None) -> int:
    """Stable fleet display order with Batman, Lucius, and Drake first."""
    profile = agent_profile(codename)
    if profile is not None:
        return profile.order
    custom = _custom_agent(codename, state_root=state_root)
    if custom is not None:
        return _CUSTOM_ORDER
    return _UNKNOWN_ORDER


def sort_codenames(codenames: list[str], *, state_root: Path | None = None) -> list[str]:
    """Sort codenames by public roster order, then alphabetically."""
    return sorted(
        codenames,
        key=lambda codename: (profile_order(codename, state_root=state_root), codename),
    )


def _normalize_codename(codename: str) -> str:
    return codename.rsplit(".", 1)[-1].strip().lower()


def _custom_agent(codename: str, *, state_root: Path | None = None):
    try:
        store = (
            CustomAgentStore.from_state_root(state_root)
            if state_root is not None
            else CustomAgentStore.from_env()
        )
        return store.get(codename)
    except Exception:
        return None
