"""Tests for ``lib/server/agent_profiles.py``.

The public agent roster is owned by ``lib/roster_manifest.json``, the shared
manifest the desktop client also imports. ``AGENT_PROFILES`` derives its
codename set from that manifest and pairs each codename with local display
data. These tests pin that contract so the two surfaces cannot drift on which
agents exist.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from server.agent_profiles import _PROFILE_DISPLAY, AGENT_PROFILES

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "lib" / "roster_manifest.json"


def _manifest_codenames() -> set[str]:
    payload = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {agent["codename"] for agent in payload["agents"]}


def test_profiles_and_manifest_codenames_match() -> None:
    manifest = _manifest_codenames()
    profiles = {profile.codename for profile in AGENT_PROFILES}
    assert profiles == manifest


def test_local_display_data_matches_manifest_codenames() -> None:
    assert set(_PROFILE_DISPLAY) == _manifest_codenames()


def test_profiles_expose_every_codename_once() -> None:
    codenames = [profile.codename for profile in AGENT_PROFILES]
    assert len(codenames) == len(set(codenames))


def test_profiles_sorted_by_order() -> None:
    orders = [profile.order for profile in AGENT_PROFILES]
    assert orders == sorted(orders)
    # Architect, Senior Dev, and Planner stay first regardless of manifest position.
    assert [p.codename for p in AGENT_PROFILES[:3]] == ["architect", "senior-dev", "planner"]
