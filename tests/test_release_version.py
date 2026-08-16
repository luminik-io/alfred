from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _toml(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _release_notes(version: str, changelog: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "bin/release-notes.sh"), version, str(changelog)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_version_is_consistent() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert _toml("pyproject.toml")["project"]["version"] == version
    assert _json("site/package.json")["version"] == version
    site_lock = _json("site/package-lock.json")
    assert site_lock["version"] == version
    assert site_lock["packages"][""]["version"] == version
    assert _json("clients/desktop/package.json")["version"] == version
    desktop_node_lock = _json("clients/desktop/package-lock.json")
    assert desktop_node_lock["version"] == version
    assert desktop_node_lock["packages"][""]["version"] == version
    assert _toml("clients/desktop/src-tauri/Cargo.toml")["package"]["version"] == version

    desktop_lock = _toml("clients/desktop/src-tauri/Cargo.lock")
    desktop_package = next(
        package for package in desktop_lock["package"] if package["name"] == "alfred_desktop"
    )
    assert desktop_package["version"] == version

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_heading = re.search(
        rf"^## \[{re.escape(version)}\] - (?P<date>\d{{4}}-\d{{2}}-\d{{2}})$",
        changelog,
        re.MULTILINE,
    )
    assert release_heading is not None
    release_date = release_heading.group("date")
    site_changelog = (ROOT / "site/src/content/docs/about/changelog.md").read_text(encoding="utf-8")
    assert f"## {version} ({release_date})" in site_changelog
    anchor = version.replace(".", "")
    assert f"CHANGELOG.md#{anchor}---{release_date}" in site_changelog


def test_release_workflow_validates_the_dispatch_tag_before_shell_use() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "RELEASE_INPUT_TAG: ${{ inputs.tag }}" in workflow
    assert 'tag="${{ inputs.tag }}"' not in workflow
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in workflow
    assert "push:" not in workflow.partition("permissions:")[0]
    assert "if: github.ref == 'refs/heads/main'" not in workflow
    assert 'if [ "$GITHUB_REF" != "refs/heads/main" ]; then' in workflow
    assert "Release workflow must run from main, not $GITHUB_REF" in workflow
    assert "ref: main" in workflow
    assert "ref: ${{ steps.tag.outputs.tag }}" not in workflow
    assert workflow.index("- name: Verify dispatch source") < workflow.index(
        "- name: Determine tag"
    )
    assert workflow.index("- name: Determine tag") < workflow.index("- uses: actions/checkout@v7")
    assert "bin/release-source-gate.sh" in workflow
    assert '"refs/heads/main:refs/remotes/origin/main"' in workflow
    assert "--jq '.verification.verified'" in workflow
    assert "--jq '.commit.verification.verified'" in workflow
    assert 'git show "${tag}:VERSION"' in workflow
    assert 'git show "${tag}:CHANGELOG.md"' in workflow
    assert "bin/release-notes.sh" in workflow
    assert "--json isDraft,isImmutable" in workflow
    assert 'if [ "$is_draft" = "true" ]; then' in workflow
    assert "gh release edit" in workflow
    assert "Release $tag is already published; no update made" in workflow


def test_release_notes_extracts_only_highlights(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [1.2.3] - 2026-08-16\n\n"
        "### Highlights\n\n"
        "- First result.\n"
        "- Second result.\n\n"
        "### Fixed\n\n"
        "- Internal detail.\n",
        encoding="utf-8",
    )

    result = _release_notes("1.2.3", changelog)

    assert result.returncode == 0
    assert result.stdout == "- First result.\n- Second result.\n"


def test_release_notes_fails_when_highlights_are_missing(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [1.2.3] - 2026-08-16\n\n### Fixed\n\n- Detail.\n",
        encoding="utf-8",
    )

    result = _release_notes("1.2.3", changelog)

    assert result.returncode == 1
    assert "no Highlights found for 1.2.3" in result.stderr


def test_release_notes_fails_for_a_malformed_highlights_heading(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [1.2.3] - 2026-08-16\n\n### Highlight\n\n- Detail.\n",
        encoding="utf-8",
    )

    result = _release_notes("1.2.3", changelog)

    assert result.returncode == 1
    assert "no Highlights found for 1.2.3" in result.stderr


def test_release_notes_does_not_treat_regex_characters_as_wildcards(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [1x2x3] - 2026-08-16\n\n### Highlights\n\n- Wrong.\n",
        encoding="utf-8",
    )

    result = _release_notes("1.2.3", changelog)

    assert result.returncode == 1
    assert "no Highlights found for 1.2.3" in result.stderr


def test_release_source_gate_accepts_an_annotated_tag_on_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", "release")
    main_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
    (repo / "NEXT").write_text("post-release\n", encoding="utf-8")
    _git(repo, "add", "NEXT")
    _git(repo, "commit", "-m", "advance main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    result = subprocess.run(
        [str(ROOT / "bin/release-source-gate.sh"), "v1.2.3", "origin/main"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == main_commit


def test_release_source_gate_rejects_a_lightweight_tag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", "release")
    main_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", main_commit)
    _git(repo, "tag", "--no-sign", "v1.2.3")

    result = subprocess.run(
        [str(ROOT / "bin/release-source-gate.sh"), "v1.2.3", "origin/main"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must be an annotated tag" in result.stderr


def test_release_source_gate_rejects_a_side_branch_tag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "VERSION").write_text("1.2.2\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", "main")
    main_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", main_commit)
    _git(repo, "checkout", "-b", "release-side")
    (repo / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", "unreviewed release")
    _git(repo, "tag", "-a", "v1.2.3", "-m", "v1.2.3")

    result = subprocess.run(
        [str(ROOT / "bin/release-source-gate.sh"), "v1.2.3", "origin/main"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "is not on origin/main" in result.stderr
