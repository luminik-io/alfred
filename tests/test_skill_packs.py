"""Tests for the curated skill-pack registry (`lib/skill_packs.py`).

No network: the one fetch path is exercised through an injected runner stub, so
the suite is deterministic and CI-safe. Vendored installs use a tmp skills dir.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import skill_packs  # noqa: E402


def test_default_shell_runner_returns_timeout_status(tmp_path, monkeypatch) -> None:
    class FakeProcess:
        pid = 456

        def __init__(self, command, **_kwargs):
            self.command = command
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(self.command, timeout)
            return -15

    signals = []
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(skill_packs.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    assert skill_packs._default_shell_runner("slow-command", tmp_path) == 124
    assert signals == [
        (456, skill_packs.signal.SIGTERM),
        (456, skill_packs.signal.SIGKILL),
    ]


def test_default_shell_runner_cleans_process_group_on_interrupt(tmp_path, monkeypatch) -> None:
    class FakeProcess:
        pid = 654

        def __init__(self, _command, **_kwargs):
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            return -15

    signals = []
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(skill_packs.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(KeyboardInterrupt):
        skill_packs._default_shell_runner("interrupt-me", tmp_path)

    assert signals == [
        (654, skill_packs.signal.SIGTERM),
        (654, skill_packs.signal.SIGKILL),
    ]


# --------------------------------------------------------------------------
# Manifest parsing
# --------------------------------------------------------------------------


def test_shipped_manifest_parses() -> None:
    packs = skill_packs.load_manifest()
    assert packs, "manifest should define at least one pack"
    names = {p.name for p in packs}
    # The two load-bearing defaults called out in the task.
    assert "vercel-react-best-practices" in names
    assert "gstack" in names


def test_manifest_pack_names_are_unique() -> None:
    packs = skill_packs.load_manifest()
    names = [p.name for p in packs]
    assert len(names) == len(set(names))


def test_every_pack_has_a_license_and_source() -> None:
    for p in skill_packs.load_manifest():
        assert p.license, f"{p.name} missing license"
        assert p.source, f"{p.name} missing source"


def test_vendored_packs_point_at_a_real_directory() -> None:
    root = skill_packs.skills_root() / "vendored"
    for p in skill_packs.load_manifest():
        if p.is_vendored:
            src = root / p.vendored_path
            assert src.is_dir(), f"{p.name} vendored_path {src} does not exist"
            assert (src / "SKILL.md").is_file(), f"{p.name} has no SKILL.md"


def test_every_vendored_skill_keeps_its_license_file() -> None:
    """Attribution hygiene: each vendored skill ships its upstream LICENSE."""
    root = skill_packs.skills_root() / "vendored"
    for p in skill_packs.load_manifest():
        if p.is_vendored:
            assert (root / p.vendored_path / "LICENSE").is_file(), (
                f"{p.name} is vendored but has no LICENSE file next to it"
            )


def test_fetch_packs_have_a_fetch_command() -> None:
    for p in skill_packs.load_manifest():
        if p.is_fetch:
            assert p.fetch_cmd, f"{p.name} is fetch but has no fetch_cmd"


def test_no_copyleft_license_is_vendored() -> None:
    """GPL/AGPL cannot be vendored into this MIT repo; guard against a regression."""
    copyleft = {"GPL", "GPL-2.0", "GPL-3.0", "AGPL", "AGPL-3.0", "LGPL"}
    for p in skill_packs.load_manifest():
        if p.is_vendored:
            assert p.license not in copyleft, f"{p.name} vendors copyleft {p.license}"


def test_parse_rejects_bad_install_shape() -> None:
    with pytest.raises(ValueError, match="invalid install"):
        skill_packs._parse_pack({"name": "x", "install": "bogus"})


def test_parse_rejects_first_party_without_path() -> None:
    with pytest.raises(ValueError, match="must set first_party_path"):
        skill_packs._parse_pack({"name": "x", "install": "first_party"})


# --------------------------------------------------------------------------
# First-party tier
# --------------------------------------------------------------------------


def test_shipped_manifest_has_first_party_packs() -> None:
    packs = skill_packs.load_manifest()
    first_party = [p for p in packs if p.is_first_party]
    names = {p.name for p in first_party}
    assert "spec-to-issues" in names
    assert "write-tests" in names
    assert len(first_party) == 6


def test_first_party_packs_point_at_a_real_skill_dir() -> None:
    root = skill_packs.skills_root() / "first_party"
    for p in skill_packs.load_manifest():
        if p.is_first_party:
            src = root / p.first_party_path
            assert (src / "SKILL.md").is_file(), f"{p.name} has no SKILL.md at {src}"


def test_first_party_packs_are_local_copy_and_mit() -> None:
    for p in skill_packs.load_manifest():
        if p.is_first_party:
            assert p.is_local_copy
            assert not p.is_fetch
            assert p.license == "MIT"


def test_install_first_party_copies_skill(tmp_path: Path) -> None:
    packs = skill_packs.load_manifest()
    pack = next(p for p in packs if p.name == "spec-to-issues")
    result = skill_packs.install_pack(pack, skills_dir=tmp_path)
    assert result.dest == tmp_path / "spec-to-issues"
    assert (result.dest / "SKILL.md").is_file()
    # The reference file rides along with the copytree.
    assert (result.dest / "references" / "spec-shape.md").is_file()


def test_install_first_party_missing_source_raises(tmp_path: Path) -> None:
    bad = _pack(install="first_party", first_party_path="nope", vendored_path=None)
    with pytest.raises(FileNotFoundError, match="first-party source missing"):
        skill_packs.install_pack(bad, skills_dir=tmp_path)


def test_starter_packs_are_the_default_first_party_set() -> None:
    packs = skill_packs.load_manifest()
    starter = skill_packs.starter_packs(packs)
    assert starter, "expected a non-empty starter set"
    for p in starter:
        assert p.default_install
        assert p.is_local_copy  # never pulls a network fetch implicitly
    # All six first-party skills are in the starter set.
    assert {p.name for p in starter} >= {"spec-to-issues", "write-tests", "review-security"}


def test_parse_rejects_vendored_without_path() -> None:
    with pytest.raises(ValueError, match="must set vendored_path"):
        skill_packs._parse_pack({"name": "x", "install": "vendored"})


def test_parse_rejects_fetch_without_cmd() -> None:
    with pytest.raises(ValueError, match="must set fetch_cmd"):
        skill_packs._parse_pack({"name": "x", "install": "fetch"})


def test_load_manifest_rejects_duplicate_names(tmp_path: Path) -> None:
    manifest = tmp_path / "packs.toml"
    manifest.write_text(
        """
[[pack]]
name = "dup"
install = "fetch"
fetch_cmd = "true"

[[pack]]
name = "dup"
install = "fetch"
fetch_cmd = "true"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate pack name"):
        skill_packs.load_manifest(manifest)


# --------------------------------------------------------------------------
# Install path resolution
# --------------------------------------------------------------------------


def _pack(**kw: object) -> skill_packs.Pack:
    base: dict[str, object] = {
        "name": "demo",
        "summary": "s",
        "source": "https://example.test/repo",
        "ref": "main",
        "license": "MIT",
        "attribution": "(c) test",
        "install": "vendored",
        "roles": ("feature-dev",),
        "vendored_path": "demo",
    }
    base.update(kw)
    return skill_packs.Pack(**base)  # type: ignore[arg-type]


def test_default_skills_dir_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(skill_packs.DEFAULT_SKILLS_DIR_ENV, str(tmp_path / "sk"))
    assert skill_packs.default_skills_dir() == tmp_path / "sk"


def test_default_skills_dir_falls_back_to_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(skill_packs.DEFAULT_SKILLS_DIR_ENV, raising=False)
    assert skill_packs.default_skills_dir().name == "skills"
    assert ".claude" in str(skill_packs.default_skills_dir())


def test_install_vendored_copies_real_skill(tmp_path: Path) -> None:
    """Use a real shipped vendored pack so the copy path is exercised end to end."""
    packs = skill_packs.load_manifest()
    vercel = next(p for p in packs if p.name == "vercel-react-best-practices")
    result = skill_packs.install_pack(vercel, skills_dir=tmp_path)
    assert result.dest == tmp_path / "vercel-react-best-practices"
    assert (result.dest / "SKILL.md").is_file()
    assert (result.dest / "LICENSE").is_file()
    assert not result.dry_run


def test_install_vendored_is_idempotent(tmp_path: Path) -> None:
    packs = skill_packs.load_manifest()
    pack = next(p for p in packs if p.is_vendored)
    skill_packs.install_pack(pack, skills_dir=tmp_path)
    # Second install replaces cleanly, no error.
    result = skill_packs.install_pack(pack, skills_dir=tmp_path)
    assert (result.dest / "SKILL.md").is_file()


def test_install_vendored_missing_source_raises(tmp_path: Path) -> None:
    bad = _pack(vendored_path="does-not-exist")
    with pytest.raises(FileNotFoundError, match="vendored source missing"):
        skill_packs.install_pack(bad, skills_dir=tmp_path)


def test_install_fetch_uses_injected_runner_and_expands_dir(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_runner(cmd: str, cwd: Path) -> int:
        calls.append((cmd, cwd))
        return 0

    pack = _pack(install="fetch", fetch_cmd="clone {skills_dir}/thing", vendored_path=None)
    result = skill_packs.install_pack(pack, skills_dir=tmp_path, runner=fake_runner)
    assert len(calls) == 1
    cmd, _ = calls[0]
    assert str(tmp_path) in cmd  # {skills_dir} was expanded
    assert result.fetched == f"clone {tmp_path}/thing"


def test_install_fetch_nonzero_runner_raises(tmp_path: Path) -> None:
    pack = _pack(install="fetch", fetch_cmd="false", vendored_path=None)
    with pytest.raises(RuntimeError, match=r"fetch for .* failed"):
        skill_packs.install_pack(pack, skills_dir=tmp_path, runner=lambda _c, _d: 1)


def test_install_fetch_failure_removes_new_partial_destination(tmp_path: Path) -> None:
    pack = _pack(install="fetch", fetch_cmd="clone {skills_dir}/thing", vendored_path=None)

    def partial_runner(_cmd: str, cwd: Path) -> int:
        (cwd / pack.name).mkdir()
        (cwd / pack.name / "partial").write_text("incomplete")
        return 124

    with pytest.raises(RuntimeError, match="exit 124"):
        skill_packs.install_pack(pack, skills_dir=tmp_path, runner=partial_runner)

    assert not (tmp_path / pack.name).exists()


def test_install_fetch_failure_preserves_existing_destination(tmp_path: Path) -> None:
    pack = _pack(install="fetch", fetch_cmd="clone {skills_dir}/thing", vendored_path=None)
    dest = tmp_path / pack.name
    dest.mkdir()
    marker = dest / "working-skill"
    marker.write_text("keep")

    with pytest.raises(RuntimeError, match="exit 124"):
        skill_packs.install_pack(pack, skills_dir=tmp_path, runner=lambda _c, _d: 124)

    assert marker.read_text() == "keep"


def test_install_fetch_shell_quotes_spaced_skills_dir(tmp_path: Path) -> None:
    """A skills dir with spaces (or metacharacters) must be shell-quoted.

    Fetch commands run with shell=True; an unquoted path would be word-split
    or interpreted as shell syntax (review finding on PR #382).
    """
    import shlex

    spaced = tmp_path / "with space" / "sk ills"
    spaced.mkdir(parents=True)
    calls: list[str] = []
    pack = _pack(
        install="fetch",
        fetch_cmd="clone {skills_dir}/thing && cd {skills_dir}/thing",
        vendored_path=None,
    )
    skill_packs.install_pack(pack, skills_dir=spaced, runner=lambda c, _d: calls.append(c) or 0)
    (cmd,) = calls
    quoted = shlex.quote(str(spaced))
    assert quoted.startswith("'")  # the spaced path really did need quoting
    assert cmd == f"clone {quoted}/thing && cd {quoted}/thing"
    # The shell must see the path as ONE token: split the first clause and
    # check the argument parses back to the real path plus suffix.
    tokens = shlex.split(cmd.split("&&")[0])
    assert tokens == ["clone", f"{spaced}/thing"]


def test_fetch_pip_installs_are_version_pinned() -> None:
    """Reference pip installs must pin the audited version (reproducibility).

    An unpinned `pip install` would fetch whatever upstream publishes next,
    silently breaking the license-audited contract recorded in the manifest
    (review finding on PR #382).
    """
    for p in skill_packs.load_manifest():
        if p.is_fetch and p.fetch_cmd and "pip install" in p.fetch_cmd:
            assert "==" in p.fetch_cmd, (
                f"{p.name} pip-installs without a version pin: {p.fetch_cmd}"
            )


def test_dry_run_writes_nothing_and_runs_nothing(tmp_path: Path) -> None:
    ran: list[str] = []
    fetch = _pack(install="fetch", fetch_cmd="echo hi", vendored_path=None)
    r1 = skill_packs.install_pack(
        fetch, skills_dir=tmp_path, dry_run=True, runner=lambda c, _d: ran.append(c) or 0
    )
    assert r1.dry_run and r1.fetched == "echo hi"
    assert ran == []  # runner never called on dry-run

    packs = skill_packs.load_manifest()
    vendored = next(p for p in packs if p.is_vendored)
    r2 = skill_packs.install_pack(vendored, skills_dir=tmp_path, dry_run=True)
    assert r2.dry_run
    assert not (tmp_path / vendored.name).exists()  # nothing copied


def test_installed_packs_reflects_directory_presence(tmp_path: Path) -> None:
    packs = skill_packs.load_manifest()
    vendored = [p for p in packs if p.is_vendored]
    assert skill_packs.installed_packs(packs, skills_dir=tmp_path) == set()
    skill_packs.install_pack(vendored[0], skills_dir=tmp_path)
    assert vendored[0].name in skill_packs.installed_packs(packs, skills_dir=tmp_path)


# --------------------------------------------------------------------------
# Prompt-inlining helper (the --bare-proof headless path)
# --------------------------------------------------------------------------


def test_skill_prompt_snippet_names_a_vendored_skill(tmp_path: Path) -> None:
    pack = _pack()
    snippet = skill_packs.skill_prompt_snippet(pack, skills_dir=tmp_path)
    assert "`demo`" in snippet
    assert "demo" in snippet


def test_skill_prompt_snippet_is_empty_for_headroom() -> None:
    packs = skill_packs.load_manifest()
    headroom = next(p for p in packs if p.name == "headroom")
    assert skill_packs.skill_prompt_snippet(headroom) == ""
