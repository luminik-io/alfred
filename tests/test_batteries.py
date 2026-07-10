"""Tests for the shared battery manifest (lib/batteries.py).

Covers manifest correctness, the truthful built-in vs opt-in split, status
computation, and the idempotent .env writer that the CLI and wizard share.
Alfred must remain fully functional with ZERO batteries, so the default env is
exercised explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import batteries  # noqa: E402

VALID_CATEGORIES = {
    batteries.CATEGORY_MEMORY,
    batteries.CATEGORY_COMPRESSION,
    batteries.CATEGORY_CODE_GRAPH,
}


# --------------------------------------------------------------------------- #
# Manifest shape
# --------------------------------------------------------------------------- #
def test_battery_ids_unique() -> None:
    ids = [b.id for b in batteries.BATTERIES]
    assert len(ids) == len(set(ids))


def test_every_battery_has_truthful_help_and_valid_category() -> None:
    for battery in batteries.BATTERIES:
        assert battery.category in VALID_CATEGORIES, battery.id
        assert battery.what.strip(), battery.id
        assert battery.how_it_helps.strip(), battery.id
        # No em-dashes or en-dashes anywhere in operator-facing copy.
        for dash in ("\u2014", "\u2013"):  # em-dash, en-dash
            assert dash not in battery.what, battery.id
            assert dash not in battery.how_it_helps, battery.id


def test_builtins_are_always_on_with_no_install() -> None:
    builtins = batteries.builtin_batteries()
    # The four documented always-on built-ins.
    assert {b.id for b in builtins} == {
        "sqlite-memory",
        "tool-compactor",
        "skeleton-reads",
        "blast-radius",
    }
    for battery in builtins:
        assert battery.default_on is True
        assert battery.install_kind == batteries.INSTALL_INCLUDED
        assert not battery.enable_env
        assert not battery.disable_env
        assert battery.requires_daemon is False


def test_opt_ins_are_off_by_default_and_declare_enable_disable() -> None:
    opt_ins = batteries.opt_in_batteries()
    assert {b.id for b in opt_ins} == {
        "dense-embeddings",
        "headroom-compression",
        "code-memory-mcp",
        "redis-ams",
        "pgvector",
    }
    for battery in opt_ins:
        assert battery.default_on is False
        assert battery.enable_env, battery.id
        assert battery.disable_env, battery.id
        assert battery.install_kind != batteries.INSTALL_INCLUDED, battery.id


def test_daemon_batteries_are_flagged_with_a_service() -> None:
    for bid in ("redis-ams", "pgvector"):
        battery = batteries.battery_by_id(bid)
        assert battery is not None
        assert battery.requires_daemon is True
        assert battery.service in {"Redis", "Postgres"}
        assert battery.install_kind == batteries.INSTALL_DAEMON


def test_pip_extras_match_pyproject() -> None:
    dense = batteries.battery_by_id("dense-embeddings")
    pg = batteries.battery_by_id("pgvector")
    assert dense is not None and dense.pip_extra == "vector"
    assert pg is not None and pg.pip_extra == "pgvector"


def test_managed_env_keys_cover_all_enable_and_disable_keys() -> None:
    keys = batteries.managed_env_keys()
    for battery in batteries.BATTERIES:
        assert set(battery.enable_env) <= keys, battery.id
        assert set(battery.disable_env) <= keys, battery.id


# --------------------------------------------------------------------------- #
# Zero batteries: Alfred is fully functional with only built-ins
# --------------------------------------------------------------------------- #
def test_manifest_with_zero_batteries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No AMS listening, no code-memory binary, clean env.
    monkeypatch.setattr(batteries, "_ams_reachable", lambda env: False)
    monkeypatch.setattr(batteries, "_code_memory_binary", lambda env: False)
    monkeypatch.setattr(batteries, "_headroom_available", lambda env: False)
    monkeypatch.setattr(batteries, "_find_spec", lambda name: False)
    env = {"ALFRED_HOME": str(tmp_path)}
    payload = batteries.manifest(env)
    assert payload["version"] == 1
    assert payload["summary"]["included"] == 4
    assert payload["summary"]["enabled"] == 0
    # Every opt-in is either available or not-installed, never enabled.
    statuses = {row["id"]: row["status"] for row in payload["batteries"]}
    for bid in (
        "dense-embeddings",
        "headroom-compression",
        "code-memory-mcp",
        "redis-ams",
        "pgvector",
    ):
        assert statuses[bid] in {"available", "not_installed"}
    for bid in ("sqlite-memory", "tool-compactor", "skeleton-reads", "blast-radius"):
        assert statuses[bid] == "included"


def test_default_provider_chain_is_sqlite_only() -> None:
    # With no ALFRED_MEMORY_PROVIDERS set, redis/pgvector are NOT enabled.
    redis = batteries.battery_by_id("redis-ams")
    pg = batteries.battery_by_id("pgvector")
    assert redis is not None and pg is not None
    assert batteries.is_enabled(redis, {}) is False
    assert batteries.is_enabled(pg, {}) is False


# --------------------------------------------------------------------------- #
# Status computation
# --------------------------------------------------------------------------- #
def test_status_included_available_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    dense = batteries.battery_by_id("dense-embeddings")
    assert dense is not None
    # installed but not enabled -> available
    monkeypatch.setattr(batteries, "_find_spec", lambda name: True)
    assert batteries.battery_status(dense, {}) == batteries.STATUS_AVAILABLE
    # enabled -> enabled (wins over available)
    assert batteries.battery_status(dense, {"ALFRED_MEMORY_SQLITE_DENSE": "1"}) == (
        batteries.STATUS_ENABLED
    )
    # not installed and not enabled -> not_installed
    monkeypatch.setattr(batteries, "_find_spec", lambda name: False)
    assert batteries.battery_status(dense, {}) == batteries.STATUS_NOT_INSTALLED


def test_headroom_enabled_only_for_headroom_engine() -> None:
    headroom = batteries.battery_by_id("headroom-compression")
    assert headroom is not None
    assert batteries.is_enabled(headroom, {"ALFRED_COMPRESSION_ENGINE": "headroom"}) is True
    assert batteries.is_enabled(headroom, {"ALFRED_COMPRESSION_ENGINE": "builtin"}) is False
    assert batteries.is_enabled(headroom, {}) is False


# --------------------------------------------------------------------------- #
# .env writer round-trip (idempotent, atomic)
# --------------------------------------------------------------------------- #
def test_write_env_enable_disable_roundtrip(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GH_ORG=acme\n", encoding="utf-8")

    redis = batteries.battery_by_id("redis-ams")
    dense = batteries.battery_by_id("dense-embeddings")
    assert redis is not None and dense is not None

    batteries.write_env(env_path, batteries.enable_values(dense))
    batteries.write_env(env_path, batteries.enable_values(redis))
    env = batteries.load_env({"ALFRED_HOME": str(tmp_path)})
    assert env["ALFRED_MEMORY_SQLITE_DENSE"] == "1"
    assert env["ALFRED_MEMORY_PROVIDERS"] == "redis,fleet"
    assert env["GH_ORG"] == "acme"  # untouched line preserved

    # Disabling redis returns to the sqlite default without touching dense.
    batteries.write_env(env_path, batteries.disable_values(redis))
    env = batteries.load_env({"ALFRED_HOME": str(tmp_path)})
    assert env["ALFRED_MEMORY_PROVIDERS"] == "sqlite,fleet"
    assert env["ALFRED_MEMORY_SQLITE_DENSE"] == "1"

    # Idempotent: writing the same value again does not duplicate the key.
    before = env_path.read_text(encoding="utf-8")
    batteries.write_env(env_path, batteries.disable_values(redis))
    assert env_path.read_text(encoding="utf-8") == before


def test_write_env_permissions(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    batteries.write_env(env_path, {"ALFRED_MEMORY_SQLITE_DENSE": "1"})
    mode = env_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_write_env_rejects_unsafe_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        batteries.write_env(tmp_path / ".env", {"bad key": "1"})
