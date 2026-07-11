"""Ratchet: the central config registry must cover every var used in code.

This is the guard that stops the ``ALFRED_*`` sprawl from coming back. It
mechanically discovers every ``ALFRED_*`` token in ``lib/`` and ``bin/`` (the
same way the audit did) and fails if any token is neither a declared
:class:`alfred_config.ConfigVar` nor an explicitly listed non-var token.

When this test fails, the fix is one of:

* the new var is real -> add a ``ConfigVar`` entry to ``lib/alfred_config.py``
  and run ``bin/alfred-config-doc.py`` to refresh the generated docs; or
* the token is not a real env var (a dynamic-prefix family, an identifier
  fragment, or a sentinel string) -> add it to
  ``alfred_config.NON_VAR_TOKENS`` with a one-line reason.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import alfred_config as cfg
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_TOKEN = re.compile(r"ALFRED_[A-Z0-9_]+")
_SKIP_SUFFIX = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}


def _discover_alfred_tokens() -> set[str]:
    """Every ``ALFRED_*`` token literally present in lib/ and bin/ source.

    Mirrors ``grep -rhoE "ALFRED_[A-Z0-9_]+" lib bin`` but skips compiled
    bytecode and binary assets (grep skips those too), so the set is exactly
    the tokens a human reading the source would see.
    """
    found: set[str] = set()
    for base in ("lib", "bin"):
        for path in (REPO_ROOT / base).rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in _SKIP_SUFFIX:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            found.update(_TOKEN.findall(text))
    return found


def test_every_alfred_token_is_registered() -> None:
    discovered = _discover_alfred_tokens()
    registered = cfg.registered_names()
    unregistered = sorted(discovered - registered)
    assert not unregistered, (
        "New ALFRED_* vars are not in the registry: "
        f"{unregistered}. Add a ConfigVar to lib/alfred_config.py (and run "
        "bin/alfred-config-doc.py), or list the token in "
        "alfred_config.NON_VAR_TOKENS with a reason."
    )


def test_discovery_finds_a_reasonable_number_of_vars() -> None:
    # Sanity check that discovery is actually scanning source, not silently
    # returning an empty set (which would make the ratchet vacuously pass).
    assert len(_discover_alfred_tokens()) > 300


def test_registry_has_no_duplicate_names() -> None:
    names = [v.name for v in cfg.all_vars()]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate registry entries: {dupes}"


def test_every_var_has_a_known_category_and_description() -> None:
    for var in cfg.all_vars():
        assert var.category in cfg.CATEGORIES, f"{var.name}: bad category {var.category}"
        assert var.description.strip(), f"{var.name}: empty description"
        assert not var.description.endswith(" "), f"{var.name}: trailing space"


def test_enum_vars_declare_choices() -> None:
    for var in cfg.all_vars():
        if var.kind == "enum":
            assert var.choices, f"{var.name}: enum var must declare choices"


def test_typed_accessors_prefer_env_then_default() -> None:
    env: dict[str, str] = {}
    # Falls back to the registered default.
    assert cfg.get_int("ALFRED_MAX_STEPS", env) == 200
    assert cfg.get_bool("ALFRED_OUTPUT_COMPACTOR", env) is True
    assert cfg.get_str("ALFRED_AMS_HOST", env) == "127.0.0.1"
    # Env override wins.
    env["ALFRED_MAX_STEPS"] = "7"
    assert cfg.get_int("ALFRED_MAX_STEPS", env) == 7
    env["ALFRED_OUTPUT_COMPACTOR"] = "0"
    assert cfg.get_bool("ALFRED_OUTPUT_COMPACTOR", env) is False
    # List splitting.
    env["ALFRED_MEMORY_PROVIDERS"] = "sqlite, fleet ,"
    assert cfg.get_list("ALFRED_MEMORY_PROVIDERS", env) == ["sqlite", "fleet"]


def test_generated_docs_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "alfred-config-doc.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Generated config docs are stale. Run bin/alfred-config-doc.py.\n"
        f"{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize("name", sorted(cfg.REGISTRY))
def test_registered_defaults_are_stringlike(name: str) -> None:
    var = cfg.REGISTRY[name]
    assert var.default is None or isinstance(var.default, str)
