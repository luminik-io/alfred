#!/usr/bin/env python3
"""``bin/code-memory-mcp`` sha256-verifies the auto-fetched release before it is
ever extracted or executed.

The launcher fetches a prebuilt codebase-memory-mcp tarball from a GitHub
release and runs the binary inside it. To close the supply-chain gap (a
compromised upstream account or an overridden repo/version env could otherwise
install a malicious binary), the download is checked against a sha256 pinned
from upstream's published checksums.txt before extraction. These tests drive
the verify path directly through the script's internal ``__verify-checksum``
hook, with no network access: a matching digest passes, and every failure mode
(wrong digest, no pin, missing file) fails closed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "code-memory-mcp"
ENV_EXAMPLE = ROOT / ".env.example"

# Pinned digest the script ships for darwin-arm64, copied from upstream
# checksums.txt for the pinned version. The test recreates a file with exactly
# this digest so the match path is exercised without any download.
DARWIN_ARM64_SHA = "fbd047509852021b5446a11141bcb0a3d1dcaebf6e5112460960f29f052c1c58"


def _verify(file_path: Path, expected: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "__verify-checksum", str(file_path), expected],
        capture_output=True,
        text=True,
    )


def _asset(tmp_path: Path) -> Path:
    """A stand-in release tarball with known bytes (so we know its digest)."""
    blob = tmp_path / "asset.tar.gz"
    blob.write_bytes(b"alfred-code-memory-pinned-asset")
    return blob


def _launcher_env(tmp_path: Path, **updates: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    env.update(updates)
    return env


def test_env_example_code_memory_pin_matches_launcher_default() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    launcher_match = re.search(
        r'CODE_MEMORY_VERSION="\$\{ALFRED_CODE_MEMORY_VERSION:-(v[^}]+)\}"',
        script,
    )
    example_match = re.search(r"^# ALFRED_CODE_MEMORY_VERSION=(v\S+)$", env_example, re.M)

    assert launcher_match is not None
    assert example_match is not None
    assert example_match.group(1) == launcher_match.group(1)


def test_verify_passes_on_matching_digest(tmp_path: Path) -> None:
    blob = _asset(tmp_path)
    actual = hashlib.sha256(blob.read_bytes()).hexdigest()
    res = _verify(blob, actual)
    assert res.returncode == 0, res.stderr


def test_verify_is_case_insensitive(tmp_path: Path) -> None:
    blob = _asset(tmp_path)
    actual = hashlib.sha256(blob.read_bytes()).hexdigest().upper()
    res = _verify(blob, actual)
    assert res.returncode == 0, res.stderr


def test_verify_fails_closed_on_mismatch(tmp_path: Path) -> None:
    blob = _asset(tmp_path)
    res = _verify(blob, "deadbeef" * 8)
    assert res.returncode != 0
    assert "MISMATCH" in res.stderr


def test_verify_fails_closed_on_empty_expected(tmp_path: Path) -> None:
    blob = _asset(tmp_path)
    res = _verify(blob, "")
    assert res.returncode != 0
    assert "refusing unverified binary" in res.stderr


def test_verify_fails_closed_on_missing_file(tmp_path: Path) -> None:
    res = _verify(tmp_path / "does-not-exist.tar.gz", DARWIN_ARM64_SHA)
    assert res.returncode != 0
    assert "missing" in res.stderr


def test_pinned_tag_resolves_to_published_digest(tmp_path: Path) -> None:
    """Passing a bare platform tag resolves to the pinned digest. A file that
    does NOT have that digest must fail closed, proving the pin is wired in
    (not silently treated as 'no pin = skip')."""
    blob = _asset(tmp_path)
    res = _verify(blob, "darwin-arm64")
    assert res.returncode != 0
    assert "MISMATCH" in res.stderr


def test_pinned_digest_overridable_via_env(tmp_path: Path) -> None:
    blob = _asset(tmp_path)
    actual = hashlib.sha256(blob.read_bytes()).hexdigest()
    env = dict(os.environ, ALFRED_CODE_MEMORY_SHA256_DARWIN_ARM64=actual)
    res = subprocess.run(
        ["bash", str(SCRIPT), "__verify-checksum", str(blob), "darwin-arm64"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, res.stderr


def test_fetch_path_has_bounded_curl_timeouts() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "--connect-timeout" in script
    assert "CODE_MEMORY_CONNECT_TIMEOUT_S" in script
    assert "--max-time" in script
    assert "CODE_MEMORY_FETCH_TIMEOUT_S" in script


def test_fetch_timeout_knobs_are_derived_after_env_files_load() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    load_pos = script.index('load_env_file "$ALFRED_HOME/.env"')
    fetch_timeout_pos = script.index("CODE_MEMORY_FETCH_TIMEOUT_S=")
    connect_timeout_pos = script.index("CODE_MEMORY_CONNECT_TIMEOUT_S=")
    assert load_pos < fetch_timeout_pos
    assert load_pos < connect_timeout_pos


def test_scope_repos_auto_discovers_git_repos_when_unconfigured(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "product" / "api" / ".git").mkdir(parents=True)
    (workspace / "product" / "api" / "packages" / "nested" / ".git").mkdir(parents=True)
    (workspace / "tools" / "alfred-os" / ".git").mkdir(parents=True)
    (workspace / "worktree").mkdir()
    (workspace / "worktree" / ".git").write_text("gitdir: ../.git/worktrees/worktree\n")
    (workspace / ".archive" / "old" / ".git").mkdir(parents=True)
    (workspace / "tools" / ".worktrees" / "pr-1" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_REPOS="",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line).relative_to(workspace).as_posix() for line in res.stdout.splitlines()]
    assert repos == ["worktree", "product/api", "tools/alfred-os"]


def test_scope_repos_defaults_to_product_subdir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "product" / "api" / ".git").mkdir(parents=True)
    (workspace / "tools" / "alfred-os" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_CODE_MEMORY_REPOS="",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [
        Path(line).relative_to(workspace / "product").as_posix() for line in res.stdout.splitlines()
    ]
    assert repos == ["api"]


def test_scope_repos_follows_symlinked_workspace_root(tmp_path: Path) -> None:
    actual = tmp_path / "actual-workspace"
    workspace = tmp_path / "workspace-link"
    (actual / "api" / ".git").mkdir(parents=True)
    workspace.symlink_to(actual, target_is_directory=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_REPOS="",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line).relative_to(workspace).as_posix() for line in res.stdout.splitlines()]
    assert repos == ["api"]


def test_scope_repos_follows_symlinked_repo_dirs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    actual = tmp_path / "actual"
    (workspace / "real" / ".git").mkdir(parents=True)
    (actual / "api" / ".git").mkdir(parents=True)
    (workspace / "api").symlink_to(actual / "api", target_is_directory=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_REPOS="",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line).relative_to(workspace).as_posix() for line in res.stdout.splitlines()]
    assert repos == ["api", "real"]


def test_scope_repos_prefers_configured_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "api" / ".git").mkdir(parents=True)
    (workspace / "web" / ".git").mkdir(parents=True)
    (workspace / "ignored" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_REPOS="web, missing, api",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line).relative_to(workspace).as_posix() for line in res.stdout.splitlines()]
    assert repos == ["web", "api"]


def test_scope_repos_uses_repo_local_map_for_configured_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    marketing = tmp_path / "marketing"
    (workspace / "product" / "backend" / ".git").mkdir(parents=True)
    (marketing / "site" / ".git").mkdir(parents=True)
    (workspace / "product" / "ignored" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-backend=backend acme-site=../../{marketing.name}/site",
        ALFRED_CODE_MEMORY_REPOS="acme-site,acme-backend",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [marketing / "site", workspace / "product" / "backend"]


def test_scope_repos_uses_shell_tokenized_repo_local_map_for_configured_scope(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    marketing = tmp_path / "marketing,archive"
    (workspace / "product" / "backend" / ".git").mkdir(parents=True)
    (marketing / "site" / ".git").mkdir(parents=True)
    repo_map = shlex.join(
        [
            "acme-backend=backend",
            f"acme-site=../../{marketing.name}/site",
        ]
    )
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=repo_map,
        ALFRED_CODE_MEMORY_REPOS="acme-site,acme-backend",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [marketing / "site", workspace / "product" / "backend"]


def test_scope_repos_keeps_single_repo_local_map_path_with_comma(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    marketing = tmp_path / "marketing,archive"
    (marketing / "site" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-site={marketing / 'site'}",
        ALFRED_CODE_MEMORY_REPOS="acme-site",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [marketing / "site"]


def test_scope_repos_keeps_single_repo_local_map_path_with_trailing_comma(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    checkout = tmp_path / "archive,"
    (checkout / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={checkout}",
        ALFRED_CODE_MEMORY_REPOS="acme-api",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [checkout]


def test_scope_repos_keeps_multi_entry_repo_local_map_path_with_trailing_comma(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    api = tmp_path / "api"
    web = tmp_path / "archive,"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={api} acme-web={web}",
        ALFRED_CODE_MEMORY_REPOS="acme-api,acme-web",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [api, web]


def test_scope_repos_reads_space_separated_repo_local_map(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    api = tmp_path / "api"
    web = tmp_path / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={api} acme-web={web}",
        ALFRED_CODE_MEMORY_REPOS="acme-api,acme-web",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [api, web]


def test_scope_repos_reads_comma_delimited_repo_local_map(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    api = tmp_path / "api"
    web = tmp_path / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={api},acme-web={web}",
        ALFRED_CODE_MEMORY_REPOS="acme-api,acme-web",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [api, web]


def test_scope_repos_preserves_comma_and_equals_repo_local_map_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    api = tmp_path / "archive,build=2" / "api"
    (api / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={api}",
        ALFRED_CODE_MEMORY_REPOS="acme-api",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [api]


def test_scope_repos_decodes_canonical_repo_local_map_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    api = tmp_path / "archive,"
    web = tmp_path / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    encoded_api = "url:" + urllib.parse.quote(str(api), safe="/._-~")
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={encoded_api} acme-web={web}",
        ALFRED_CODE_MEMORY_REPOS="acme-api,acme-web",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [api, web]


def test_scope_repos_uses_case_insensitive_repo_local_map_alias(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    app = tmp_path / "MyApp"
    (app / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"Acme/MyApp={app}",
        ALFRED_CODE_MEMORY_REPOS="myapp",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [app]


def test_scope_repos_keeps_repo_local_map_path_with_spaces(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    api = tmp_path / "My Repos" / "api"
    web = tmp_path / "web"
    (api / ".git").mkdir(parents=True)
    (web / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme-api={api} acme-web={web}",
        ALFRED_CODE_MEMORY_REPOS="acme-api,acme-web",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [api, web]


def test_scope_repos_uses_full_slug_repo_local_map_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    backend = tmp_path / "backend-checkout"
    (backend / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=f"acme/backend={backend}",
        ALFRED_CODE_MEMORY_REPOS="backend",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [backend]


def test_scope_repos_prefers_explicit_bare_map_over_full_slug_alias(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    alias_checkout = tmp_path / "alias-backend"
    explicit_checkout = tmp_path / "explicit-backend"
    (alias_checkout / ".git").mkdir(parents=True)
    (explicit_checkout / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        ALFRED_REPO_LOCAL_MAP=(f"acme/backend={alias_checkout} backend={explicit_checkout}"),
        ALFRED_CODE_MEMORY_REPOS="backend",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line) for line in res.stdout.splitlines()]
    assert repos == [explicit_checkout]


def test_scope_repos_does_not_auto_discover_when_configured_dirs_are_stale(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "api" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_REPOS="docs",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert res.stdout == ""


def test_doctor_reports_stale_configured_scope_without_auto_discovery(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "api" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_AUTOFETCH="0",
        ALFRED_CODE_MEMORY_REPOS="docs",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert "repos:       configured scope not found: docs" in res.stderr
    assert "auto-discovered" not in res.stderr


def test_scope_repos_discovers_top_level_repos_before_nested_repos(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "alpha" / "extra" / ".git").mkdir(parents=True)
    (workspace / "beta" / ".git").mkdir(parents=True)
    (workspace / "gamma" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
        ALFRED_CODE_MEMORY_REPOS="",
        ALFRED_CODE_MAP_REPOS="",
        ALFRED_CODE_MEMORY_DISCOVERY_LIMIT="2",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [Path(line).relative_to(workspace).as_posix() for line in res.stdout.splitlines()]
    assert repos == ["beta", "gamma"]


def test_scope_repos_uses_workspace_subdir_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "product" / "api" / ".git").mkdir(parents=True)
    (workspace / "tools" / "alfred-os" / ".git").mkdir(parents=True)
    env = _launcher_env(
        tmp_path,
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="product",
        ALFRED_CODE_MEMORY_REPOS="",
        ALFRED_CODE_MAP_REPOS="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "__scope-repos"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    repos = [
        Path(line).relative_to(workspace / "product").as_posix() for line in res.stdout.splitlines()
    ]
    assert repos == ["api"]


def test_index_invokes_upstream_cli_index_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    (repo / ".git").mkdir(parents=True)
    code_home = tmp_path / "code-memory-home"
    cbm_cache = tmp_path / "upstream-cache"
    log = tmp_path / "upstream.log"
    fake_bin = tmp_path / "codebase-memory-mcp"
    fake_bin.write_text(
        "#!/bin/sh\n"
        'printf "HOME=%s\\n" "$HOME" >> "$CODE_MEMORY_TEST_LOG"\n'
        'printf "CBM_CACHE_DIR=%s\\n" "${CBM_CACHE_DIR:-}" >> "$CODE_MEMORY_TEST_LOG"\n'
        'printf "ARG1=%s\\nARG2=%s\\nARG3=%s\\n" "$1" "$2" "$3" >> "$CODE_MEMORY_TEST_LOG"\n',
        encoding="utf-8",
    )
    fake_bin.chmod(0o755)
    env = _launcher_env(
        tmp_path,
        ALFRED_CODE_MEMORY_BIN=str(fake_bin),
        ALFRED_CODE_MEMORY_AUTOFETCH="0",
        ALFRED_CODE_MEMORY_HOME=str(code_home),
        ALFRED_CODE_MEMORY_REPOS="api",
        ALFRED_CODE_MAP_REPOS="",
        CBM_CACHE_DIR=str(cbm_cache),
        CODE_MEMORY_TEST_LOG=str(log),
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "index"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    text = log.read_text(encoding="utf-8")
    assert f"HOME={code_home}" in text
    assert f"CBM_CACHE_DIR={cbm_cache}" in text
    assert "ARG1=cli" in text
    assert "ARG2=index_repository" in text
    assert f'"repo_path":"{repo}"' in text


def test_index_fails_when_no_repository_is_in_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_bin = tmp_path / "codebase-memory-mcp"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    env = _launcher_env(
        tmp_path,
        ALFRED_CODE_MEMORY_BIN=str(fake_bin),
        ALFRED_CODE_MEMORY_AUTOFETCH="0",
        ALFRED_CODE_MEMORY_REPOS="missing",
        ALFRED_CODE_MAP_REPOS="",
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "index"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode != 0
    assert "no in-scope repos found" in res.stderr


def test_index_fails_when_upstream_indexing_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "api" / ".git").mkdir(parents=True)
    fake_bin = tmp_path / "codebase-memory-mcp"
    fake_bin.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    env = _launcher_env(
        tmp_path,
        ALFRED_CODE_MEMORY_BIN=str(fake_bin),
        ALFRED_CODE_MEMORY_AUTOFETCH="0",
        ALFRED_CODE_MEMORY_REPOS="api",
        ALFRED_CODE_MAP_REPOS="",
        WORKSPACE_ROOT=str(workspace),
        WORKSPACE_SUBDIR="",
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "index"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode != 0
    assert "auto-index failed" in res.stderr


def test_serve_runs_upstream_stdio_server_with_code_memory_home(tmp_path: Path) -> None:
    code_home = tmp_path / "code-memory-home"
    log = tmp_path / "serve.log"
    fake_bin = tmp_path / "codebase-memory-mcp"
    fake_bin.write_text(
        "#!/bin/sh\n"
        'printf "HOME=%s\\n" "$HOME" >> "$CODE_MEMORY_TEST_LOG"\n'
        'printf "ARGS=%s\\n" "$*" >> "$CODE_MEMORY_TEST_LOG"\n',
        encoding="utf-8",
    )
    fake_bin.chmod(0o755)
    env = _launcher_env(
        tmp_path,
        ALFRED_CODE_MEMORY_BIN=str(fake_bin),
        ALFRED_CODE_MEMORY_AUTOFETCH="0",
        ALFRED_CODE_MEMORY_HOME=str(code_home),
        CODE_MEMORY_TEST_LOG=str(log),
    )

    res = subprocess.run(
        ["bash", str(SCRIPT), "serve", "--probe"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    text = log.read_text(encoding="utf-8")
    assert f"HOME={code_home}" in text
    assert "ARGS=--probe" in text


def test_process_code_memory_binary_overrides_runtime_env_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    home.mkdir()
    runtime.mkdir()
    file_bin = tmp_path / "file-codebase-memory-mcp"
    process_bin = tmp_path / "process-codebase-memory-mcp"
    for path, label in ((file_bin, "file"), (process_bin, "process")):
        path.write_text(f"#!/bin/sh\\necho {label}\\n", encoding="utf-8")
        path.chmod(0o755)
    (runtime / ".env").write_text(
        f"ALFRED_CODE_MEMORY_BIN={file_bin}\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "ALFRED_HOME": str(runtime),
        "ALFRED_CODE_MEMORY_BIN": str(process_bin),
        "ALFRED_CODE_MEMORY_AUTOFETCH": "0",
    }

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert f"binary:  {process_bin}" in res.stderr
    assert str(file_bin) not in res.stderr


def test_launcher_uses_default_runtime_env_when_home_is_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime_a = home / ".alfred"
    runtime_b = tmp_path / "runtime-b"
    home.mkdir()
    runtime_a.mkdir(parents=True)
    runtime_b.mkdir()
    (runtime_a / ".env").write_text(
        f"ALFRED_HOME={runtime_b}\nALFRED_CODE_MEMORY_AUTOFETCH=0\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("ALFRED_HOME", None)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert f"index-dir:   {runtime_a}/state/code-memory" in res.stderr
    assert f"{runtime_b}/state/code-memory" not in res.stderr


def test_launcher_keeps_process_home_when_rc_points_elsewhere(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    home.mkdir()
    runtime_a.mkdir()
    runtime_b.mkdir()
    (runtime_a / ".env").write_text("ALFRED_CODE_MEMORY_AUTOFETCH=1\n", encoding="utf-8")
    (runtime_b / ".env").write_text("ALFRED_CODE_MEMORY_AUTOFETCH=0\n", encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = str(runtime_b)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert f"index-dir:   {runtime_b}/state/code-memory" in res.stderr
    assert f"{runtime_a}/state/code-memory" not in res.stderr


def test_launcher_loads_runtime_env_code_memory_scope(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    home.mkdir()
    runtime.mkdir()
    (runtime / ".env").write_text(
        "ALFRED_CODE_MEMORY_AUTOFETCH=0\nALFRED_CODE_MEMORY_REPOS=org/new\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = str(runtime)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert "repos:       configured scope not found: org/new" in res.stderr


def test_launcher_preserves_process_code_memory_over_runtime_env(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    home.mkdir()
    runtime.mkdir()
    (runtime / ".env").write_text(
        "ALFRED_CODE_MEMORY_AUTOFETCH=0\nALFRED_CODE_MEMORY_REPOS=org/new\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = str(runtime)
    env["ALFRED_CODE_MEMORY_REPOS"] = "org/process"
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert "repos:       configured scope not found: org/process" in res.stderr
    assert "org/new" not in res.stderr


def test_launcher_ignores_stale_rc_code_memory_when_process_home_is_active(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    home.mkdir()
    runtime_a.mkdir()
    runtime_b.mkdir()
    (home / ".alfredrc").write_text(
        f"ALFRED_HOME={runtime_a}\nALFRED_CODE_MEMORY_REPOS=org/stale\n",
        encoding="utf-8",
    )
    (runtime_b / ".env").write_text("ALFRED_CODE_MEMORY_AUTOFETCH=0\n", encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = str(runtime_b)
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert f"index-dir:   {runtime_b}/state/code-memory" in res.stderr
    assert "repos:       auto-discovered:" in res.stderr
    assert "org/stale" not in res.stderr


def test_launcher_empty_alfred_home_loads_default_home_for_code_memory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = home / ".alfred"
    home.mkdir()
    runtime.mkdir()
    (runtime / ".env").write_text(
        "ALFRED_CODE_MEMORY_AUTOFETCH=0\nALFRED_CODE_MEMORY_REPOS=org/runtime\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = ""
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert f"index-dir:   {runtime}/state/code-memory" in res.stderr
    assert "repos:       configured scope not found: org/runtime" in res.stderr


def test_launcher_ignores_explicit_alfredrc_for_code_memory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    stale_runtime = tmp_path / "stale"
    custom_rc = tmp_path / "custom.alfredrc"
    home.mkdir()
    runtime.mkdir()
    stale_runtime.mkdir()
    custom_rc.write_text(
        f"ALFRED_HOME={runtime}\n"
        f"ALFRED_CODE_MEMORY_INDEX_DIR={runtime / 'custom-index'}\n"
        "ALFRED_CODE_MEMORY_AUTOFETCH=0\n"
        "ALFRED_CODE_MEMORY_REPOS=org/custom\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFREDRC"] = str(custom_rc)
    env["ALFRED_HOME"] = str(stale_runtime)
    env.pop("ALFRED_CODE_MEMORY_INDEX_DIR", None)
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert "env-file:" in res.stderr
    assert f"index-dir:   {stale_runtime}/state/code-memory" in res.stderr
    assert "org/custom" not in res.stderr


def test_launcher_strips_env_file_comments_before_code_memory_filter(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = home / "runtime"
    home.mkdir()
    runtime.mkdir()
    (runtime / ".env").write_text(
        "ALFRED_CODE_MEMORY_AUTOFETCH=0\nALFRED_CODE_MEMORY_REPOS=org/commented\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = str(runtime)
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert f"index-dir:   {runtime}/state/code-memory" in res.stderr
    assert "repos:       configured scope not found: org/commented" in res.stderr


def test_launcher_ignores_indirect_pointer_for_default_code_memory_runtime(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    default_runtime = home / ".alfred"
    pointed_runtime = tmp_path / "runtime"
    custom_rc = tmp_path / "custom.alfredrc"
    home.mkdir()
    default_runtime.mkdir()
    pointed_runtime.mkdir()
    (home / ".alfredrc").write_text(
        f"ALFREDRC={custom_rc}\nALFRED_CODE_MEMORY_REPOS=org/stale\n",
        encoding="utf-8",
    )
    custom_rc.write_text(
        f"ALFRED_HOME={pointed_runtime}\nALFRED_CODE_MEMORY_REPOS=org/pointed\n",
        encoding="utf-8",
    )
    (default_runtime / ".env").write_text(
        "ALFRED_CODE_MEMORY_AUTOFETCH=0\nALFRED_CODE_MEMORY_REPOS=org/default\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ALFRED_HOME"] = ""
    env.pop("ALFREDRC", None)
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert "env-file:" in res.stderr
    assert f"index-dir:   {default_runtime}/state/code-memory" in res.stderr
    assert "repos:       configured scope not found: org/default" in res.stderr
    assert "org/stale" not in res.stderr
    assert "org/pointed" not in res.stderr
    assert str(pointed_runtime) not in res.stderr


def test_launcher_ignores_pointed_rc_memory_when_process_home_is_active(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    stale_runtime = tmp_path / "stale-runtime"
    custom_rc = tmp_path / "custom.alfredrc"
    home.mkdir()
    runtime.mkdir()
    stale_runtime.mkdir()
    (home / ".alfredrc").write_text(f"ALFREDRC={custom_rc}\n", encoding="utf-8")
    custom_rc.write_text(
        f"ALFRED_HOME={stale_runtime}\n"
        "ALFRED_CODE_MEMORY_REPOS=org/stale\n"
        "ALFRED_CODE_MEMORY_AUTOFETCH=1\n",
        encoding="utf-8",
    )
    (runtime / ".env").write_text(
        "ALFRED_CODE_MEMORY_REPOS=org/runtime\nALFRED_CODE_MEMORY_AUTOFETCH=0\n",
        encoding="utf-8",
    )
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "ALFRED_HOME": str(runtime),
    }
    env.pop("ALFRED_CODE_MEMORY_AUTOFETCH", None)
    env.pop("ALFRED_CODE_MEMORY_REPOS", None)
    env.pop("ALFREDRC", None)

    res = subprocess.run(
        ["bash", str(SCRIPT), "doctor"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert res.returncode == 0, res.stderr
    assert "env-file:" in res.stderr
    assert f"index-dir:   {runtime}/state/code-memory" in res.stderr
    assert "repos:       configured scope not found: org/runtime" in res.stderr
    assert str(stale_runtime) not in res.stderr
    assert "org/stale" not in res.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
