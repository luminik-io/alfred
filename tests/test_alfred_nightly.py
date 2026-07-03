"""Coverage for the config-driven weekly dependency updater.

The updater is opt-in: with no repos configured it is a no-op. These tests
exercise the environment-driven repo parsing and the safe/major bump split
without shelling out to npm or GitHub.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BIN = _ROOT / "bin"
_LIB = _ROOT / "lib"


def _load_nightly(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    if str(_LIB) not in sys.path:
        sys.path.insert(0, str(_LIB))
    for stale in list(sys.modules):
        if stale == "nightly_under_test":
            sys.modules.pop(stale, None)
    spec = importlib.util.spec_from_file_location("nightly_under_test", _BIN / "alfred-nightly.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["nightly_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_no_repos_configured_is_empty(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    assert nightly._parse_npm_repos("") == []
    assert nightly._parse_advisory_repos("") == []


def test_parse_npm_repos_with_and_without_pre_push(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    raw = "frontend:your-frontend:npm install && npm run build;api:your-api:"
    parsed = nightly._parse_npm_repos(raw)
    assert parsed == [
        ("frontend", "your-frontend", "npm install && npm run build"),
        ("api", "your-api", None),
    ]


def test_parse_npm_repos_keeps_colons_in_pre_push(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    # Only the first two colons split; the command may contain more.
    parsed = nightly._parse_npm_repos("web:your-web:a && b:c")
    assert parsed == [("web", "your-web", "a && b:c")]


def test_parse_npm_repos_skips_malformed_entries(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    # Missing slug is dropped; a valid entry survives.
    parsed = nightly._parse_npm_repos("justlocal;good:your-good:npm install")
    assert parsed == [("good", "your-good", "npm install")]


def test_parse_advisory_repos(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    parsed = nightly._parse_advisory_repos("backend:your-backend:gradle;data:your-data:pip")
    assert parsed == [
        ("backend", "your-backend", "gradle"),
        ("data", "your-data", "pip"),
    ]


def test_split_safe_vs_major(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    outdated = {
        "a": {"current": "1.0.0", "wanted": "1.2.0", "latest": "2.0.0"},
        "b": {"current": "3.0.0", "wanted": "3.0.0", "latest": "3.0.0"},
    }
    safe, majors = nightly.split_safe_vs_major(outdated)
    assert safe == [("a", "1.0.0", "1.2.0")]
    assert majors == [("a", "1.2.0", "2.0.0")]


def test_pr_body_has_no_ai_attribution(monkeypatch, tmp_path):
    nightly = _load_nightly(monkeypatch, tmp_path)
    body = nightly._build_pr_body(
        "web",
        safe=[("a", "1.0.0", "1.1.0")],
        majors=[],
        cves=[],
    )
    commit = nightly._build_commit_message(
        "web",
        safe=[("a", "1.0.0", "1.1.0")],
        majors=[],
        cves=[],
    )
    em_dash = chr(0x2014)
    en_dash = chr(0x2013)
    for text in (body, commit):
        assert "Co-Authored-By" not in text
        assert "Claude" not in text
        assert em_dash not in text
        assert en_dash not in text
