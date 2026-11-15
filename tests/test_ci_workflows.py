"""Guards for CI triggers and path gating that keep pull-request runs cheap."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
PR_METADATA = (WORKFLOWS / "pr-metadata.yml").read_text(encoding="utf-8")


def _job(text: str, name: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)", text)
    assert match, name
    return match.group(1)


def _changes_script() -> str:
    job = _job(CI, "changes")
    match = re.search(r"(?ms)^        run: \|\n(.*)", job)
    assert match
    return "\n".join(line[10:] for line in match.group(1).splitlines())


def _desktop_needed(tmp_path: Path, event: str, files: list[str]) -> str:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    git = stub / "git"
    git.write_text('#!/bin/sh\ncat "$FILES"\n')
    git.chmod(0o755)
    listing = tmp_path / "files.txt"
    listing.write_text("".join(f"{name}\n" for name in files))
    output = tmp_path / "output.txt"
    output.write_text("")
    subprocess.run(
        ["bash", "-c", _changes_script()],
        env={
            "PATH": f"{stub}:/usr/bin:/bin",
            "EVENT": event,
            "BASE_SHA": "base",
            "HEAD_SHA": "head",
            "FILES": str(listing),
            "GITHUB_OUTPUT": str(output),
        },
        check=True,
    )
    return output.read_text().strip()


def test_ci_does_not_rerun_on_pull_request_edits() -> None:
    triggers = re.search(r"(?ms)^  pull_request:\n(.*?)^  push:", CI)
    assert triggers
    assert "edited" not in triggers.group(1)
    assert "public-metadata:" not in CI


def test_metadata_check_reruns_on_pull_request_edits() -> None:
    assert "types: [opened, synchronize, reopened, edited]" in PR_METADATA
    assert "name: Public PR metadata" in PR_METADATA
    assert "python bin/public-metadata-check.py" in PR_METADATA


def test_desktop_client_waits_for_the_changed_area_gate() -> None:
    job = _job(CI, "desktop-client")
    assert "needs: changes" in job
    assert "if: needs.changes.outputs.desktop == 'true'" in job


def test_desktop_build_is_skipped_only_for_site_docs_and_markdown(tmp_path: Path) -> None:
    assert (
        _desktop_needed(tmp_path, "pull_request", ["site/package.json", "site/package-lock.json"])
        == "desktop=false"
    )
    assert (
        _desktop_needed(tmp_path, "pull_request", ["README.md", "docs/DEMO.md"]) == "desktop=false"
    )
    assert (
        _desktop_needed(tmp_path, "pull_request", ["site/package.json", "lib/server/app.py"])
        == "desktop=true"
    )
    assert (
        _desktop_needed(tmp_path, "pull_request", ["clients/desktop/package.json"])
        == "desktop=true"
    )
    assert _desktop_needed(tmp_path, "pull_request", []) == "desktop=true"
    assert _desktop_needed(tmp_path, "push", ["README.md"]) == "desktop=true"
