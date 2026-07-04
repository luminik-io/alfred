"""Validate the Alfred first-party skills and their manifest registration.

Every ``skills/first_party/<name>/SKILL.md`` must have valid frontmatter (name
matching the directory, a non-empty description, both within the Anthropic
SKILL.md length limits) and must be registered in ``skills/packs.toml`` as a
``first_party`` pack. The registry-level shape (install path resolution, the
starter set) is exercised in ``test_skill_packs.py``; this file guards the skill
content itself.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import skill_packs  # noqa: E402

FIRST_PARTY_DIR = REPO_ROOT / "skills" / "first_party"

# Anthropic SKILL.md frontmatter limits.
_NAME_MAX = 64
_DESC_MAX = 1024
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _skill_dirs() -> list[Path]:
    return sorted(
        p.parent for p in FIRST_PARTY_DIR.glob("*/SKILL.md") if p.parent.name != "_proposed"
    )


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal ``---`` fenced ``key: value`` parser (matches the injector)."""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md must open with a --- fence"
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" not in line or line != line.lstrip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    raise AssertionError("SKILL.md frontmatter is not closed by a --- fence")


def test_there_are_six_first_party_skills() -> None:
    names = {d.name for d in _skill_dirs()}
    assert names == {
        "spec-to-issues",
        "write-tests",
        "review-security",
        "add-observability",
        "migrate-dependency",
        "changelog-and-release-notes",
    }


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_first_party_skill_frontmatter_is_valid(skill_dir: Path) -> None:
    fm = _parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    name = fm.get("name", "")
    description = fm.get("description", "")

    assert name == skill_dir.name, f"name {name!r} must match directory {skill_dir.name!r}"
    assert _NAME_RE.match(name), f"name {name!r} must be lowercase-hyphen"
    assert len(name) <= _NAME_MAX, f"name exceeds {_NAME_MAX} chars"
    assert description, "description (the trigger) must be present"
    assert len(description) <= _DESC_MAX, f"description exceeds {_DESC_MAX} chars"
    assert fm.get("license") == "MIT", "first-party skills are MIT"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_first_party_skill_body_has_procedure_and_output(skill_dir: Path) -> None:
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "when to use" in body
    assert "## procedure" in body
    assert "## output" in body


def test_every_first_party_skill_is_registered_as_first_party() -> None:
    packs = {p.name: p for p in skill_packs.load_manifest()}
    for skill_dir in _skill_dirs():
        pack = packs.get(skill_dir.name)
        assert pack is not None, f"{skill_dir.name} is not registered in packs.toml"
        assert pack.is_first_party, f"{skill_dir.name} must be install=first_party"
        assert pack.first_party_path == skill_dir.name
        assert pack.license == "MIT"


def test_spec_to_issues_ships_its_reference() -> None:
    ref = FIRST_PARTY_DIR / "spec-to-issues" / "references" / "spec-shape.md"
    assert ref.is_file(), "spec-to-issues must ship references/spec-shape.md"


def test_first_party_skills_use_placeholder_repo_names_only() -> None:
    """No real internal repo names may appear in a public skill body.

    The banned tokens are assembled at runtime (prefix + suffix) so this guard
    file does not itself contain the literal strings the scrub-check blocks.
    """
    prefix = "lumin" + "ik-"
    banned = [prefix + suffix for suffix in ("backend", "frontend", "mobile", "nango")]
    for skill_dir in _skill_dirs():
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{skill_dir.name} leaks a real repo name"
