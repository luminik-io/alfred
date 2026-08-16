"""Setup-status probes used by the desktop onboarding flow."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from server import setup as setup_mod  # noqa: E402


def _scope_cache_dir(cache_root: Path, *repos: Path) -> Path:
    canonical = sorted({str(repo.resolve()) for repo in repos})
    material = "".join(f"{path}\n" for path in canonical).encode("utf-8")
    return cache_root / "scopes" / hashlib.sha256(material).hexdigest()


def _stub_common(monkeypatch: pytest.MonkeyPatch) -> None:
    # Setup-status tests model a clean host unless a case explicitly installs
    # a fake binary. Binary discovery prepends standard package-manager paths,
    # so sanitizing PATH alone would still expose tools installed on the host.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        setup_mod,
        "gh_auth_status",
        lambda **_kwargs: {"ok": True, "account": "octocat", "detail": "Signed in."},
    )
    monkeypatch.setattr(
        setup_mod,
        "engine_clis",
        lambda **_kwargs: [
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "path": "/usr/local/bin/codex",
            }
        ],
    )
    monkeypatch.setattr(setup_mod, "selected_repos", lambda: ["octocat/web"])
    monkeypatch.setattr(setup_mod, "load_demo_cards", lambda: {})


def test_bootstrap_status_shares_one_deadline_across_slow_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Barrier(2)
    deadlines: dict[str, float | None] = {}

    def github_probe(*, deadline: float | None = None) -> dict[str, object]:
        deadlines["github"] = deadline
        started.wait(timeout=5)
        return {"ok": True, "account": "octocat", "detail": "Signed in."}

    def engine_probe(*, deadline: float | None = None) -> list[dict[str, object]]:
        deadlines["engines"] = deadline
        started.wait(timeout=5)
        return []

    def repo_probe(
        _repos: list[str],
        _env: dict[str, str],
        *,
        deadline: float | None = None,
    ) -> list[dict[str, object]]:
        deadlines["repos"] = deadline
        return []

    monkeypatch.setattr(setup_mod, "gh_auth_status", github_probe)
    monkeypatch.setattr(setup_mod, "engine_clis", engine_probe)
    monkeypatch.setattr(setup_mod, "_runtime_config_env", lambda: {})
    monkeypatch.setattr(setup_mod, "setup_board_repos", lambda _env: [])
    monkeypatch.setattr(setup_mod, "_setup_queue_repos_for_status", lambda _env: set())
    monkeypatch.setattr(setup_mod, "_selected_repo_local_paths", repo_probe)
    monkeypatch.setattr(setup_mod, "code_memory_status", lambda _env: {})
    monkeypatch.setattr(setup_mod, "_code_memory_coverage", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(setup_mod, "capability_status", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(setup_mod, "install_inventory", lambda **_kwargs: {})
    monkeypatch.setattr(setup_mod, "first_run_readiness_status", lambda **_kwargs: {})
    monkeypatch.setattr(setup_mod, "load_demo_cards", lambda: {})

    before = time.monotonic()
    setup_mod.bootstrap_status()

    assert deadlines["github"] == deadlines["engines"] == deadlines["repos"]
    assert deadlines["github"] is not None
    assert before < deadlines["github"] <= before + 10.5


def test_default_hybrid_route_blocks_on_claude_auth_even_when_codex_is_ready() -> None:
    ready, detail = setup_mod._engine_route_status(
        [
            {
                "name": "claude",
                "display_name": "Claude Code",
                "installed": True,
                "ready": False,
                "state": "auth_required",
            },
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "state": "ready",
            },
        ]
    )

    assert ready is False
    assert "Claude Code blocks the default hybrid route" in detail


def test_bootstrap_status_blocks_when_claude_auth_stops_default_hybrid_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        setup_mod,
        "engine_clis",
        lambda **_kwargs: [
            {
                "name": "claude",
                "display_name": "Claude Code",
                "installed": True,
                "ready": False,
                "state": "auth_required",
            },
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "state": "ready",
            },
        ],
    )

    payload = setup_mod.bootstrap_status()
    checks = {row["key"]: row for row in payload["first_run"]["checks"]}

    assert payload["engine_ready"] is False
    assert checks["engine_clis"]["ready"] is False
    assert "blocks the default hybrid route" in checks["engine_clis"]["detail"]


def test_default_hybrid_route_uses_codex_when_claude_is_missing() -> None:
    ready, detail = setup_mod._engine_route_status(
        [
            {
                "name": "claude",
                "display_name": "Claude Code",
                "installed": False,
                "ready": False,
                "state": "missing",
            },
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "state": "ready",
            },
        ]
    )

    assert ready is True
    assert detail == "Ready via Codex fallback."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "hybrid"),
        ("", "hybrid"),
        ("  ", "hybrid"),
        ("removed-engine", "disabled"),
        (" CODEX ", "codex"),
    ],
)
def test_configured_setup_engine_mode_preserves_hybrid_default(
    raw: str | None,
    expected: str,
) -> None:
    env = {} if raw is None else {"ALFRED_ENGINE": raw}

    assert setup_mod._configured_engine_mode(env) == expected


def test_bootstrap_status_blocks_invalid_fleet_engine_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALFRED_ENGINE", "removed-engine")

    payload = setup_mod.bootstrap_status()
    checks = {row["key"]: row for row in payload["first_run"]["checks"]}

    assert payload["engine_ready"] is False
    assert checks["engine_clis"]["ready"] is False
    assert checks["engine_clis"]["detail"] == (
        "ALFRED_ENGINE is invalid; coding engine dispatch is disabled."
    )
    assert checks["engine_clis"]["action"] == (
        "Set ALFRED_ENGINE to claude, codex, or hybrid, then recheck setup."
    )


def test_bootstrap_status_requires_claude_when_fleet_mode_is_claude(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALFRED_ENGINE", "claude")
    monkeypatch.setattr(
        setup_mod,
        "engine_clis",
        lambda **_kwargs: [
            {
                "name": "claude",
                "display_name": "Claude Code",
                "installed": False,
                "ready": False,
                "state": "missing",
            },
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "state": "ready",
            },
        ],
    )

    payload = setup_mod.bootstrap_status()
    checks = {row["key"]: row for row in payload["first_run"]["checks"]}

    assert payload["engine_ready"] is False
    assert checks["engine_clis"]["ready"] is False
    assert "configured Claude Code route" in checks["engine_clis"]["detail"]


def test_bootstrap_status_uses_ready_codex_when_fleet_mode_is_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALFRED_ENGINE", "codex")
    monkeypatch.setattr(
        setup_mod,
        "engine_clis",
        lambda **_kwargs: [
            {
                "name": "claude",
                "display_name": "Claude Code",
                "installed": True,
                "ready": False,
                "state": "auth_required",
            },
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "state": "ready",
            },
        ],
    )

    payload = setup_mod.bootstrap_status()
    checks = {row["key"]: row for row in payload["first_run"]["checks"]}

    assert payload["engine_ready"] is True
    assert checks["engine_clis"]["ready"] is True
    assert checks["engine_clis"]["detail"] == "Ready via configured Codex route."


def test_engine_inventory_uses_scheduler_selected_claude_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def inventory(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(setup_mod, "_runtime_config_env", lambda: {"PATH": "/usr/bin"})
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_environment_lookup",
        lambda *_args, **_kwargs: setup_mod.runtime_facade.SchedulerEnvironmentLookup(
            value="/profiles/secondary",
            available=True,
        ),
    )
    monkeypatch.setattr(setup_mod.runtime_facade, "engine_inventory", inventory)

    setup_mod.engine_clis(deadline=time.monotonic() + 5)

    assert captured["environ"]["CLAUDE_CONFIG_DIR"] == "/profiles/secondary"


def test_engine_inventory_fails_closed_when_scheduler_profile_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_mod, "_runtime_config_env", lambda: {"PATH": "/usr/bin"})
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_environment_lookup",
        lambda *_args, **_kwargs: setup_mod.runtime_facade.SchedulerEnvironmentLookup(),
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "engine_inventory",
        lambda **_kwargs: [
            {
                "name": "claude",
                "display_name": "Claude Code",
                "installed": True,
                "ready": True,
                "state": "ready",
                "failures": [],
            },
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "state": "ready",
                "failures": [],
            },
        ],
    )

    engines = setup_mod.engine_clis(deadline=time.monotonic() + 5)
    by_name = {engine["name"]: engine for engine in engines}

    assert by_name["claude"]["ready"] is False
    assert by_name["claude"]["state"] == "probe_failed"
    assert by_name["claude"]["failures"] == ["profile_lookup_failed"]
    assert by_name["codex"]["ready"] is True


def test_engine_inventory_keeps_static_profile_after_deadline_on_unsupported_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_mod,
        "_runtime_config_env",
        lambda: {"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/profiles/static"},
    )
    monkeypatch.setattr(
        setup_mod,
        "_runtime_env_file_value",
        lambda *_args, **_kwargs: "/profiles/static",
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_supported",
        lambda: False,
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_environment_lookup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an expired deadline must not start a scheduler command")
        ),
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "engine_inventory",
        lambda **kwargs: (
            captured.update(kwargs)
            or [
                {
                    "name": "claude",
                    "display_name": "Claude Code",
                    "installed": True,
                    "ready": True,
                    "state": "ready",
                    "failures": [],
                }
            ]
        ),
    )

    engines = setup_mod.engine_clis(deadline=time.monotonic() - 1)

    assert engines[0]["ready"] is True
    assert captured["environ"]["CLAUDE_CONFIG_DIR"] == "/profiles/static"


def test_engine_inventory_keeps_static_claude_profile_without_manager_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    runtime_home = tmp_path / ".alfred"
    runtime_home.mkdir()
    (runtime_home / ".env").write_text(
        "CLAUDE_CONFIG_DIR=/profiles/static\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_mod,
        "_runtime_config_env",
        lambda: {
            "ALFRED_HOME": str(runtime_home),
            "HOME": str(tmp_path),
            "PATH": "/usr/bin",
            "CLAUDE_CONFIG_DIR": "/profiles/shell-only",
        },
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_environment_lookup",
        lambda *_args, **_kwargs: setup_mod.runtime_facade.SchedulerEnvironmentLookup(
            available=True
        ),
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "engine_inventory",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    setup_mod.engine_clis(deadline=time.monotonic() + 5)

    assert captured["environ"]["CLAUDE_CONFIG_DIR"] == "/profiles/static"


def test_engine_inventory_excludes_shell_only_claude_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    runtime_home = tmp_path / ".alfred"
    runtime_home.mkdir()
    monkeypatch.setattr(
        setup_mod,
        "_runtime_config_env",
        lambda: {
            "ALFRED_HOME": str(runtime_home),
            "HOME": str(tmp_path),
            "PATH": "/usr/bin",
            "CLAUDE_CONFIG_DIR": "/profiles/shell-only",
        },
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_environment_lookup",
        lambda *_args, **_kwargs: setup_mod.runtime_facade.SchedulerEnvironmentLookup(
            available=True
        ),
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "engine_inventory",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    setup_mod.engine_clis(deadline=time.monotonic() + 5)

    assert "CLAUDE_CONFIG_DIR" not in captured["environ"]


def test_engine_inventory_can_probe_the_current_process_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    runtime_home = tmp_path / ".alfred"
    runtime_home.mkdir()
    (runtime_home / ".env").write_text(
        "CLAUDE_CONFIG_DIR=/profiles/static\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/profiles/conversation-process")
    monkeypatch.setattr(
        setup_mod,
        "_runtime_config_env",
        lambda: {
            "ALFRED_HOME": str(runtime_home),
            "HOME": str(tmp_path),
            "PATH": "/usr/bin",
        },
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "scheduler_environment_lookup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("process-profile probes must not query the scheduler")
        ),
    )
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "engine_inventory",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    setup_mod.engine_clis(
        deadline=time.monotonic() + 5,
        environment="process",
    )

    assert captured["environ"]["CLAUDE_CONFIG_DIR"] == "/profiles/conversation-process"


def _isolate_launcher_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    alfred_home = tmp_path / ".alfred"
    home.mkdir(exist_ok=True)
    alfred_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(alfred_home))
    monkeypatch.delenv("ALFRED_CODE_MEMORY_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MAP_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_WORKSPACE_SUBDIR", raising=False)
    monkeypatch.delenv("ALFRED_CONTEXT_GOVERNOR", raising=False)
    monkeypatch.delenv("ARCHITECT_AUTO_EXECUTE", raising=False)
    monkeypatch.delenv("ARCHITECT_PARENT_REPO", raising=False)
    monkeypatch.delenv("WORKSPACE_SUBDIR", raising=False)


def test_bootstrap_rejects_detected_candidate_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        setup_mod,
        "engine_clis",
        lambda **_kwargs: [
            {
                "name": "opencode",
                "display_name": "OpenCode",
                "installed": True,
                "protocol_compatible": True,
                "ready": False,
                "dispatchable": False,
                "state": "needs_validation",
                "detail": "OpenCode still needs a deep permission probe.",
                "path": "/usr/local/bin/opencode",
                "version": "opencode 2.0.0",
                "capabilities": ["text"],
                "failures": ["deep_probe_required"],
            }
        ],
    )

    payload = setup_mod.bootstrap_status()
    checks = {row["key"]: row for row in payload["first_run"]["checks"]}

    assert payload["engine_ready"] is False
    assert checks["engine_clis"]["ready"] is False
    assert checks["engine_clis"]["detail"] == "Detected but not ready: OpenCode."


def _git_repo_with_origin(path: Path, slug: str) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", f"https://github.com/{slug}.git"],
        check=True,
    )


def test_code_memory_coverage_requires_exact_github_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = workspace / "web"
    _git_repo_with_origin(repo, "octocat/web")
    env = {
        "WORKSPACE_ROOT": str(workspace),
        "WORKSPACE_SUBDIR": "",
    }
    code_memory = {
        "enabled": True,
        "binary": {"resolved": True},
        "index_present": True,
        "repos": {"selected": ["web"]},
    }

    matching = setup_mod._code_memory_coverage(["octocat/web"], code_memory, env)
    wrong_owner = setup_mod._code_memory_coverage(["other/web"], code_memory, env)
    disabled = setup_mod._code_memory_coverage(
        ["octocat/web"], {**code_memory, "enabled": False}, env
    )
    missing_binary = setup_mod._code_memory_coverage(
        ["octocat/web"], {**code_memory, "binary": {"resolved": False}}, env
    )

    assert matching["ready"] is True
    assert matching["covered"] == ["octocat/web"]
    assert wrong_owner["ready"] is False
    assert wrong_owner["covered"] == []
    assert wrong_owner["missing"] == ["other/web"]
    assert disabled["ready"] is False
    assert missing_binary["ready"] is False


def test_bootstrap_status_reports_code_memory_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_AUTOFETCH", raising=False)

    payload = setup_mod.bootstrap_status()

    code_memory = payload["code_memory"]
    assert code_memory["enabled"] is True
    assert code_memory["autofetch"] is True
    assert code_memory["binary"]["resolved"] is False
    assert code_memory["binary"]["source"] == "none"
    assert code_memory["version_pin"] == "v0.8.1"
    assert code_memory["repo"] == "DeusData/codebase-memory-mcp"
    assert code_memory["index_dir"] == str(tmp_path / ".alfred" / "state" / "code-memory")
    assert code_memory["index_present"] is False
    assert code_memory["repos"] == {
        "configured": [],
        "configured_existing": [],
        "discovered": [],
        "selected": [],
        "source": "unconfigured",
        "count": 0,
    }
    capability = next(
        item for item in payload["capability_plane"]["capabilities"] if item["key"] == "code_graph"
    )
    assert capability["state"] == "needs_scope"
    assert capability["install_hint"] == (
        "Set ALFRED_CODE_MEMORY_REPOS or ALFRED_CODE_MAP_REPOS, then run "
        "`alfred code-memory index`."
    )
    assert payload["code_memory_coverage"] == {
        "ready": False,
        "covered": [],
        "missing": [],
        "detected": [],
    }


def test_bootstrap_status_includes_ready_first_run_checklist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    workspace = tmp_path / "workspace"
    _git_repo_with_origin(workspace / "web", "octocat/web")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_QUEUE_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "octocat/web")

    home = tmp_path / ".alfred"
    (home / "state").mkdir(parents=True)
    (home / "state" / "server-token").write_text("secret\n", encoding="utf-8")
    conf = home / "launchd" / "agents.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "alfred.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tSingle-repo engineer\n",
        encoding="utf-8",
    )

    payload = setup_mod.bootstrap_status()
    first_run = payload["first_run"]
    by_key = {check["key"]: check for check in first_run["checks"]}

    assert payload["ready"] is True
    assert first_run["ready"] is True
    assert first_run["summary"]["required_ready"] == first_run["summary"]["required_total"]
    assert first_run["summary"]["blockers"] == []
    assert by_key["repo_local_paths"]["ready"] is True
    assert by_key["repo_local_paths"]["detected"] == [
        {
            "repo": "octocat/web",
            "path": str(workspace / "web"),
            "source": "workspace",
            "exists": True,
            "is_git_repo": True,
            "github_remote_name": "origin",
            "github_remote_repo": "octocat/web",
            "identity_matches": True,
            "ready": True,
            "reason": None,
        }
    ]
    assert by_key["architect_parent_repo"]["state"] == "optional"


def test_bootstrap_status_preserves_disabled_context_governor_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("ALFRED_CONTEXT_GOVERNOR", "0")

    payload = setup_mod.bootstrap_status()
    first_run_by_key = {check["key"]: check for check in payload["first_run"]["checks"]}
    capability_by_key = {item["key"]: item for item in payload["capability_plane"]["capabilities"]}

    assert capability_by_key["context_compression"]["state"] == "disabled"
    assert capability_by_key["context_compression"]["installed"] is False
    assert capability_by_key["context_compression"]["enabled"] is False
    assert first_run_by_key["context_compression"]["tier"] == "optional"
    assert first_run_by_key["context_compression"]["state"] == "disabled"
    assert first_run_by_key["context_compression"]["ready"] is False
    assert first_run_by_key["context_compression"]["action"] == ""
    assert payload["first_run"]["summary"]["recommended_total"] == 2


def test_bootstrap_status_first_run_blocks_missing_queue_and_local_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.delenv("ALFRED_QUEUE_REPOS", raising=False)
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "octocat/web")

    home = tmp_path / ".alfred"
    (home / "state").mkdir(parents=True)
    (home / "state" / "server-token").write_text("secret\n", encoding="utf-8")
    conf = home / "launchd" / "agents.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "alfred.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tSingle-repo engineer\n",
        encoding="utf-8",
    )

    first_run = setup_mod.bootstrap_status()["first_run"]
    by_key = {check["key"]: check for check in first_run["checks"]}

    assert first_run["ready"] is False
    assert first_run["headline"] == "2 required setup items need action."
    assert set(first_run["summary"]["blockers"]) == {"queue_coverage", "repo_local_paths"}
    assert by_key["queue_coverage"]["detail"] == (
        "Queue actions are missing selected repos: octocat/web."
    )
    assert by_key["repo_local_paths"]["detected"] == [
        {
            "repo": "octocat/web",
            "path": str(workspace / "web"),
            "source": "workspace",
            "exists": False,
            "is_git_repo": False,
            "github_remote_name": None,
            "github_remote_repo": None,
            "identity_matches": False,
            "ready": False,
            "reason": "missing",
        }
    ]


def test_bootstrap_status_first_run_uses_singular_blocker_headline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_QUEUE_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "octocat/web")

    home = tmp_path / ".alfred"
    (home / "state").mkdir(parents=True)
    (home / "state" / "server-token").write_text("secret\n", encoding="utf-8")
    conf = home / "launchd" / "agents.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "alfred.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tSingle-repo engineer\n",
        encoding="utf-8",
    )

    first_run = setup_mod.bootstrap_status()["first_run"]

    assert first_run["ready"] is False
    assert first_run["summary"]["blockers"] == ["repo_local_paths"]
    assert first_run["headline"] == "1 required setup item needs action."
    by_key = {check["key"]: check for check in first_run["checks"]}
    assert by_key["repo_local_paths"]["detail"] == "1 selected repo needs local path mapping."


def test_bootstrap_status_first_run_blocks_invalid_explicit_local_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    workspace = tmp_path / "workspace"
    _git_repo_with_origin(workspace / "web", "octocat/web")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_QUEUE_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", f"octocat/web={tmp_path / 'missing-web'}")

    home = tmp_path / ".alfred"
    (home / "state").mkdir(parents=True)
    (home / "state" / "server-token").write_text("secret\n", encoding="utf-8")
    conf = home / "launchd" / "agents.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "alfred.senior-dev\tsenior-dev.py\tinterval:1200\tyes\t\tSingle-repo engineer\n",
        encoding="utf-8",
    )

    first_run = setup_mod.bootstrap_status()["first_run"]
    by_key = {check["key"]: check for check in first_run["checks"]}

    assert first_run["ready"] is False
    assert first_run["summary"]["blockers"] == ["repo_local_paths"]
    assert by_key["repo_local_paths"]["detected"] == [
        {
            "repo": "octocat/web",
            "path": str(tmp_path / "missing-web"),
            "source": "map",
            "exists": False,
            "is_git_repo": False,
            "github_remote_name": None,
            "github_remote_repo": None,
            "identity_matches": False,
            "ready": False,
            "reason": "missing",
        }
    ]


def test_bootstrap_status_reports_configured_code_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    index_dir = tmp_path / "index"
    workspace = tmp_path / "workspace"
    api = workspace / "api"
    web = workspace / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    graph_dir = _scope_cache_dir(index_dir / ".cache" / "codebase-memory-mcp", api, web)
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.db").write_text("ok", encoding="utf-8")

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api, web, api")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    payload = setup_mod.bootstrap_status()

    code_memory = payload["code_memory"]
    assert code_memory["binary"] == {
        "resolved": True,
        "path": str(binary),
        "source": "env",
        "configured": str(binary),
    }
    assert code_memory["index_present"] is True
    assert code_memory["repos"] == {
        "configured": ["api", "web"],
        "configured_existing": ["api", "web"],
        "discovered": [],
        "selected": ["api", "web"],
        "source": "configured",
        "count": 2,
    }
    assert code_memory["detail"] == "Code-memory binary and index are present."


def test_bootstrap_status_rejects_stale_configured_code_memory_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    index_dir = tmp_path / "index"
    graph_dir = index_dir / ".cache" / "codebase-memory-mcp"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.db").write_text("stale", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "removed-repo")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    payload = setup_mod.bootstrap_status()

    code_memory = payload["code_memory"]
    assert code_memory["repos"]["source"] == "configured-missing"
    assert code_memory["repos"]["selected"] == []
    assert code_memory["detail"] == (
        "Configured code-memory repositories do not resolve to git checkouts."
    )
    capability = next(
        item for item in payload["capability_plane"]["capabilities"] if item["key"] == "code_graph"
    )
    assert capability["state"] == "needs_scope"


def test_bootstrap_status_ignores_legacy_index_dir_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / "legacy.db").write_text("stale", encoding="utf-8")
    workspace = tmp_path / "workspace"
    (workspace / "api" / ".git").mkdir(parents=True)

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["index_dir"] == str(index_dir)
    assert code_memory["graph_dir"] == str(
        _scope_cache_dir(index_dir / ".cache" / "codebase-memory-mcp", workspace / "api")
    )
    assert code_memory["index_present"] is False
    assert (
        code_memory["detail"]
        == "Code-memory binary is present; run an index before relying on graph queries."
    )


def test_bootstrap_status_checks_code_memory_home_cache_for_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    index_dir = tmp_path / "legacy-index"
    code_home = tmp_path / "code-memory-home"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    graph_dir = _scope_cache_dir(code_home / ".cache" / "codebase-memory-mcp", repo)
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.db").write_text("ok", encoding="utf-8")

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_HOME", str(code_home))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    payload = setup_mod.bootstrap_status()

    code_memory = payload["code_memory"]
    assert code_memory["index_dir"] == str(index_dir)
    assert code_memory["index_home"] == str(code_home)
    assert code_memory["graph_dir"] == str(graph_dir)
    assert code_memory["index_present"] is True
    assert code_memory["detail"] == "Code-memory binary and index are present."


def test_bootstrap_status_checks_upstream_cbm_cache_dir_for_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    index_dir = tmp_path / "legacy-index"
    code_home = tmp_path / "code-memory-home"
    cbm_cache = tmp_path / "upstream-cache"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    graph_dir = _scope_cache_dir(cbm_cache, repo)
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.db").write_text("ok", encoding="utf-8")

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_HOME", str(code_home))
    monkeypatch.setenv("CBM_CACHE_DIR", str(cbm_cache))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["index_dir"] == str(index_dir)
    assert code_memory["index_home"] == str(code_home)
    assert code_memory["graph_dir"] == str(graph_dir)
    assert code_memory["index_present"] is True
    assert code_memory["detail"] == "Code-memory binary and index are present."


def test_bootstrap_status_does_not_reuse_graph_from_wider_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    api = workspace / "api"
    web = workspace / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    cache_root = tmp_path / "cache-root"
    old_graph_dir = _scope_cache_dir(cache_root, api, web)
    old_graph_dir.mkdir(parents=True)
    (old_graph_dir / "graph.db").write_text("old wider graph", encoding="utf-8")
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.setenv("CBM_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["graph_dir"] == str(_scope_cache_dir(cache_root, api))
    assert code_memory["graph_dir"] != str(old_graph_dir)
    assert code_memory["index_present"] is False
    assert "run an index" in code_memory["detail"]


def test_launcher_and_setup_status_agree_on_scope_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    api = workspace / "api"
    web = workspace / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    cache_root = tmp_path / "cache-root"
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "ALFRED_HOME": str(tmp_path / ".alfred"),
        "ALFRED_CODE_MEMORY_BIN": str(binary),
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
        "ALFRED_CODE_MEMORY_REPOS": "web,api",
        "ALFRED_CODE_MAP_REPOS": "",
        "CBM_CACHE_DIR": str(cache_root),
        "WORKSPACE_ROOT": str(workspace),
        "WORKSPACE_SUBDIR": "",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    doctor = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert doctor.returncode == 0, doctor.stderr
    assert f"cache-dir:   {code_memory['graph_dir']}" in doctor.stderr
    assert code_memory["graph_dir"] == str(_scope_cache_dir(cache_root, api, web))


@pytest.mark.parametrize(
    ("relative_setting", "relative_value", "cache_suffix"),
    [
        ("CBM_CACHE_DIR", "relative-cache", Path("relative-cache")),
        (
            "ALFRED_CODE_MEMORY_HOME",
            "relative-home",
            Path("relative-home/.cache/codebase-memory-mcp"),
        ),
        (
            "ALFRED_CODE_MEMORY_INDEX_DIR",
            "relative-index",
            Path("relative-index/.cache/codebase-memory-mcp"),
        ),
    ],
)
def test_launcher_and_setup_resolve_relative_cache_roots_from_runtime_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative_setting: str,
    relative_value: str,
    cache_suffix: Path,
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    runtime = tmp_path / ".alfred"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    launcher_cwd = tmp_path / "launcher-cwd"
    launcher_cwd.mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "ALFRED_HOME": str(runtime),
        "ALFRED_CODE_MEMORY_BIN": str(binary),
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
        "ALFRED_CODE_MEMORY_REPOS": "api",
        "ALFRED_CODE_MAP_REPOS": "",
        "WORKSPACE_ROOT": str(workspace),
        "WORKSPACE_SUBDIR": "",
        relative_setting: relative_value,
    }
    if relative_setting != "CBM_CACHE_DIR":
        env.pop("CBM_CACHE_DIR", None)
    if relative_setting != "ALFRED_CODE_MEMORY_HOME":
        env.pop("ALFRED_CODE_MEMORY_HOME", None)
    if relative_setting != "ALFRED_CODE_MEMORY_INDEX_DIR":
        env.pop("ALFRED_CODE_MEMORY_INDEX_DIR", None)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if "CBM_CACHE_DIR" not in env:
        monkeypatch.delenv("CBM_CACHE_DIR", raising=False)
    if "ALFRED_CODE_MEMORY_HOME" not in env:
        monkeypatch.delenv("ALFRED_CODE_MEMORY_HOME", raising=False)
    if "ALFRED_CODE_MEMORY_INDEX_DIR" not in env:
        monkeypatch.delenv("ALFRED_CODE_MEMORY_INDEX_DIR", raising=False)

    doctor = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "doctor"],
        cwd=launcher_cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.bootstrap_status()["code_memory"]
    expected = _scope_cache_dir(runtime / cache_suffix, repo)

    assert doctor.returncode == 0, doctor.stderr
    assert f"cache-dir:   {expected}" in doctor.stderr
    assert code_memory["graph_dir"] == str(expected)


@pytest.mark.parametrize(
    ("tilde_setting", "cache_suffix"),
    [
        ("CBM_CACHE_DIR", Path("cache")),
        (
            "ALFRED_CODE_MEMORY_HOME",
            Path("memory/.cache/codebase-memory-mcp"),
        ),
        (
            "ALFRED_CODE_MEMORY_INDEX_DIR",
            Path("index/.cache/codebase-memory-mcp"),
        ),
    ],
)
def test_launcher_and_setup_resolve_tilde_cache_roots_without_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tilde_setting: str,
    cache_suffix: Path,
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    runtime = tmp_path / ".alfred"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    launcher_cwd = tmp_path / "launcher-cwd"
    launcher_cwd.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "ALFRED_HOME": str(runtime),
        "ALFRED_CODE_MEMORY_BIN": str(binary),
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
        "ALFRED_CODE_MEMORY_REPOS": "api",
        "ALFRED_CODE_MAP_REPOS": "",
        "WORKSPACE_ROOT": str(workspace),
        "WORKSPACE_SUBDIR": "",
        tilde_setting: f"~/{cache_suffix.parts[0]}",
    }
    monkeypatch.delenv("HOME", raising=False)
    for key in (
        "CBM_CACHE_DIR",
        "ALFRED_CODE_MEMORY_HOME",
        "ALFRED_CODE_MEMORY_INDEX_DIR",
    ):
        if key not in env:
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    doctor = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "doctor"],
        cwd=launcher_cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.bootstrap_status()["code_memory"]
    expected = _scope_cache_dir(runtime / cache_suffix, repo)

    assert doctor.returncode == 0, doctor.stderr
    assert "unbound variable" not in doctor.stderr
    assert f"cache-dir:   {expected}" in doctor.stderr
    assert code_memory["graph_dir"] == str(expected)


def test_bootstrap_status_ignores_empty_code_memory_cache_scaffolding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    code_home = tmp_path / "code-memory-home"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    graph_dir = _scope_cache_dir(code_home / ".cache" / "codebase-memory-mcp", repo)
    graph_dir.mkdir(parents=True)

    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(binary))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_HOME", str(code_home))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["index_home"] == str(code_home)
    assert code_memory["graph_dir"] == str(graph_dir)
    assert code_memory["index_present"] is False
    assert "run an index" in code_memory["detail"]


def test_code_memory_status_ignores_legacy_alfredrc_without_process_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    alfred_home = tmp_path / "runtime"
    home.mkdir()
    alfred_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ALFRED_HOME", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    (home / ".alfredrc").write_text(
        "\n".join(
            [
                f"ALFRED_HOME={alfred_home}",
                "ALFRED_CODE_MEMORY_MCP=0",
                "ALFRED_CODE_MEMORY_AUTOFETCH=0",
            ]
        ),
        encoding="utf-8",
    )

    code_memory = setup_mod.code_memory_status()

    assert code_memory["enabled"] is True
    assert code_memory["autofetch"] is True
    assert code_memory["index_dir"] == str(home / ".alfred" / "state" / "code-memory")


def test_setup_config_prefers_process_env_over_runtime_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text(
        "\n".join(
            [
                "CLAUDE_BIN=/file/claude",
                "CODEX_BIN=/file/codex",
                "GH_ORG=file-org",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    claude_bin = tmp_path / "env-claude"
    codex_bin = tmp_path / "env-codex"
    for binary in (claude_bin, codex_bin):
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(claude_bin))
    monkeypatch.setenv("CODEX_BIN", str(codex_bin))
    monkeypatch.setenv("GH_ORG", "env-org")
    monkeypatch.setattr(setup_mod, "selected_repos", lambda: [])

    launcher_env = setup_mod._code_memory_launcher_env()
    engines = {item["name"]: item for item in setup_mod.engine_clis()}

    assert launcher_env["CLAUDE_BIN"] == str(claude_bin)
    assert launcher_env["CODEX_BIN"] == str(codex_bin)
    assert launcher_env["GH_ORG"] == "env-org"
    assert engines["claude"]["path"] == str(claude_bin)
    assert engines["codex"]["path"] == str(codex_bin)
    assert setup_mod._repo_list_owners() == ["env-org"]


def test_engine_inventory_does_not_execute_candidate_harnesses_during_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    marker = tmp_path / "candidate-called"
    opencode = tmp_path / "opencode"
    opencode.write_text(
        f"#!/bin/sh\nprintf called >> {marker}\n",
        encoding="utf-8",
    )
    opencode.chmod(0o755)
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.setenv("CLAUDE_BIN", str(tmp_path / "missing-claude"))
    monkeypatch.setenv("CODEX_BIN", str(tmp_path / "missing-codex"))
    monkeypatch.setenv("OPENCODE_BIN", str(opencode))

    engines = {item["name"]: item for item in setup_mod.engine_clis()}

    assert marker.exists() is False
    assert engines["opencode"]["installed"] is True
    assert engines["opencode"]["state"] == "needs_validation"
    assert engines["opencode"]["protocol_compatible"] is False


def test_setup_config_reads_runtime_env_file_but_not_legacy_alfredrc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    home.mkdir()
    runtime.mkdir()
    (home / ".alfredrc").write_text(
        "\n".join(
            [
                "CLAUDE_BIN=/stale/claude",
                "CODEX_BIN=/stale/codex",
                "GH_BIN=/stale/gh",
                "GH_ORG=stale-org",
            ]
        ),
        encoding="utf-8",
    )
    (runtime / ".env").write_text(
        "\n".join(
            [
                f"CODEX_BIN={tmp_path / 'runtime-codex'}",
                "GH_ORG=runtime-org",
            ]
        ),
        encoding="utf-8",
    )
    runtime_codex = tmp_path / "runtime-codex"
    runtime_codex.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime_codex.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.delenv("GH_BIN", raising=False)
    monkeypatch.delenv("GH_ORG", raising=False)
    monkeypatch.setattr(setup_mod, "selected_repos", lambda: [])
    monkeypatch.setattr(
        setup_mod.runtime_facade,
        "engine_inventory",
        lambda **kwargs: [
            {"name": "claude", "path": None},
            {"name": "codex", "path": kwargs["environ"]["CODEX_BIN"]},
        ],
    )

    engines = {item["name"]: item for item in setup_mod.engine_clis()}

    assert setup_mod._setup_config_value("CODEX_BIN") == str(runtime_codex)
    assert setup_mod._setup_config_value("CLAUDE_BIN") == ""
    assert setup_mod._gh_bin() == "gh"
    assert engines["codex"]["path"] == str(runtime_codex)
    assert engines["claude"]["path"] is None
    assert setup_mod._repo_list_owners() == ["runtime-org"]


def test_gh_subprocess_env_drops_empty_path_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")

    parts = setup_mod._gh_subprocess_env()["PATH"].split(os.pathsep)

    assert "" not in parts
    assert "." not in parts
    assert str(home / ".local" / "bin") in parts


def test_selected_repos_preserves_shipped_and_bridge_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("ALFRED_QUEUE_REPOS", raising=False)
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "octocat/api, octocat/web")

    assert setup_mod.selected_repos() == ["octocat/api", "octocat/web"]


def test_selected_repos_ignores_queue_only_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.setenv("ALFRED_QUEUE_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "acme/frontend")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "acme/api")

    assert setup_mod.selected_repos() == ["acme/api", "acme/frontend"]


def test_selected_repos_reads_runtime_env_file_but_not_alfredrc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    home.mkdir()
    runtime.mkdir()
    (home / ".alfredrc").write_text("ALFRED_QUEUE_REPOS=octocat/stale\n", encoding="utf-8")
    (runtime / ".env").write_text(
        "ALFRED_QUEUE_REPOS=octocat/web\nALFRED_BRIDGE_REPOS=octocat/api\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("ALFRED_QUEUE_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_SHIPPED_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_BRIDGE_REPOS", raising=False)

    assert setup_mod.selected_repos() == ["octocat/api"]


def test_install_inventory_uses_active_serve_home_not_launcher_rc_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    home = tmp_path / "home"
    active_home = tmp_path / "active-runtime"
    launcher_home = tmp_path / "launcher-runtime"
    home.mkdir()
    active_home.mkdir()
    launcher_home.mkdir()
    (home / ".alfredrc").write_text(
        f"ALFRED_HOME={launcher_home}\nALFRED_SHIPPED_REPOS=launcher/api\n",
        encoding="utf-8",
    )
    (active_home / ".env").write_text(
        "GH_ORG=active\nALFRED_SHIPPED_REPOS=active/api\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(active_home))
    monkeypatch.delenv("ALFRED_QUEUE_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_SHIPPED_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_BRIDGE_REPOS", raising=False)

    status = setup_mod.bootstrap_status()
    inventory = status["install"]

    assert status["repos"]["selected"] == ["active/api"]
    assert inventory["alfred_home"] == str(active_home)
    assert inventory["env_path"] == str(active_home / ".env")
    assert inventory["env_present"] is True


def test_install_inventory_reports_roster_theme_and_repo_local_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    alfred_home = tmp_path / ".alfred"
    theme_dir = alfred_home / "state" / "roster-theme"
    theme_dir.mkdir(parents=True)
    (theme_dir / "roster-theme.json").write_text(
        json.dumps(
            {
                "theme": "custom",
                "custom_names": {"architect": "Sherlock", "senior-dev": "Watson"},
                "custom_roles": {"architect": "Lead detective"},
                "updated_at": "2026-06-30T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "acme/site='../marketing/site' acme/api=/Users/example/api",
    )

    inventory = setup_mod.bootstrap_status()["install"]
    by_key = {item["key"]: item for item in inventory["items"]}

    assert inventory["roster_theme"] == {
        "theme": "custom",
        "label": "Custom",
        "path": str(theme_dir / "roster-theme.json"),
        "custom_names_count": 2,
        "custom_roles_count": 1,
        "updated_at": "2026-06-30T12:00:00Z",
    }
    assert inventory["repo_local_map"] == {
        "present": True,
        "count": 2,
        "entries": [
            {"repo": "acme/api", "path": "/Users/example/api"},
            {"repo": "acme/site", "path": "../marketing/site"},
        ],
    }
    assert by_key["roster-theme"]["detail"] == "Custom roster active with 2 names and 1 role label."
    assert by_key["repo-map"]["detail"] == "2 repo local path mappings configured."


def test_roster_theme_inventory_does_not_swallow_store_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import roster_theme_store

    def fail_from_state_root(_state_root: Path) -> object:
        raise RuntimeError("broken roster theme store")

    monkeypatch.setattr(
        roster_theme_store.RosterThemeStore,
        "from_state_root",
        fail_from_state_root,
    )

    with pytest.raises(RuntimeError, match="broken roster theme store"):
        setup_mod._install_roster_theme(tmp_path)


def test_roster_theme_inventory_coerces_updated_at_to_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import roster_theme_store

    class FakeState:
        def __init__(self) -> None:
            self.theme = "custom"
            self.custom_names = {"architect": "Sherlock"}
            self.custom_roles = {}
            self.updated_at = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)

    class FakeStore:
        def load(self) -> FakeState:
            return FakeState()

    monkeypatch.setattr(
        roster_theme_store.RosterThemeStore,
        "from_state_root",
        lambda _state_root: FakeStore(),
    )

    payload = setup_mod._install_roster_theme(tmp_path)

    assert payload["updated_at"] == "2026-06-30 12:00:00+00:00"


def test_bootstrap_status_uses_active_serve_home_for_code_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    home = tmp_path / "home"
    active_home = tmp_path / "active-runtime"
    launcher_home = tmp_path / "launcher-runtime"
    active_cache = active_home / "bin" / "codebase-memory-mcp"
    active_index = active_home / "state" / "code-memory"
    launcher_cache = launcher_home / "bin" / "codebase-memory-mcp"
    launcher_index = launcher_home / "state" / "code-memory"
    workspace = tmp_path / "workspace"
    active_repo = workspace / "active" / "api"
    launcher_repo = workspace / "launcher" / "api"
    (active_repo / ".git").mkdir(parents=True)
    (launcher_repo / ".git").mkdir(parents=True)
    active_graph = _scope_cache_dir(active_index / ".cache" / "codebase-memory-mcp", active_repo)
    launcher_graph = _scope_cache_dir(
        launcher_index / ".cache" / "codebase-memory-mcp", launcher_repo
    )
    home.mkdir()
    active_cache.parent.mkdir(parents=True)
    active_graph.mkdir(parents=True)
    launcher_cache.parent.mkdir(parents=True)
    launcher_graph.mkdir(parents=True)
    active_cache.write_text("#!/bin/sh\n", encoding="utf-8")
    active_cache.chmod(active_cache.stat().st_mode | stat.S_IXUSR)
    launcher_cache.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher_cache.chmod(launcher_cache.stat().st_mode | stat.S_IXUSR)
    (active_graph / "graph.db").write_text("active", encoding="utf-8")
    (launcher_graph / "graph.db").write_text("launcher", encoding="utf-8")
    (home / ".alfredrc").write_text(
        f"ALFRED_HOME={launcher_home}\nALFRED_CODE_MEMORY_REPOS=launcher/api\n",
        encoding="utf-8",
    )
    active_home.mkdir(exist_ok=True)
    (active_home / ".env").write_text(
        "ALFRED_CODE_MEMORY_REPOS=active/api\nALFRED_SHIPPED_REPOS=active/api\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(active_home))
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_QUEUE_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_SHIPPED_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_BRIDGE_REPOS", raising=False)

    status = setup_mod.bootstrap_status()
    code_memory = status["code_memory"]

    assert status["install"]["alfred_home"] == str(active_home)
    assert code_memory["binary"]["path"] == str(active_cache)
    assert code_memory["index_dir"] == str(active_index)
    assert code_memory["index_present"] is True
    assert code_memory["repos"]["configured"] == ["active/api"]
    assert "launcher/api" not in code_memory["repos"]["configured"]


def test_bootstrap_status_expands_tilde_home_for_code_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    home = tmp_path / "home"
    alfred_home = home / "runtime"
    cache_bin = alfred_home / "bin" / "codebase-memory-mcp"
    index_dir = alfred_home / "state" / "code-memory"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    graph_dir = _scope_cache_dir(index_dir / ".cache" / "codebase-memory-mcp", repo)
    cache_bin.parent.mkdir(parents=True)
    graph_dir.mkdir(parents=True)
    cache_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    (graph_dir / "graph.db").write_text("ok", encoding="utf-8")
    cache_bin.chmod(cache_bin.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", "~/runtime")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["binary"] == {
        "resolved": True,
        "path": str(cache_bin),
        "source": "cache",
        "configured": None,
    }
    assert code_memory["index_dir"] == str(index_dir)
    assert code_memory["index_present"] is True


def test_code_memory_status_ignores_rc_selected_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    cache_bin = runtime_a / "bin" / "codebase-memory-mcp"
    index_dir = runtime_a / "state" / "code-memory"
    home.mkdir()
    runtime_a.mkdir()
    runtime_b.mkdir()
    cache_bin.parent.mkdir(parents=True)
    index_dir.mkdir(parents=True)
    cache_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    (index_dir / "graph.db").write_text("ok", encoding="utf-8")
    cache_bin.chmod(cache_bin.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ALFRED_HOME", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    (home / ".alfredrc").write_text(f"ALFRED_HOME={runtime_a}\n", encoding="utf-8")
    (runtime_a / ".env").write_text(
        f"ALFRED_HOME={runtime_b}\nALFRED_CODE_MEMORY_REPOS=api\n",
        encoding="utf-8",
    )

    code_memory = setup_mod.code_memory_status()

    assert code_memory["binary"]["path"] is None
    assert code_memory["index_dir"] == str(home / ".alfred" / "state" / "code-memory")
    assert code_memory["index_present"] is False


def test_bootstrap_status_matches_case_insensitive_launcher_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.delenv("ALFRED_CODE_MEMORY_AUTOFETCH", raising=False)
    alfred_home = tmp_path / ".alfred"
    alfred_home.mkdir()
    workspace = tmp_path / "workspace"
    (workspace / "api" / ".git").mkdir(parents=True)
    (alfred_home / ".env").write_text(
        "ALFRED_CODE_MEMORY_AUTOFETCH=False\nALFRED_CODE_MEMORY_REPOS=api\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["autofetch"] is False
    assert "autofetch is disabled" in code_memory["detail"]


def test_bootstrap_status_fails_closed_after_stale_code_memory_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    cache_bin = tmp_path / ".alfred" / "bin" / "codebase-memory-mcp"
    cache_bin.parent.mkdir(parents=True)
    cache_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    cache_bin.chmod(cache_bin.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_BIN", str(tmp_path / "removed-binary"))

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["binary"] == {
        "resolved": False,
        "path": None,
        "source": "env",
        "configured": str(tmp_path / "removed-binary"),
    }


def test_code_memory_status_ignores_ambient_path_binary(tmp_path: Path) -> None:
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    ambient_bin = path_dir / "codebase-memory-mcp"
    ambient_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    ambient_bin.chmod(ambient_bin.stat().st_mode | stat.S_IXUSR)

    code_memory = setup_mod.code_memory_status(
        {
            "HOME": str(tmp_path),
            "ALFRED_HOME": str(tmp_path / ".alfred"),
            "PATH": str(path_dir),
            "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
        }
    )

    assert code_memory["binary"] == {
        "resolved": False,
        "path": None,
        "source": "none",
        "configured": None,
    }


@pytest.mark.parametrize("use_home", [True, False])
def test_launcher_status_and_battery_agree_on_explicit_tilde_binary(
    tmp_path: Path,
    use_home: bool,
) -> None:
    alfred_home = tmp_path / "runtime"
    home = tmp_path / "home"
    expansion_root = home if use_home else alfred_home
    binary = expansion_root / "bin" / "custom-memory"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nprintf 'custom-memory 1.0\\n'\n", encoding="utf-8")
    binary.chmod(0o755)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "ALFRED_HOME": str(alfred_home),
        "ALFRED_CODE_MEMORY_BIN": "~/bin/custom-memory",
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
    }
    if use_home:
        env["HOME"] = str(home)

    launcher = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.code_memory_status(env)

    assert launcher.returncode == 0, launcher.stderr
    assert f"binary:  {binary}" in launcher.stderr
    assert code_memory["binary"] == {
        "resolved": True,
        "path": str(binary),
        "source": "env",
        "configured": "~/bin/custom-memory",
    }
    assert setup_mod.batteries._code_memory_binary(env) is True


def test_launcher_status_and_battery_reject_missing_explicit_tilde_binary(
    tmp_path: Path,
) -> None:
    alfred_home = tmp_path / "runtime"
    cache = alfred_home / "bin" / "codebase-memory-mcp"
    cache.parent.mkdir(parents=True)
    cache.write_text("#!/bin/sh\nprintf 'cache 1.0\\n'\n", encoding="utf-8")
    cache.chmod(0o755)
    missing = alfred_home / "bin" / "custom-memory"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "ALFRED_HOME": str(alfred_home),
        "ALFRED_CODE_MEMORY_BIN": "~/bin/custom-memory",
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
    }

    launcher = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.code_memory_status(env)

    assert launcher.returncode == 0, launcher.stderr
    assert f"explicit binary is not executable: {missing}" in launcher.stderr
    assert "binary:  NOT RESOLVED" in launcher.stderr
    assert str(cache) not in launcher.stderr
    assert code_memory["binary"] == {
        "resolved": False,
        "path": None,
        "source": "env",
        "configured": "~/bin/custom-memory",
    }
    assert setup_mod.batteries._code_memory_binary(env) is False


def test_bootstrap_status_respects_code_memory_disable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_MCP", "0")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_AUTOFETCH", "0")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api, web")
    payload = setup_mod.bootstrap_status()
    code_memory = payload["code_memory"]
    first_run_by_key = {check["key"]: check for check in payload["first_run"]["checks"]}
    capability_by_key = {item["key"]: item for item in payload["capability_plane"]["capabilities"]}

    assert code_memory["enabled"] is False
    assert code_memory["autofetch"] is False
    assert code_memory["repos"] == {
        "configured": ["api", "web"],
        "configured_existing": [],
        "discovered": [],
        "selected": ["api", "web"],
        "source": "configured",
        "count": 2,
    }
    assert code_memory["detail"] == "Code memory is disabled with ALFRED_CODE_MEMORY_MCP."
    assert capability_by_key["code_graph"]["install_hint"] == (
        "Set ALFRED_CODE_MEMORY_MCP=1 to re-enable code graph memory."
    )
    assert first_run_by_key["code_graph"]["tier"] == "optional"
    assert first_run_by_key["code_graph"]["state"] == "disabled"
    assert first_run_by_key["code_graph"]["action"] == ""
    assert first_run_by_key["code_graph"]["detected"] == {
        "capability_state": "disabled",
        "enabled": False,
    }


def test_capability_plane_reports_missing_optional_layers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "claude"))
    monkeypatch.delenv("ALFRED_CONTEXT_COMPRESSION", raising=False)
    monkeypatch.delenv("ALFRED_CONTEXT_GOVERNOR", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_AUTOFETCH", raising=False)

    payload = setup_mod.capability_status()
    by_key = {item["key"]: item for item in payload["capabilities"]}

    assert payload["summary"] == {"ready": 1, "actionable": 2, "disabled": 0, "total": 3}
    assert by_key["code_graph"]["state"] == "needs_scope"
    assert by_key["context_compression"]["state"] == "ready"
    assert by_key["context_compression"]["enabled"] is True
    assert by_key["context_compression"]["detected"]["env_key"] == "ALFRED_CONTEXT_GOVERNOR"
    assert by_key["engineering_skills"]["state"] == "missing"


def test_capability_plane_reports_enabled_graphify_instead_of_disabled_alternative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph = tmp_path / "graph.json"
    graph.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        setup_mod.batteries,
        "manifest",
        lambda _env: {
            "batteries": [
                {
                    "id": "graphify",
                    "configured": True,
                    "enabled": True,
                    "installed": True,
                    "status": "enabled",
                    "docs": "docs/CODE_MEMORY.md",
                    "how_it_helps": "Graphify is active for local relationship queries.",
                }
            ]
        },
    )
    code_memory = {
        "enabled": False,
        "autofetch": False,
        "binary": {"resolved": True},
        "index_present": True,
        "detail": "Code memory is disabled with ALFRED_CODE_MEMORY_MCP.",
    }

    payload = setup_mod.capability_status(
        code_memory,
        launcher_env={"ALFRED_HOME": "/tmp/x", "ALFRED_GRAPHIFY_GRAPH": str(graph)},
    )
    code_graph = next(item for item in payload["capabilities"] if item["key"] == "code_graph")

    assert code_graph["state"] == "ready"
    assert code_graph["enabled"] is True
    assert code_graph["installed"] is True
    assert code_graph["detected"]["engine"] == "graphify"
    assert code_graph["source"]["source"] == "graphifyy"


def test_capability_plane_reports_enabled_missing_graphify_as_installable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_mod.batteries,
        "manifest",
        lambda _env: {
            "batteries": [
                {
                    "id": "graphify",
                    "configured": True,
                    "enabled": False,
                    "installed": False,
                    "status": "missing",
                    "install_hint": "pipx install graphifyy",
                }
            ]
        },
    )
    code_memory = {
        "enabled": False,
        "autofetch": False,
        "binary": {"resolved": True},
        "index_present": True,
        "detail": "Code memory is disabled.",
    }

    payload = setup_mod.capability_status(code_memory, launcher_env={"ALFRED_HOME": "/tmp/x"})
    code_graph = next(item for item in payload["capabilities"] if item["key"] == "code_graph")

    assert code_graph["state"] == "installable"
    assert code_graph["enabled"] is True
    assert code_graph["installed"] is False
    assert code_graph["detected"]["engine"] == "graphify"
    assert code_graph["install_hint"] == "pipx install graphifyy"
    assert code_graph["source"]["source"] == "graphifyy"

    readiness = setup_mod._code_graph_readiness_check(payload, code_memory)
    assert readiness["state"] == "actionable"
    assert readiness["detected"] == {
        "capability_state": "installable",
        "enabled": True,
        "engine": "graphify",
    }


def test_ready_code_memory_wins_while_graphify_is_not_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_mod.batteries,
        "manifest",
        lambda _env: {
            "batteries": [
                {"id": "graphify", "configured": True, "enabled": False, "installed": False}
            ]
        },
    )
    code_memory = {
        "enabled": False,
        "binary": {"resolved": True},
        "index_present": True,
        "repos": {"configured": ["api"], "selected": ["api"]},
        "detail": "Code memory is ready.",
    }
    payload = setup_mod.capability_status(
        code_memory,
        launcher_env={
            "ALFRED_HOME": "/tmp/x",
            "ALFRED_GRAPHIFY_FALLBACK": "code-memory",
        },
    )
    code_graph = next(item for item in payload["capabilities"] if item["key"] == "code_graph")
    assert code_graph["state"] == "ready"
    assert code_graph["source"]["source"] == "DeusData/codebase-memory-mcp"
    assert code_graph["detail"] == (
        "Code-memory fallback is ready while Graphify is not installed."
    )


def test_graphify_fallback_inspects_explicit_code_memory_scope_while_mcp_stays_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    repo.joinpath(".git").mkdir(parents=True)
    binary = runtime / "bin" / "codebase-memory-mcp"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    cache_root = tmp_path / "cache"
    graph_dir = _scope_cache_dir(cache_root, repo)
    graph_dir.mkdir(parents=True)
    graph_dir.joinpath("graph.db").write_text("ready", encoding="utf-8")
    env = {
        "HOME": str(tmp_path / "home"),
        "ALFRED_HOME": str(runtime),
        "ALFRED_CODE_MEMORY_MCP": "0",
        "ALFRED_GRAPHIFY_MCP": "1",
        "ALFRED_GRAPHIFY_FALLBACK": "code-memory",
        "ALFRED_CODE_MEMORY_REPOS": "api",
        "CBM_CACHE_DIR": str(cache_root),
        "WORKSPACE_ROOT": str(workspace),
        "WORKSPACE_SUBDIR": "",
    }
    monkeypatch.setattr(
        setup_mod.batteries,
        "manifest",
        lambda _env: {
            "batteries": [
                {
                    "id": "graphify",
                    "configured": True,
                    "enabled": False,
                    "installed": False,
                }
            ]
        },
    )

    code_memory = setup_mod.code_memory_status(env)
    capability_plane = setup_mod.capability_status(code_memory, launcher_env=env)
    code_graph = next(
        item for item in capability_plane["capabilities"] if item["key"] == "code_graph"
    )

    assert code_memory["enabled"] is False
    assert code_memory["repos"]["selected"] == ["api"]
    assert code_memory["graph_dir"] == str(graph_dir)
    assert code_memory["index_present"] is True
    assert code_graph["state"] == "ready"
    assert code_graph["source"]["source"] == "DeusData/codebase-memory-mcp"


def test_relative_graph_is_not_probed_against_setup_server_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph = tmp_path / "graphify-out" / "graph.json"
    graph.parent.mkdir()
    graph.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        setup_mod.batteries,
        "manifest",
        lambda _env: {
            "batteries": [
                {"id": "graphify", "configured": True, "enabled": False, "installed": True}
            ]
        },
    )
    payload = setup_mod.capability_status(
        {"enabled": False, "binary": {"resolved": False}, "index_present": False},
        launcher_env={"ALFRED_GRAPHIFY_GRAPH": "graphify-out/graph.json"},
    )
    code_graph = next(item for item in payload["capabilities"] if item["key"] == "code_graph")
    assert code_graph["state"] == "needs_index"
    assert code_graph["detected"]["graph_present"] is False


def test_capability_plane_reports_builtin_context_governor_with_headroom_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / "codex"
    (codex_home / "skills" / "gstack").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "claude"))
    monkeypatch.setenv("ALFRED_CONTEXT_COMPRESSION", "1")

    def fake_which(name: str, **_kwargs: object) -> str | None:
        return "/opt/homebrew/bin/headroom" if name == "headroom" else None

    monkeypatch.setattr(setup_mod.shutil, "which", fake_which)
    code_memory = {
        "enabled": True,
        "autofetch": True,
        "binary": {
            "resolved": True,
            "path": "/usr/local/bin/codebase-memory-mcp",
            "source": "path",
            "configured": None,
        },
        "version_pin": "v0.8.1",
        "repo": "DeusData/codebase-memory-mcp",
        "index_dir": str(tmp_path / "index"),
        "index_present": True,
        "repos": {"configured": ["api"], "selected": ["api"], "count": 1},
        "detail": "Code-memory binary and index are present.",
    }

    payload = setup_mod.capability_status(
        code_memory,
        launcher_env={
            "ALFRED_GRAPHIFY_MCP": "0",
            "ALFRED_CONTEXT_COMPRESSION": "1",
            "CODEX_HOME": str(codex_home),
            "CLAUDE_HOME": str(tmp_path / "claude"),
        },
    )
    by_key = {item["key"]: item for item in payload["capabilities"]}

    assert payload["summary"]["ready"] == 3
    assert payload["summary"]["actionable"] == 0
    assert by_key["code_graph"]["state"] == "ready"
    assert by_key["context_compression"]["state"] == "ready"
    assert by_key["context_compression"]["enabled"] is True
    assert by_key["context_compression"]["detected"] == {
        "built_in": True,
        "env_key": "ALFRED_CONTEXT_GOVERNOR",
        "headroom_binary": "/opt/homebrew/bin/headroom",
        "headroom_enabled": True,
        "headroom_env_key": "ALFRED_CONTEXT_COMPRESSION",
    }
    assert by_key["engineering_skills"]["state"] == "ready"
    assert by_key["engineering_skills"]["detected"]["paths"] == [
        str(codex_home / "skills" / "gstack")
    ]


def test_capability_plane_detects_first_party_starter_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    claude_home = tmp_path / "claude"
    (claude_home / "skills" / "spec-to-issues").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)

    payload = setup_mod.capability_status()
    skills = {item["key"]: item for item in payload["capabilities"]}["engineering_skills"]

    assert skills["state"] == "ready"
    assert skills["detected"]["paths"] == [str(claude_home / "skills" / "spec-to-issues")]


def test_capability_plane_uses_configured_skills_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skills_dir = tmp_path / "custom-skills"
    (skills_dir / "write-tests").mkdir(parents=True)
    monkeypatch.setenv("ALFRED_SKILLS_DIR", str(skills_dir))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "claude"))
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)

    payload = setup_mod.capability_status()
    skills = {item["key"]: item for item in payload["capabilities"]}["engineering_skills"]

    assert skills["state"] == "ready"
    assert skills["detected"]["paths"] == [str(skills_dir / "write-tests")]


def test_capability_plane_keeps_context_governor_ready_without_headroom_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / ".alfred"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "claude"))
    monkeypatch.delenv("ALFRED_CONTEXT_COMPRESSION", raising=False)
    monkeypatch.setattr(
        setup_mod.shutil,
        "which",
        lambda name, **_kwargs: "/opt/homebrew/bin/headroom" if name == "headroom" else None,
    )

    payload = setup_mod.capability_status()
    context = {item["key"]: item for item in payload["capabilities"]}["context_compression"]

    assert context["state"] == "ready"
    assert context["installed"] is True
    assert context["enabled"] is True
    assert context["detected"]["headroom_binary"] == "/opt/homebrew/bin/headroom"
    assert context["detected"]["headroom_enabled"] is False


def test_capability_plane_reads_headroom_opt_in_from_runtime_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text("ALFRED_CONTEXT_COMPRESSION=0\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("ALFRED_CONTEXT_COMPRESSION", raising=False)
    monkeypatch.setattr(
        setup_mod.shutil,
        "which",
        lambda name, **_kwargs: "/opt/homebrew/bin/headroom" if name == "headroom" else None,
    )

    payload = setup_mod.capability_status()
    context = {item["key"]: item for item in payload["capabilities"]}["context_compression"]

    assert context["state"] == "ready"
    assert context["installed"] is True
    assert context["enabled"] is True
    assert context["detected"]["headroom_enabled"] is False


def test_capability_plane_reports_disabled_context_governor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text("ALFRED_CONTEXT_GOVERNOR=0\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("ALFRED_CONTEXT_GOVERNOR", raising=False)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)

    payload = setup_mod.capability_status()
    context = {item["key"]: item for item in payload["capabilities"]}["context_compression"]

    assert context["state"] == "disabled"
    assert context["installed"] is False
    assert context["enabled"] is False
    assert "ALFRED_CONTEXT_GOVERNOR" in context["detail"]


def test_capability_plane_treats_empty_context_governor_env_as_default_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text("ALFRED_CONTEXT_GOVERNOR=\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("ALFRED_CONTEXT_GOVERNOR", raising=False)
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)

    payload = setup_mod.capability_status()
    context = {item["key"]: item for item in payload["capabilities"]}["context_compression"]

    assert context["state"] == "ready"
    assert context["installed"] is True
    assert context["enabled"] is True


def test_capability_plane_ignores_legacy_alfredrc_for_non_code_memory_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    stale_codex = tmp_path / "stale-codex"
    home.mkdir()
    runtime.mkdir()
    (stale_codex / "skills" / "gstack").mkdir(parents=True)
    (home / ".alfredrc").write_text(
        f"ALFRED_CONTEXT_COMPRESSION=1\nCODEX_HOME={stale_codex}\n",
        encoding="utf-8",
    )
    (runtime / ".env").write_text("ALFRED_CONTEXT_COMPRESSION=0\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.delenv("ALFRED_CONTEXT_COMPRESSION", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_HOME", raising=False)
    monkeypatch.setattr(
        setup_mod.shutil,
        "which",
        lambda name, **_kwargs: "/opt/homebrew/bin/headroom" if name == "headroom" else None,
    )
    code_memory = {
        "enabled": True,
        "autofetch": True,
        "binary": {"resolved": False},
        "index_present": False,
        "repos": {"configured": [], "count": 0},
        "detail": "Code-memory binary is not installed yet.",
    }

    payload = setup_mod.capability_status(code_memory)
    by_key = {item["key"]: item for item in payload["capabilities"]}

    assert by_key["context_compression"]["state"] == "ready"
    assert by_key["context_compression"]["enabled"] is True
    assert by_key["context_compression"]["detected"]["headroom_enabled"] is False
    assert by_key["engineering_skills"]["state"] == "missing"
    assert by_key["engineering_skills"]["detected"]["paths"] == []


def test_capability_plane_uses_explicit_skill_homes_without_resolving_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    (codex_home / "skills" / "gstack").mkdir(parents=True)
    (claude_home / "skills").mkdir(parents=True)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))
    monkeypatch.setattr(
        setup_mod.Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))),
    )
    monkeypatch.setattr(
        setup_mod.os.path,
        "expanduser",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no home")),
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)

    payload = setup_mod.capability_status()
    skills = {item["key"]: item for item in payload["capabilities"]}["engineering_skills"]

    assert skills["state"] == "ready"
    assert skills["detected"]["paths"] == [str(codex_home / "skills" / "gstack")]


def test_setup_module_cold_import_survives_without_agent_runner_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = os.environ.copy()
    env.pop("HOME", None)
    env["PYTHONPATH"] = str(ROOT / "lib")
    code = """
import builtins
import pathlib

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "agent_runner.paths":
        raise RuntimeError("agent_runner.paths import should not be needed")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
pathlib.Path.home = staticmethod(
    lambda: (_ for _ in ()).throw(RuntimeError("no home"))
)

from server.setup import capability_status

print(capability_status()["summary"]["total"])
"""

    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "3"


def test_bootstrap_status_demo_fallback_survives_unresolvable_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    codex_home.mkdir()
    claude_home.mkdir()
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("ALFRED_HOME", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MAP_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_BRIDGE_REPOS", raising=False)
    monkeypatch.setenv("ALFRED_QUEUE_REPOS", "octocat/web")
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "octocat/web")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))
    monkeypatch.setattr(
        setup_mod,
        "gh_auth_status",
        lambda **_kwargs: {"ok": True, "account": "octocat", "detail": "Signed in."},
    )
    monkeypatch.setattr(
        setup_mod,
        "engine_clis",
        lambda **_kwargs: [
            {
                "name": "codex",
                "display_name": "Codex",
                "installed": True,
                "ready": True,
                "path": "/usr/local/bin/codex",
            }
        ],
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        setup_mod.os.path,
        "expanduser",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no home")),
    )
    monkeypatch.setattr(
        setup_mod.Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))),
    )

    payload = setup_mod.bootstrap_status()

    assert payload["demo"] == {"present": False}
    assert payload["capability_plane"]["summary"]["total"] == 3
    assert payload["ready"] is True


def test_bootstrap_status_survives_tilde_code_memory_paths_without_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _stub_common(monkeypatch)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.setenv("WORKSPACE_ROOT", "~/workspace")
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_HOME", "~/code-memory-home")
    monkeypatch.setenv("CBM_CACHE_DIR", "~/code-memory-cache")
    monkeypatch.setattr(
        setup_mod.Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))),
    )
    monkeypatch.setattr(
        setup_mod.os.path,
        "expanduser",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no home")),
    )
    monkeypatch.setattr(setup_mod.shutil, "which", lambda *_args, **_kwargs: None)

    payload = setup_mod.bootstrap_status()

    code_memory = payload["code_memory"]
    assert code_memory["index_home"] == str(runtime / "code-memory-home")
    assert code_memory["graph_dir"] == str(runtime / "code-memory-cache" / "scopes" / "unavailable")
    assert code_memory["repos"]["configured"] == ["api"]
    assert code_memory["repos"]["source"] == "configured-missing"
    assert payload["capability_plane"]["summary"]["total"] == 3


def test_bootstrap_status_avoids_home_dependent_runtime_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import builtins

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    gh_bin = tmp_path / "gh"
    gh_bin.write_text(
        '#!/bin/sh\nprintf "Logged in to github.com as octocat\\n" >&2\n',
        encoding="utf-8",
    )
    gh_bin.chmod(0o755)
    codex_bin = tmp_path / "codex"
    codex_bin.write_text(
        """#!/bin/sh
case "$*" in
  --version) printf 'codex-cli 1.2.3\n' ;;
  'exec --help') printf '%s\n' '--output-last-message --sandbox --cd --skip-git-repo-check --ignore-user-config --ephemeral -c --model --add-dir --dangerously-bypass-approvals-and-sandbox' ;;
  'login status') exit 0 ;;
  *) exit 1 ;;
esac
""",
        encoding="utf-8",
    )
    codex_bin.chmod(0o755)

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("ALFRED_HOME", str(runtime))
    monkeypatch.setenv("ALFRED_QUEUE_REPOS", "acme/frontend, acme/api")
    monkeypatch.setenv("GH_BIN", str(gh_bin))
    monkeypatch.setenv("CODEX_BIN", str(codex_bin))
    monkeypatch.delenv("ALFRED_CODE_MEMORY_BIN", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    monkeypatch.setenv("ALFRED_SHIPPED_REPOS", "acme/frontend")
    monkeypatch.setenv("ALFRED_BRIDGE_REPOS", "acme/api")
    monkeypatch.setattr(
        setup_mod.Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))),
    )

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        blocked = {"agent_runner.paths", "issue_queue", "shipped_board"}
        if name in blocked:
            raise RuntimeError(f"{name} import should not be needed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    payload = setup_mod.bootstrap_status()

    assert payload["github"]["ok"] is True
    assert payload["repos"]["selected"] == ["acme/api", "acme/frontend"]
    assert payload["ready"] is True


def test_bootstrap_status_does_not_discover_code_memory_repos_without_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "product" / "api" / ".git").mkdir(parents=True)
    (workspace / "product" / "api" / "packages" / "nested" / ".git").mkdir(parents=True)
    (workspace / "tools" / "alfred-os" / ".git").mkdir(parents=True)
    (workspace / "worktree").mkdir()
    (workspace / "worktree" / ".git").write_text("gitdir: ../.git/worktrees/worktree\n")
    (workspace / ".archive" / "old" / ".git").mkdir(parents=True)
    (workspace / "tools" / ".worktrees" / "pr-1" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_REPOS", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MAP_REPOS", raising=False)

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"] == {
        "configured": [],
        "configured_existing": [],
        "discovered": [],
        "selected": [],
        "source": "unconfigured",
        "count": 0,
    }


def test_bootstrap_status_prefers_existing_configured_code_memory_repos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "api" / ".git").mkdir(parents=True)
    (workspace / "web" / ".git").mkdir(parents=True)
    (workspace / "ignored" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "web, missing, my repo, api")
    (workspace / "myrepo" / ".git").mkdir(parents=True)

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"] == {
        "configured": ["web", "missing", "myrepo", "api"],
        "configured_existing": ["web", "myrepo", "api"],
        "discovered": [],
        "selected": ["web", "myrepo", "api"],
        "source": "configured",
        "count": 3,
    }


def test_bootstrap_status_uses_repo_local_map_for_configured_code_memory_repos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    marketing = tmp_path / "marketing"
    (workspace / "product" / "backend" / ".git").mkdir(parents=True)
    (marketing / "site" / ".git").mkdir(parents=True)
    (workspace / "product" / "ignored" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        f"acme-backend=backend acme-site={marketing / 'site'}",
    )
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "acme-site,acme-backend")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"] == {
        "configured": ["acme-site", "acme-backend"],
        "configured_existing": ["acme-site", "acme-backend"],
        "discovered": [],
        "selected": ["acme-site", "acme-backend"],
        "source": "configured",
        "count": 2,
    }


def test_setup_expands_tilde_workspace_and_repo_map_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    home = tmp_path / "home"
    workspace_repo = home / "workspace" / "api"
    mapped_repo = home / "repos" / "web"
    workspace_repo.joinpath(".git").mkdir(parents=True)
    mapped_repo.joinpath(".git").mkdir(parents=True)
    cache_root = tmp_path / "cache-root"
    monkeypatch.setenv("WORKSPACE_ROOT", "~/workspace")
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", "web=~/repos/web")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "api,web")
    monkeypatch.setenv("CBM_CACHE_DIR", str(cache_root))

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"]["configured_existing"] == ["api", "web"]
    assert code_memory["graph_dir"] == str(
        _scope_cache_dir(cache_root, workspace_repo, mapped_repo)
    )


def test_launcher_and_setup_use_runtime_home_for_mapped_tilde_without_home(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    repo = runtime / "repos" / "api"
    repo.joinpath(".git").mkdir(parents=True)
    cache_root = tmp_path / "cache-root"
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$CBM_CACHE_DIR"\n', encoding="utf-8")
    binary.chmod(0o755)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "ALFRED_HOME": str(runtime),
        "ALFRED_CODE_MEMORY_BIN": str(binary),
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
        "ALFRED_CODE_MEMORY_REPOS": "api",
        "ALFRED_CODE_MAP_REPOS": "",
        "ALFRED_REPO_LOCAL_MAP": "api=~/repos/api",
        "CBM_CACHE_DIR": str(cache_root),
        "WORKSPACE_ROOT": str(tmp_path / "unused-workspace"),
        "WORKSPACE_SUBDIR": "",
    }

    launcher = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "index"],
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.code_memory_status(env)
    expected = _scope_cache_dir(cache_root, repo)

    assert launcher.returncode == 0, launcher.stderr
    assert launcher.stdout.strip() == str(expected)
    assert code_memory["repos"]["configured_existing"] == ["api"]
    assert code_memory["graph_dir"] == str(expected)


def test_launcher_and_setup_use_runtime_home_for_tilde_workspace_without_home(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    repo = runtime / "workspace" / "api"
    repo.joinpath(".git").mkdir(parents=True)
    cache_root = tmp_path / "cache-root"
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$CBM_CACHE_DIR"\n', encoding="utf-8")
    binary.chmod(0o755)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "ALFRED_HOME": str(runtime),
        "ALFRED_CODE_MEMORY_BIN": str(binary),
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
        "ALFRED_CODE_MEMORY_REPOS": "api",
        "ALFRED_CODE_MAP_REPOS": "",
        "CBM_CACHE_DIR": str(cache_root),
        "WORKSPACE_ROOT": "~/workspace",
        "WORKSPACE_SUBDIR": "",
    }

    launcher = subprocess.run(
        ["bash", str(ROOT / "bin" / "code-memory-mcp"), "index"],
        capture_output=True,
        text=True,
        env=env,
    )
    code_memory = setup_mod.code_memory_status(env)
    expected = _scope_cache_dir(cache_root, repo)

    assert launcher.returncode == 0, launcher.stderr
    assert launcher.stdout.strip() == str(expected)
    assert code_memory["repos"]["configured_existing"] == ["api"]
    assert code_memory["graph_dir"] == str(expected)


@pytest.mark.parametrize(
    ("env", "expected_base"),
    [
        ({"HOME": "/configured/home", "ALFRED_HOME": "/runtime"}, Path("/configured/home")),
        ({"ALFRED_HOME": "/runtime"}, Path("/runtime")),
        ({}, Path.home()),
    ],
)
def test_mapped_tilde_home_precedence(
    env: dict[str, str],
    expected_base: Path,
) -> None:
    path = setup_mod._code_memory_configured_repo_path(
        env,
        "api",
        {"api": "~/repos/api"},
    )

    assert path == expected_base / "repos" / "api"


def test_mapped_tilde_does_not_use_platform_fallback_when_home_is_configured() -> None:
    path = setup_mod._code_memory_configured_repo_path(
        {
            "HOME": "~",
            "ALFRED_HOME": "/runtime",
            "WORKSPACE_ROOT": "/workspace",
            "WORKSPACE_SUBDIR": "",
        },
        "api",
        {"api": "~/repos/api"},
    )

    assert path == Path("/workspace/~/repos/api")


def test_bootstrap_status_uses_full_slug_repo_local_map_for_bare_code_memory_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    mapped = tmp_path / "mapped-backend"
    mapped.joinpath(".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", f"acme/backend={mapped}")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "backend")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"] == {
        "configured": ["backend"],
        "configured_existing": ["backend"],
        "discovered": [],
        "selected": ["backend"],
        "source": "configured",
        "count": 1,
    }


def test_bootstrap_status_parses_repo_local_map_paths_with_commas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    repo = tmp_path / "marketing,site" / "repo"
    repo.joinpath(".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", f"acme-site={repo}")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "acme-site")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"]["configured_existing"] == ["acme-site"]


def test_bootstrap_status_does_not_auto_discover_when_configured_code_memory_repos_are_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "product" / "api" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "old-alfred")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"] == {
        "configured": ["old-alfred"],
        "configured_existing": [],
        "discovered": [],
        "selected": [],
        "source": "configured-missing",
        "count": 0,
    }


def test_bootstrap_status_does_not_auto_discover_when_configured_code_memory_dirs_are_not_git_repos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_common(monkeypatch)
    _isolate_launcher_env(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "api" / ".git").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKSPACE_SUBDIR", "")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_REPOS", "docs")

    code_memory = setup_mod.bootstrap_status()["code_memory"]

    assert code_memory["repos"] == {
        "configured": ["docs"],
        "configured_existing": [],
        "discovered": [],
        "selected": [],
        "source": "configured-missing",
        "count": 0,
    }
