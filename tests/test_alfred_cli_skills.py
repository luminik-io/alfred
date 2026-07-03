"""Tests for the `alfred skills` operator subcommand."""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin" / "alfred"
LIB = REPO_ROOT / "lib"
sys.path.insert(0, str(LIB))


@pytest.fixture()
def cli_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    # Isolate the skills dir so "installed" detection is deterministic.
    monkeypatch.setenv("ALFRED_SKILLS_DIR", str(tmp_path / "skills"))
    loader = SourceFileLoader("alfred_cli_skills", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alfred_cli_skills"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(cli_module, argv: list[str]) -> int:
    return cli_module.main(argv)


def test_skills_list_json_lists_curated_packs(cli_module, capsys) -> None:
    rc = _run(cli_module, ["skills", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = {p["name"] for p in payload}
    assert "vercel-react-best-practices" in names
    assert "gstack" in names
    assert all("license" in p and "install" in p for p in payload)


def test_skills_list_role_filter(cli_module, capsys) -> None:
    rc = _run(cli_module, ["skills", "list", "--role", "feature-dev", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload, "expected some feature-dev packs"
    for p in payload:
        assert "feature-dev" in p["roles"]


def test_skills_install_vendored_lands_in_skills_dir(cli_module, capsys, tmp_path: Path) -> None:
    rc = _run(cli_module, ["skills", "install", "code-review-and-quality"])
    assert rc == 0
    dest = tmp_path / "skills" / "code-review-and-quality"
    assert (dest / "SKILL.md").is_file()
    assert (dest / "LICENSE").is_file()
    assert "installed code-review-and-quality" in capsys.readouterr().out


def test_skills_install_unknown_pack_errors(cli_module, capsys) -> None:
    rc = _run(cli_module, ["skills", "install", "no-such-pack"])
    assert rc == 2
    assert "Unknown pack" in capsys.readouterr().err


def test_skills_install_fetch_requires_yes(cli_module, capsys) -> None:
    rc = _run(cli_module, ["skills", "install", "gstack"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "reference-install" in err
    assert "--yes" in err


def test_skills_install_fetch_dry_run_previews_without_yes(cli_module, capsys) -> None:
    rc = _run(cli_module, ["skills", "install", "gstack", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would install gstack" in out
    assert "git clone" in out


def test_skills_installed_reflects_prior_install(cli_module, capsys, tmp_path: Path) -> None:
    assert _run(cli_module, ["skills", "install", "security-and-hardening"]) == 0
    capsys.readouterr()
    rc = _run(cli_module, ["skills", "installed", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "security-and-hardening" in payload["installed"]
