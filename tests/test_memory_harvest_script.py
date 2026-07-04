from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from importlib import util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from fleet_brain import FleetBrain  # noqa: E402


def _load_script_module():
    spec = util.spec_from_file_location(
        "memory_harvest_script", REPO_ROOT / "bin" / "memory-harvest.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_harvest(tmp_path: Path, db: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "ALFRED_FLEET_BRAIN_DB": str(db),
    }
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "memory-harvest.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


@pytest.mark.parametrize(
    ("script", "sentinel"),
    [
        ("memory-harvest.py", "[MEMORY-HARVEST-DOCTOR-OK]"),
        ("memory-auto-promote.py", "[MEMORY-AUTO-PROMOTE-DOCTOR-OK]"),
        ("memory-consolidate.py", "[MEMORY-CONSOLIDATE-DOCTOR-OK]"),
    ],
)
def test_memory_wrappers_doctor_mode_short_circuits(
    tmp_path: Path, script: str, sentinel: str
) -> None:
    db = tmp_path / "brain.db"
    env = {
        **os.environ,
        "ALFRED_DOCTOR": "1",
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "ALFRED_FLEET_BRAIN_DB": str(db),
    }
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / script), "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert sentinel in result.stdout
    assert not db.exists()


def test_memory_harvest_queues_reviewable_candidates(tmp_path: Path) -> None:
    db = tmp_path / "brain.db"
    brain = FleetBrain(db_path=db)
    now = datetime.now(UTC)
    for idx in range(2):
        brain.record_failure(
            codename="huntress",
            repo="org/web",
            firing_id=f"fid-{idx}",
            subtype="error_timeout",
            summary="browserType.launch: Executable does not exist",
            engine="claude",
            created_at=now - timedelta(minutes=idx),
        )

    result = _run_harvest(tmp_path, db, "--json", "--no-slack")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["queued"] == 1
    candidates = brain.list_memory_candidates(status="candidate")
    assert len(candidates) == 1
    assert candidates[0].source == "memory-harvest"
    assert "Seen at least 2 times as of harvest time." in candidates[0].body


def test_memory_harvest_preview_does_not_queue(tmp_path: Path) -> None:
    db = tmp_path / "brain.db"
    brain = FleetBrain(db_path=db)
    for idx in range(2):
        brain.record_failure(
            codename="lucius",
            repo="org/api",
            firing_id=f"fid-{idx}",
            subtype="error_timeout",
            summary="timed out waiting for engine",
            engine="claude",
        )

    result = _run_harvest(tmp_path, db, "--preview", "--json", "--no-slack")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["applied"] is False
    assert payload["queued"] == 0
    assert len(payload["proposals"]) == 1
    assert brain.list_memory_candidates(status="candidate") == []


def test_memory_harvest_timeout_kills_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script_module()
    pid_file = tmp_path / "slow-brain.pid"
    slow_brain = tmp_path / "slow-brain.py"
    slow_brain.write_text(
        "\n".join(
            [
                "import os",
                "import time",
                "from pathlib import Path",
                "Path(os.environ['MEMORY_HARVEST_PID_FILE']).write_text(str(os.getpid()))",
                "time.sleep(30)",
            ]
        )
    )
    monkeypatch.setenv("MEMORY_HARVEST_PID_FILE", str(pid_file))
    monkeypatch.setattr(script, "_brain_script", lambda: slow_brain)

    args = script.build_parser().parse_args(["--timeout", "1", "--no-slack"])
    with pytest.raises(RuntimeError, match="timed out"):
        script._run_harvest(args)

    pid = int(pid_file.read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out memory harvest child was not reaped")


def test_slack_trigger_uses_rendered_queued_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    payload = {
        "applied": True,
        "queued": 3,
        "duplicates": 0,
        "proposals": [{"status": "duplicate", "candidate_id": "mem_existing"}],
    }
    posts: list[str] = []

    monkeypatch.setattr(script, "_run_harvest", lambda _args: payload)
    monkeypatch.setattr(
        script,
        "_post_slack",
        lambda message, *, severity="info": posts.append(message) or True,
    )

    assert script.main([]) == 0
    assert posts == []
    assert "queued=0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# memory-consolidate.py scheduled runner
# ---------------------------------------------------------------------------


def _run_consolidate(tmp_path: Path, db: Path, *args: str, arm: bool = False):
    env = {
        **os.environ,
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "ALFRED_FLEET_BRAIN_DB": str(db),
    }
    if arm:
        env["ALFRED_MEMORY_CONSOLIDATE"] = "1"
    else:
        env.pop("ALFRED_MEMORY_CONSOLIDATE", None)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "memory-consolidate.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_memory_consolidate_disarmed_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "brain.db"
    FleetBrain(db_path=db)  # create the schema

    result = _run_consolidate(tmp_path, db, "--json", "--no-slack", arm=False)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["enabled"] is False
    assert payload["decayed"] == 0
    assert payload["merged"] == 0


def test_memory_consolidate_dry_run_reports_without_writing(tmp_path: Path) -> None:
    """Armed + dry-run never touches AMS, so it runs end to end with no server."""
    db = tmp_path / "brain.db"
    FleetBrain(db_path=db)

    result = _run_consolidate(tmp_path, db, "--dry-run", "--json", "--no-slack", arm=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["enabled"] is True
    assert payload["dry_run"] is True


def test_memory_consolidate_honors_persisted_env_opt_in(tmp_path: Path) -> None:
    """Arming ALFRED_MEMORY_CONSOLIDATE in $ALFRED_HOME/.env (NOT the process
    env) is enough for the scheduled runner: the persisted opt-in is loaded."""
    alfred_home = tmp_path / "alfred"
    alfred_home.mkdir()
    (alfred_home / ".env").write_text("ALFRED_MEMORY_CONSOLIDATE=1\n")
    db = tmp_path / "brain.db"
    FleetBrain(db_path=db)

    env = {
        **os.environ,
        "ALFRED_HOME": str(alfred_home),
        "ALFRED_FLEET_BRAIN_DB": str(db),
    }
    env.pop("ALFRED_MEMORY_CONSOLIDATE", None)  # only the persisted file arms it
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "memory-consolidate.py"),
            "--dry-run",
            "--json",
            "--no-slack",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["enabled"] is True


def test_memory_consolidate_disarmed_does_not_create_ledger(tmp_path: Path) -> None:
    """A disarmed scheduled run is a true no-op that never opens/creates the DB,
    even when ALFRED_FLEET_BRAIN_DB points at a not-yet-existing path."""
    alfred_home = tmp_path / "alfred"
    missing_db = tmp_path / "nested" / "not-created" / "brain.db"

    env = {
        **os.environ,
        "ALFRED_HOME": str(alfred_home),
        "ALFRED_FLEET_BRAIN_DB": str(missing_db),
    }
    env.pop("ALFRED_MEMORY_CONSOLIDATE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "memory-consolidate.py"),
            "--json",
            "--no-slack",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["enabled"] is False
    # The disarmed run must not have created the DB (its parent dir does not even
    # exist), proving it never opened the ledger.
    assert not missing_db.exists()


def test_memory_consolidate_module_loads() -> None:
    spec = util.spec_from_file_location(
        "memory_consolidate_script", REPO_ROOT / "bin" / "memory-consolidate.py"
    )
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The runner exposes a build_parser with the documented flags.
    parser = module.build_parser()
    ns = parser.parse_args(["--stale-days", "90", "--dry-run"])
    assert ns.stale_days == 90
    assert ns.dry_run is True


def test_memory_consolidate_empty_child_output_is_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child that crashes before printing JSON (empty stdout, rc!=0) must be a
    hard failure, not a false 'disabled/no-op' that skips the failure path."""
    spec = util.spec_from_file_location(
        "memory_consolidate_empty_test", REPO_ROOT / "bin" / "memory-consolidate.py"
    )
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # A stub "brain script" that prints nothing and exits nonzero.
    crasher = tmp_path / "crasher.py"
    crasher.write_text("import sys\nsys.exit(3)\n")
    monkeypatch.setattr(module, "_brain_script", lambda: crasher)

    import argparse

    args = argparse.Namespace(stale_days=180, dry_run=False, timeout=30)
    with pytest.raises(RuntimeError) as excinfo:
        module._run_consolidate(args)
    assert "rc=3" in str(excinfo.value)


def test_memory_consolidate_main_reports_child_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner's main() returns nonzero (not 0) when the child crashes empty."""
    db = tmp_path / "brain.db"
    FleetBrain(db_path=db)
    # Point the wrapper's brain script at a crasher so the child exits empty rc!=0.
    crasher = tmp_path / "crash.py"
    crasher.write_text("import sys\nsys.exit(2)\n")
    spec = util.spec_from_file_location(
        "memory_consolidate_main_test", REPO_ROOT / "bin" / "memory-consolidate.py"
    )
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_brain_script", lambda: crasher)
    monkeypatch.setattr(module, "_post_slack", lambda *a, **k: True)

    rc = module.main(["--no-slack"])
    assert rc == 1
