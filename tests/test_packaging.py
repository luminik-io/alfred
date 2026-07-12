"""Packaging contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


def test_wheel_maps_lib_modules_to_top_level_imports():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["only-include"] == ["lib"]
    assert wheel["sources"] == ["lib"]


def test_desktop_bundle_contains_required_core_resources():
    config = json.loads(Path("clients/desktop/src-tauri/tauri.conf.json").read_text())
    resources = config["bundle"]["resources"]

    assert resources["../../../skills"] == "alfred-core/skills"
    for required in ("bin", "lib", "prompts", "skills"):
        assert Path(required).exists(), f"desktop core resource is missing: {required}"


def test_wheel_smoke_import_and_console_script(tmp_path):
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()

    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        # agent_runner is now a package with a thin __init__ re-exporting
        # the public API; the historical single-file layout is gone.
        assert "agent_runner/__init__.py" in names
        # A few load-bearing submodules should always ship with the wheel.
        for sub in ("paths", "process", "result", "github", "state"):
            assert f"agent_runner/{sub}.py" in names, f"wheel missing agent_runner/{sub}.py"
        assert "alfred_os_cli.py" in names
        # The curated skill-pack workflow must be usable from an installed
        # wheel: the shared CLI module, the pure core, the manifest, and the
        # vendored tree all ship (review finding on PR #382).
        assert "skill_packs.py" in names
        assert "skills_cli.py" in names
        assert "skills/packs.toml" in names
        assert "skills/NOTICE.md" in names
        assert any(
            name.startswith("skills/vendored/") and name.endswith("SKILL.md") for name in names
        )
        assert any(
            name.startswith("skills/vendored/") and name.endswith("LICENSE") for name in names
        )
        entry_points = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        assert "alfred-os = alfred_os_cli:main" in zf.read(entry_points).decode()


def test_operator_cli_exposes_claude_wrapper():
    result = subprocess.run(
        [sys.executable, "bin/alfred", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "claude" in result.stdout
    assert "manage Claude Code account routing" in result.stdout
    assert "doctor" in result.stdout
    assert "run host preflight checks for the configured fleet" in result.stdout


def test_operator_cli_owns_claude_probe():
    result = subprocess.run(
        [sys.executable, "bin/alfred", "claude", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "status,primary,secondary,swap,probe" in result.stdout
    removed_helper = "alfred" + "-claude"
    assert not Path("bin", removed_helper).exists()


def test_deploy_help_is_read_only(tmp_path):
    alfred_home = tmp_path / "alfred-runtime"
    result = subprocess.run(
        ["bash", "deploy.sh", "--help"],
        env={"HOME": str(tmp_path), "ALFRED_HOME": str(alfred_home), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Usage: ./deploy.sh" in result.stdout
    assert "Deploy Alfred runtime files" in result.stdout
    assert "--adopt-legacy-ams" in result.stdout
    assert result.stderr == ""
    assert not alfred_home.exists()


def test_deploy_rejects_unknown_arguments_before_side_effects(tmp_path):
    for args in (["--bogus"], ["", "--bogus"]):
        alfred_home = tmp_path / ("runtime-" + str(len(args)))
        result = subprocess.run(
            ["bash", "deploy.sh", *args],
            env={
                "HOME": str(tmp_path),
                "ALFRED_HOME": str(alfred_home),
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert "Usage: ./deploy.sh" in result.stderr
        assert not alfred_home.exists()


def test_runtime_dep_probes_match_pyproject_base_deps():
    """install.sh, deploy.sh, and bin/doctor.sh each probe/install the runtime
    deps by name. Those lists must stay in sync with pyproject's base
    dependencies, or a fresh install passes while doctor fails (or the
    installer ships packages nothing imports). Guards the drift where a dep
    was removed from the install path but doctor still required it."""
    import re

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    import_name = {"slack-sdk": "slack_sdk"}
    expected = {
        import_name.get(name, name)
        for name in (
            re.split(r"[><=~!\[]", dep, maxsplit=1)[0].strip()
            for dep in pyproject["project"]["dependencies"]
        )
    }

    # The scripts probe importability with `python -c "import a, b, c"`.
    probe_re = re.compile(r'-c "import ([\w, ]+)"')
    for script in ("install.sh", "deploy.sh", "bin/doctor.sh"):
        text = Path(script).read_text(encoding="utf-8")
        probes = probe_re.findall(text)
        assert probes, f"{script} no longer probes runtime deps; update this test"
        for probe in probes:
            probed = {name.strip() for name in probe.split(",")}
            assert probed == expected, (
                f"{script} probes {sorted(probed)} but pyproject base deps are "
                f"{sorted(expected)}; keep them in sync"
            )

    # The uv pip install fallbacks must not install packages pyproject dropped
    # (or miss ones it added). The quoted "name>=floor" pins only appear in the
    # runtime-dep install blocks.
    dep_line_re = re.compile(r'"([a-z0-9-]+)>=[0-9.]+"')
    for script in ("install.sh", "deploy.sh"):
        text = Path(script).read_text(encoding="utf-8")
        installed = {import_name.get(name, name) for name in dep_line_re.findall(text)}
        assert installed == expected, (
            f"{script} installs {sorted(installed)} but pyproject base deps are "
            f"{sorted(expected)}; keep them in sync"
        )


def test_install_script_uses_exact_homebrew_formula_probe():
    text = Path("install.sh").read_text(encoding="utf-8")

    assert 'brew list --formula "$1" >/dev/null 2>&1' in text
    assert 'brew list --cask "$1" >/dev/null 2>&1' in text
    assert "brew tap --list" not in text
    assert "brew list --formula | grep -q" not in text
    assert "${pkg%@*}" not in text
