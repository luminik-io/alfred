"""Tests for ``lib/roster_theme_store.py``.

The store is the single inspectable home for the active roster theme plus
operator-authored custom names, shared across the desktop and the Slack
message path. These tests cover the three things that matter: a clean
default, atomic round-trip persistence, and strict input validation on
writes paired with lenient coercion on reads.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from roster_theme_store import (
    BASE_THEME_NAMES,
    BASE_THEME_ROLES,
    DEFAULT_THEME_ID,
    PRESET_DISPLAY_NAMES,
    RosterThemeError,
    RosterThemeState,
    RosterThemeStore,
    default_theme_state,
)


def _store(tmp_path: Path) -> RosterThemeStore:
    return RosterThemeStore.from_state_root(tmp_path / "state")


def test_load_missing_file_returns_batman_default(tmp_path: Path) -> None:
    state = _store(tmp_path).load()
    assert state.theme == DEFAULT_THEME_ID == "batman"
    assert dict(state.custom_names) == {}
    assert dict(state.custom_roles) == {}


def test_default_theme_state_is_batman() -> None:
    assert default_theme_state().theme == "batman"


def test_save_preset_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = store.save(theme="transformers")
    assert saved.theme == "transformers"
    assert saved.updated_at is not None
    # A fresh load (new process simulation) reads the same value off disk.
    assert _store(tmp_path).load().theme == "transformers"


def test_save_custom_names_and_roles_persist_and_resolve(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        theme="custom",
        custom_names={"architect": "Sherlock", "fleet-doctor": "Watson"},
        custom_roles={"architect": "Lead detective"},
    )
    loaded = _store(tmp_path).load()
    assert loaded.theme == "custom"
    assert loaded.display_name_for("architect") == "Sherlock"
    assert loaded.display_name_for("fleet-doctor") == "Watson"
    assert loaded.role_label_for("architect") == "Lead detective"
    # A codename without a custom role resolves to None.
    assert loaded.role_label_for("fleet-doctor") is None


def test_dotted_codename_normalizes_to_bare_slug(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(theme="custom", custom_names={"alfred.architect": "Sherlock"})
    loaded = _store(tmp_path).load()
    assert loaded.display_name_for("architect") == "Sherlock"


def test_preset_theme_does_not_expose_custom_names(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Under a preset, the authored roster is never exposed (display_name_for is
    # None), even though the names are retained on disk for a later switch back.
    store.save(theme="custom", custom_names={"architect": "Sherlock"})
    store.save(theme="batman")
    loaded = _store(tmp_path).load()
    assert loaded.theme == "batman"
    assert loaded.display_name_for("architect") is None


def test_preset_switch_retains_custom_roster_for_later_restore(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Author a custom roster, then temporarily switch to a preset with no payload.
    store.save(
        theme="custom",
        custom_names={"architect": "Sherlock"},
        custom_roles={"architect": "Lead detective"},
    )
    store.save(theme="justice-league")

    # The preset is active and exposes nothing, but the authored roster survives on
    # disk so a restart (fresh load) does not lose it.
    reloaded = _store(tmp_path).load()
    assert reloaded.theme == "justice-league"
    assert reloaded.display_name_for("architect") is None
    assert dict(reloaded.custom_names) == {"architect": "Sherlock"}
    assert dict(reloaded.custom_roles) == {"architect": "Lead detective"}

    # Switching back to custom (no payload) restores the authored roster intact.
    store.save(theme="custom")
    restored = _store(tmp_path).load()
    assert restored.theme == "custom"
    assert restored.display_name_for("architect") == "Sherlock"
    assert restored.role_label_for("architect") == "Lead detective"


def test_explicit_custom_payload_replaces_retained_roster(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(theme="custom", custom_names={"architect": "Sherlock"})
    # An explicit (even empty) custom payload on a preset write clears the roster,
    # so the operator can deliberately discard it rather than have it linger.
    store.save(theme="batman", custom_names={}, custom_roles={})
    loaded = _store(tmp_path).load()
    assert loaded.theme == "batman"
    assert dict(loaded.custom_names) == {}


def test_custom_display_name_falls_back_to_batman_base(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(theme="custom", custom_names={"architect": "Sherlock"})
    loaded = _store(tmp_path).load()
    # The renamed agent (keyed by its role slug) uses the operator name.
    assert loaded.custom_display_name_for("architect") == "Sherlock"
    # An un-renamed known agent uses its base-theme display name, not the bare
    # slug, so the Slack path matches the desktop (which overlays on the same base).
    assert loaded.custom_display_name_for("senior-dev") == "Lucius"
    # An unknown slug has no base persona, so it returns None.
    assert loaded.custom_display_name_for("mystery-bot") is None


def test_custom_display_name_is_none_for_preset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(theme="justice-league")
    loaded = _store(tmp_path).load()
    assert loaded.custom_display_name_for("senior-dev") is None


def test_custom_role_label_falls_back_to_batman_base(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        theme="custom",
        custom_names={"architect": "Sherlock"},
        custom_roles={"architect": "Lead detective"},
    )
    loaded = _store(tmp_path).load()
    # The operator role wins when set.
    assert loaded.custom_role_label_for("architect") == "Lead detective"
    # A known agent with no custom role uses the base-theme role label, matching
    # the desktop, not the env role or None.
    assert loaded.custom_role_label_for("senior-dev") == "Senior developer"
    # An unknown slug has no base role, so it returns None and the caller
    # keeps the shipped env-role behavior.
    assert loaded.custom_role_label_for("mystery-bot") is None


def test_custom_role_label_is_none_for_preset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(theme="transformers")
    loaded = _store(tmp_path).load()
    assert loaded.custom_role_label_for("architect") is None


def test_save_rejects_unknown_theme(tmp_path: Path) -> None:
    with pytest.raises(RosterThemeError):
        _store(tmp_path).save(theme="nope")


def test_save_rejects_non_codename_key(tmp_path: Path) -> None:
    with pytest.raises(RosterThemeError):
        _store(tmp_path).save(theme="custom", custom_names={"Bad Key!": "X"})


def test_save_rejects_empty_label(tmp_path: Path) -> None:
    with pytest.raises(RosterThemeError):
        _store(tmp_path).save(theme="custom", custom_names={"architect": "   "})


def test_save_rejects_non_mapping_custom_names(tmp_path: Path) -> None:
    with pytest.raises(RosterThemeError):
        _store(tmp_path).save(theme="custom", custom_names=["not", "a", "map"])  # type: ignore[arg-type]


def test_save_rejects_too_many_entries(tmp_path: Path) -> None:
    too_many = {f"agent-{i}": f"name{i}" for i in range(200)}
    with pytest.raises(RosterThemeError):
        _store(tmp_path).save(theme="custom", custom_names=too_many)


def test_label_strips_control_chars_and_bounds_length(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(theme="custom", custom_names={"architect": "Sher\nlock" + "x" * 200})
    loaded = _store(tmp_path).load()
    name = loaded.display_name_for("architect")
    assert name is not None
    assert "\n" not in name
    assert len(name) <= 64


def test_load_drops_malformed_entries_without_raising(tmp_path: Path) -> None:
    # Hand-write a payload with junk an attacker or a stale writer might leave.
    root = tmp_path / "state" / "roster-theme"
    root.mkdir(parents=True)
    (root / "roster-theme.json").write_text(
        json.dumps(
            {
                "theme": "custom",
                "custom_names": {"architect": "Sherlock", "Not A Codename!": "x", "ok": ""},
            }
        ),
        encoding="utf-8",
    )
    loaded = RosterThemeStore.from_state_root(tmp_path / "state").load()
    assert loaded.display_name_for("architect") == "Sherlock"
    # The malformed key and the empty label are silently dropped.
    assert dict(loaded.custom_names) == {"architect": "Sherlock"}


def test_load_unknown_theme_falls_back_to_default(tmp_path: Path) -> None:
    root = tmp_path / "state" / "roster-theme"
    root.mkdir(parents=True)
    (root / "roster-theme.json").write_text(json.dumps({"theme": "garbage"}), encoding="utf-8")
    assert RosterThemeStore.from_state_root(tmp_path / "state").load().theme == "batman"


def test_preset_maps_cover_the_same_roster_as_batman_base() -> None:
    # Every preset re-skins the SAME fleet as the base theme. If a new agent is
    # added to BASE_THEME_NAMES without a matching entry in each preset, Slack
    # would render that agent's bare codename under a preset while the desktop
    # shows a themed name. Hold the codename sets identical so that cannot ship.
    base = set(BASE_THEME_NAMES)
    for theme, names in PRESET_DISPLAY_NAMES.items():
        assert set(names) == base, f"{theme} preset roster drifted from the base theme"


def test_batman_base_uses_canonical_scheduled_codenames() -> None:
    assert "cleanup" not in BASE_THEME_NAMES
    assert "cleanup" not in BASE_THEME_ROLES
    assert "agent-cleanup" in BASE_THEME_NAMES
    assert "memory-auto-promote" in BASE_THEME_NAMES
    assert "agent-morning-brief" in BASE_THEME_NAMES
    assert "shipped-summary-weekly" in BASE_THEME_NAMES


def test_themed_display_name_resolves_preset_identity() -> None:
    state = RosterThemeState(theme="transformers", custom_names={}, custom_roles={})
    # The role slug is the identity key; the preset supplies the display name.
    assert state.themed_display_name_for("senior-dev") == "Ironhide"
    assert state.themed_display_name_for("architect") == "Optimus Prime"
    # Role label comes from the base theme the presets share.
    assert state.themed_role_label_for("senior-dev") == BASE_THEME_ROLES["senior-dev"]


def test_themed_display_name_default_theme_keeps_shipped_behavior() -> None:
    state = RosterThemeState(theme="batman", custom_names={}, custom_roles={})
    # The default theme returns None so the caller keeps codename_with_role.
    assert state.themed_display_name_for("senior-dev") is None
    assert state.themed_role_label_for("senior-dev") is None


def test_themed_display_name_custom_theme_uses_custom_overlay() -> None:
    state = RosterThemeState(
        theme="custom",
        custom_names={"architect": "Sherlock"},
        custom_roles={"architect": "Lead detective"},
    )
    assert state.themed_display_name_for("architect") == "Sherlock"
    assert state.themed_role_label_for("architect") == "Lead detective"
    # An unnamed agent still resolves to its base-theme name under custom.
    assert state.themed_display_name_for("senior-dev") == BASE_THEME_NAMES["senior-dev"]


def test_themed_name_for_resolves_under_every_theme() -> None:
    # Unlike ``themed_display_name_for``, ``themed_name_for`` always returns a real
    # display name for a known slug, INCLUDING the default theme (so a
    # bare-name surface never shows the raw slug after the rename).
    default_state = RosterThemeState(theme="batman", custom_names={}, custom_roles={})
    assert default_state.themed_name_for("senior-dev") == "Lucius"
    assert default_state.themed_name_for("architect") == "Batman"

    preset = RosterThemeState(theme="transformers", custom_names={}, custom_roles={})
    assert preset.themed_name_for("senior-dev") == "Ironhide"

    custom = RosterThemeState(
        theme="custom", custom_names={"architect": "Sherlock"}, custom_roles={}
    )
    assert custom.themed_name_for("architect") == "Sherlock"
    assert custom.themed_name_for("senior-dev") == BASE_THEME_NAMES["senior-dev"]


def test_themed_name_for_unknown_codename_is_none() -> None:
    # A codename outside the known fleet has no themed name under any theme.
    for theme in ("batman", "transformers", "custom"):
        state = RosterThemeState(theme=theme, custom_names={}, custom_roles={})
        assert state.themed_name_for("release-captain") is None


def test_legacy_codename_reskinned_by_role() -> None:
    # A machine installed before the role-slug rename has Batman-cast codenames
    # (``lucius``) in agents.conf. Those are not canonical slugs, so the
    # per-codename maps miss them, but the theme layer must still re-skin them by
    # ROLE so Slack matches the desktop.
    jl = RosterThemeState(theme="justice-league", custom_names={}, custom_roles={})
    assert jl.themed_name_for("lucius") == "Superman"
    assert jl.themed_role_label_for("lucius") == "Senior developer"

    sci = RosterThemeState(theme="scientists", custom_names={}, custom_roles={})
    assert sci.themed_name_for("rasalghul") == "Curie"

    # The default theme keeps the legacy agent's base persona rather than the raw
    # slug.
    default_state = RosterThemeState(theme="batman", custom_names={}, custom_roles={})
    assert default_state.themed_name_for("lucius") == "Lucius"


def test_role_for_codename_maps_slugs_and_legacy_names() -> None:
    from roster_theme_store import role_for_codename

    assert role_for_codename("senior-dev") == "senior-dev"
    assert role_for_codename("lucius") == "senior-dev"
    assert role_for_codename("Ra's al Ghul".replace(" ", "").replace("'", "").lower()) == "reviewer"
    assert role_for_codename("fleet.local.architect") == "architect"
    assert role_for_codename("totally-unknown") is None


def test_new_builtin_themes_present_and_unique() -> None:
    from roster_theme_store import PRESET_THEME_IDS

    for theme_id in ("programmers", "scientists", "mathematicians", "philosophers"):
        assert theme_id in PRESET_THEME_IDS
        names = list(PRESET_DISPLAY_NAMES[theme_id].values())
        assert len(names) >= 12
        # No two agents share a persona within a theme.
        assert len({name.casefold() for name in names}) == len(names)


def test_new_builtin_themes_reskin_on_slack() -> None:
    # The four new themes must resolve real display names for a canonical slug so
    # a Slack post under them re-skins the roster.
    state = RosterThemeState(theme="mathematicians", custom_names={}, custom_roles={})
    assert state.themed_name_for("architect") == "Gauss"
    assert state.themed_name_for("senior-dev") == "Euler"


def test_legacy_same_role_codenames_stay_distinct() -> None:
    # Two legacy Batman-cast codenames that share a role (both ``ops``) must each
    # resolve to their OWN themed persona, never both collapse onto the role
    # pool's first name. ``Fleet doctor`` and ``Agent cleanup`` are distinct ops
    # agents in the manifest.
    jl = RosterThemeState(theme="justice-league", custom_names={}, custom_roles={})
    fleet_doctor = jl.themed_name_for("fleetdoctor")
    agent_cleanup = jl.themed_name_for("agentcleanup")
    assert fleet_doctor == "Doctor Fate"
    assert agent_cleanup == "Atom"
    assert fleet_doctor != agent_cleanup

    # Same guarantee under the default theme (each keeps its own base persona).
    batman = RosterThemeState(theme="batman", custom_names={}, custom_roles={})
    assert batman.themed_name_for("fleetdoctor") == "Fleet doctor"
    assert batman.themed_name_for("agentcleanup") == "Agent cleanup"
    assert batman.themed_name_for("fleetdoctor") != batman.themed_name_for("agentcleanup")


def test_default_theme_pairs_legacy_name_with_role_label() -> None:
    # Under the default Batman theme a known legacy codename returns a themed
    # name, so its role label must resolve too (via the derived role) rather than
    # leaving the agent with a name and no role on Slack.
    batman = RosterThemeState(theme="batman", custom_names={}, custom_roles={})
    assert batman.themed_name_for("lucius") == "Lucius"
    assert batman.themed_role_label_for("lucius") == "Senior developer"
    # An unknown codename still returns ``None`` so the caller keeps the shipped
    # env-role fallback.
    assert batman.themed_role_label_for("release-captain") is None
