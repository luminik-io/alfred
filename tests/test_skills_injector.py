"""Tests for the runner skill-injector (`lib/agent_runner/skills_context.py`).

Covers frontmatter-only discovery, the size cap, role filtering, block
rendering, and the process.py wiring (appends for a firing's role, omits when
ALFRED_SKILLS_INJECT=0 or no skill matches).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from agent_runner import process, skills_context  # noqa: E402


def _write_skill(root: Path, name: str, description: str, body: str = "body") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nlicense: MIT\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return d / "SKILL.md"


# --------------------------------------------------------------------------
# discover_skills
# --------------------------------------------------------------------------


def test_discover_parses_frontmatter_only(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "Trigger for alpha.", body="A very long body " * 100)
    _write_skill(tmp_path, "beta", "Trigger for beta.")
    metas = skills_context.discover_skills([tmp_path])
    assert [m.name for m in metas] == ["alpha", "beta"]  # sorted by name
    alpha = next(m for m in metas if m.name == "alpha")
    assert alpha.description == "Trigger for alpha."
    assert alpha.path.name == "SKILL.md"


def test_discover_skips_missing_dirs_and_bad_frontmatter(tmp_path: Path) -> None:
    _write_skill(tmp_path, "good", "Has a trigger.")
    # A SKILL.md with no frontmatter is not a valid injectable skill.
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("# just a heading, no frontmatter\n", encoding="utf-8")
    metas = skills_context.discover_skills([tmp_path, tmp_path / "does-not-exist"])
    assert [m.name for m in metas] == ["good"]


def test_discover_dedupes_by_name_first_dir_wins(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_skill(d1, "dup", "From d1.")
    _write_skill(d2, "dup", "From d2.")
    metas = skills_context.discover_skills([d1, d2])
    assert len(metas) == 1
    assert metas[0].description == "From d1."


def test_discover_respects_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a tiny cap, the frontmatter past the cap is not read (name missing)."""
    _write_skill(tmp_path, "capped", "A description well past a tiny cap boundary.")
    # Cap smaller than the frontmatter block: the truncated head lacks a closing
    # fence / the description line, so parsing yields no valid meta.
    monkeypatch.setattr(skills_context, "MAX_SKILL_FILE_SIZE", 10)
    metas = skills_context.discover_skills([tmp_path])
    assert metas == []


def test_max_skill_file_size_matches_deepagents_cap() -> None:
    assert skills_context.MAX_SKILL_FILE_SIZE == 10 * 1024 * 1024


# --------------------------------------------------------------------------
# skills_for_role
# --------------------------------------------------------------------------


def _metas(*names: str) -> list[skills_context.SkillMeta]:
    return [skills_context.SkillMeta(n, f"trigger {n}", Path(f"/x/{n}/SKILL.md")) for n in names]


def test_skills_for_role_filters_by_manifest_roles() -> None:
    metas = _metas("a", "b", "c")
    roles = {"a": ("feature-dev",), "b": ("pr-review",), "c": ("feature-dev", "planner")}
    selected = skills_context.skills_for_role("feature-dev", metas, roles_by_name=roles)
    assert {m.name for m in selected} == {"a", "c"}


def test_skills_for_role_none_or_unknown_yields_empty() -> None:
    metas = _metas("a")
    roles = {"a": ("feature-dev",)}
    assert skills_context.skills_for_role(None, metas, roles_by_name=roles) == []
    assert skills_context.skills_for_role("nobody", metas, roles_by_name=roles) == []


def test_skills_for_role_uses_shipped_manifest_by_default() -> None:
    """Against the real manifest, write-tests is offered to feature-dev."""
    metas = _metas("write-tests", "review-security")
    selected = skills_context.skills_for_role("feature-dev", metas)
    assert {m.name for m in selected} == {"write-tests", "review-security"}
    planner = skills_context.skills_for_role("planner", _metas("spec-to-issues"))
    assert [m.name for m in planner] == ["spec-to-issues"]


# --------------------------------------------------------------------------
# render_skills_block
# --------------------------------------------------------------------------


def test_render_includes_name_description_and_path() -> None:
    block = skills_context.render_skills_block(_metas("write-tests"))
    assert "write-tests" in block
    assert "trigger write-tests" in block
    assert "/x/write-tests/SKILL.md" in block
    assert "Available skills" in block


def test_render_empty_selection_is_empty_string() -> None:
    assert skills_context.render_skills_block([]) == ""


# --------------------------------------------------------------------------
# skills_context_for_role (entry point) + env gate
# --------------------------------------------------------------------------


def test_context_for_role_gated_off_returns_empty(tmp_path: Path) -> None:
    _write_skill(tmp_path, "write-tests", "Trigger.")
    out = skills_context.skills_context_for_role(
        "feature-dev", dirs=[tmp_path], env={"ALFRED_SKILLS_INJECT": "0"}
    )
    assert out == ""


def test_context_for_role_default_on_appends_block(tmp_path: Path) -> None:
    _write_skill(tmp_path, "write-tests", "Derive tests from criteria.")
    out = skills_context.skills_context_for_role("feature-dev", dirs=[tmp_path], env={})
    assert "write-tests" in out
    assert "Available skills" in out


def test_context_for_role_no_role_returns_empty(tmp_path: Path) -> None:
    _write_skill(tmp_path, "write-tests", "Trigger.")
    assert skills_context.skills_context_for_role(None, dirs=[tmp_path], env={}) == ""


# --------------------------------------------------------------------------
# process.py wiring
# --------------------------------------------------------------------------


def test_process_appends_block_for_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process, "skills_context_for_role", lambda role: "SKILLS-BLOCK" if role else ""
    )
    out = process._with_skills_block("PROMPT", "feature-dev")
    assert out == "PROMPT\n\nSKILLS-BLOCK"


def test_process_omits_block_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "skills_context_for_role", lambda role: "")
    assert process._with_skills_block("PROMPT", "feature-dev") == "PROMPT"
    assert process._with_skills_block("PROMPT", None) == "PROMPT"


def test_process_swallows_injector_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(role):
        raise RuntimeError("brain down")

    monkeypatch.setattr(process, "skills_context_for_role", boom)
    # Behavior-preserving: an injector failure must never break a firing.
    assert process._with_skills_block("PROMPT", "feature-dev") == "PROMPT"


# --------------------------------------------------------------------------
# Role derivation from codename (the fix: injection with no caller change)
# --------------------------------------------------------------------------


def test_resolve_firing_role_derives_from_codename() -> None:
    assert process._resolve_firing_role(None, "lucius") == "feature-dev"
    assert process._resolve_firing_role(None, "drake") == "planner"
    assert process._resolve_firing_role(None, "rasalghul") == "pr-review"
    assert process._resolve_firing_role(None, "Lucius") == "feature-dev"  # case-insensitive


def test_resolve_firing_role_explicit_overrides_codename() -> None:
    assert process._resolve_firing_role("pr-review", "lucius") == "pr-review"


def test_resolve_firing_role_unknown_codename_is_none() -> None:
    assert process._resolve_firing_role(None, "automerge") is None
    assert process._resolve_firing_role(None, "") is None


def test_with_skills_block_derives_role_from_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process, "skills_context_for_role", lambda role: f"[{role}]" if role else ""
    )
    out = process._with_skills_block("PROMPT", None, "lucius")
    assert out == "PROMPT\n\n[feature-dev]"
    assert process._with_skills_block("PROMPT", None, "automerge") == "PROMPT"


def test_invoke_agent_engine_injects_role_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the role block reaches the prompt the engine actually runs."""
    monkeypatch.setattr(
        process, "skills_context_for_role", lambda role: f"[skills for {role}]" if role else ""
    )
    seen: dict[str, str] = {}

    def fake_claude(prompt, **kwargs):
        seen["prompt"] = prompt
        from agent_runner.result import ClaudeResult

        return ClaudeResult(
            success=True,
            subtype="success",
            num_turns=1,
            cost_usd=0.0,
            session_id=None,
            result_text="ok",
            raw={},
            stop_reason="end_turn",
            error_message=None,
        )

    result, engine = process.invoke_agent_engine(
        "do the thing",
        engine="claude",
        agent="lucius",
        firing_id="f1",
        workdir=Path("."),
        claude_allowed_tools="Read",
        timeout=10,
        role="feature-dev",
        claude_fn=fake_claude,
    )
    assert engine == "claude"
    assert result.success
    assert "do the thing" in seen["prompt"]
    assert "[skills for feature-dev]" in seen["prompt"]


def _fake_claude_capturing(seen: dict[str, str]):
    def fake_claude(prompt, **kwargs):
        seen["prompt"] = prompt
        from agent_runner.result import ClaudeResult

        return ClaudeResult(
            success=True,
            subtype="success",
            num_turns=1,
            cost_usd=0.0,
            session_id=None,
            result_text="ok",
            raw={},
            stop_reason="end_turn",
            error_message=None,
        )

    return fake_claude


def test_invoke_agent_engine_derives_role_from_codename(monkeypatch: pytest.MonkeyPatch) -> None:
    """No explicit role: the codename drives injection for every existing caller."""
    monkeypatch.setattr(
        process, "skills_context_for_role", lambda role: f"[skills for {role}]" if role else ""
    )
    seen: dict[str, str] = {}
    process.invoke_agent_engine(
        "do the thing",
        engine="claude",
        agent="lucius",  # -> feature-dev via the roster map
        firing_id="f1",
        workdir=Path("."),
        claude_allowed_tools="Read",
        timeout=10,
        claude_fn=_fake_claude_capturing(seen),
    )
    assert "do the thing" in seen["prompt"]
    assert "[skills for feature-dev]" in seen["prompt"]


def test_invoke_agent_engine_operational_codename_leaves_prompt_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operational codename with no skill role injects nothing."""
    monkeypatch.setattr(
        process, "skills_context_for_role", lambda role: f"[skills for {role}]" if role else ""
    )
    seen: dict[str, str] = {}
    process.invoke_agent_engine(
        "just this",
        engine="claude",
        agent="automerge",  # no skill role -> no injection
        firing_id="f1",
        workdir=Path("."),
        claude_allowed_tools="Read",
        timeout=10,
        claude_fn=_fake_claude_capturing(seen),
    )
    assert "just this" in seen["prompt"]
    assert "[skills for" not in seen["prompt"]
