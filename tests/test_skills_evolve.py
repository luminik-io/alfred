"""Tests for `alfred skills evolve` (clustering + draft emission).

The lesson source is stubbed, so no live brain is needed. The key guarantees:
clustering groups by (repo, tag) with a minimum size, drafts land under
``_proposed/`` with valid frontmatter, and NOTHING is ever installed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import skills_evolve  # noqa: E402


@dataclass
class StubLesson:
    body: str
    tags: list[str] = field(default_factory=list)
    repo: str = "your-backend"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _recall_from(lessons):
    def recall(**kwargs):
        return list(lessons)

    return recall


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def test_cluster_groups_by_repo_and_tag() -> None:
    lessons = [
        StubLesson("regen the client after schema change", ["schema"], "your-backend"),
        StubLesson("schema migrations need a down path", ["schema"], "your-backend"),
        StubLesson("only one auth lesson", ["auth"], "your-backend"),
    ]
    clusters = skills_evolve.cluster_lessons(lessons)
    # schema has 2 (kept), auth has 1 (dropped by min-size).
    assert [c.tag for c in clusters] == ["schema"]
    assert clusters[0].repo == "your-backend"
    assert clusters[0].size == 2


def test_cluster_respects_min_size() -> None:
    lessons = [StubLesson("a", ["x"]), StubLesson("b", ["x"]), StubLesson("c", ["x"])]
    assert skills_evolve.cluster_lessons(lessons, min_cluster_size=4) == []
    assert skills_evolve.cluster_lessons(lessons, min_cluster_size=3)[0].size == 3


def test_cluster_since_filters_old_lessons() -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    new = datetime.now(UTC)
    lessons = [
        StubLesson("old", ["x"], created_at=old),
        StubLesson("new-1", ["x"], created_at=new),
        StubLesson("new-2", ["x"], created_at=new),
    ]
    clusters = skills_evolve.cluster_lessons(lessons, since=new - timedelta(days=1))
    assert len(clusters) == 1
    bodies = [str(x.body) for x in clusters[0].lessons]
    assert "old" not in bodies


def test_cluster_multi_tag_lesson_contributes_to_each_tag() -> None:
    lessons = [
        StubLesson("l1", ["auth", "backend"]),
        StubLesson("l2", ["auth"]),
        StubLesson("l3", ["backend"]),
    ]
    clusters = {c.tag: c for c in skills_evolve.cluster_lessons(lessons)}
    assert set(clusters) == {"auth", "backend"}
    assert clusters["auth"].size == 2
    assert clusters["backend"].size == 2


# --------------------------------------------------------------------------
# Draft rendering + emission
# --------------------------------------------------------------------------


def test_render_draft_has_valid_proposed_frontmatter() -> None:
    cluster = skills_evolve.LessonCluster(
        name="your-backend-schema",
        repo="your-backend",
        tag="schema",
        lessons=(StubLesson("regen client"), StubLesson("add down migration")),
    )
    body = skills_evolve.render_proposed_skill(cluster)
    assert body.startswith("---\n")
    assert "name: your-backend-schema" in body
    assert "description:" in body
    assert "status: proposed" in body
    assert "regen client" in body
    assert "## Procedure" in body


def test_evolve_writes_drafts_under_proposed(tmp_path: Path) -> None:
    lessons = [
        StubLesson("regen the client", ["schema"], "your-backend"),
        StubLesson("add a down migration", ["schema"], "your-backend"),
    ]
    proposals = skills_evolve.evolve_skills(recall=_recall_from(lessons), proposed_dir=tmp_path)
    assert len(proposals) == 1
    prop = proposals[0]
    assert prop.written is True
    assert prop.path == tmp_path / "your-backend-schema" / "SKILL.md"
    assert prop.path.is_file()
    assert "status: proposed" in prop.path.read_text(encoding="utf-8")


def test_evolve_dry_run_writes_nothing(tmp_path: Path) -> None:
    lessons = [
        StubLesson("a", ["x"], "your-backend"),
        StubLesson("b", ["x"], "your-backend"),
    ]
    proposals = skills_evolve.evolve_skills(
        recall=_recall_from(lessons), proposed_dir=tmp_path, dry_run=True
    )
    assert len(proposals) == 1
    assert proposals[0].written is False
    assert not any(tmp_path.rglob("SKILL.md"))  # nothing written


def test_evolve_never_installs(tmp_path: Path, monkeypatch) -> None:
    """Evolve must not touch the skills-install path under any circumstances."""
    import skill_packs

    def fail_install(*args, **kwargs):
        raise AssertionError("evolve must never install a skill")

    monkeypatch.setattr(skill_packs, "install_pack", fail_install)
    lessons = [
        StubLesson("a", ["x"], "your-backend"),
        StubLesson("b", ["x"], "your-backend"),
    ]
    proposals = skills_evolve.evolve_skills(recall=_recall_from(lessons), proposed_dir=tmp_path)
    # Drafts land only under the injected proposed dir, never in a skills dir.
    assert all(str(tmp_path) in str(p.path) for p in proposals)


def test_evolve_empty_when_no_clusters(tmp_path: Path) -> None:
    lessons = [StubLesson("lonely", ["x"], "your-backend")]  # size 1, no cluster
    proposals = skills_evolve.evolve_skills(recall=_recall_from(lessons), proposed_dir=tmp_path)
    assert proposals == []


# --------------------------------------------------------------------------
# CLI wiring (evolve verb never installs, honors --dry-run)
# --------------------------------------------------------------------------


def test_cli_evolve_dry_run_reports_without_writing(tmp_path: Path, monkeypatch, capsys) -> None:
    import skills_cli

    monkeypatch.setattr(skills_evolve, "default_proposed_dir", lambda: tmp_path)
    lessons = [
        StubLesson("a", ["x"], "your-backend"),
        StubLesson("b", ["x"], "your-backend"),
    ]
    rc = skills_cli.cmd_evolve(dry_run=True, recall=_recall_from(lessons))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Would draft" in out
    assert "nothing was installed" in out
    assert not any(tmp_path.rglob("SKILL.md"))
