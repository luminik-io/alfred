from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from fleet_brain import FleetBrain  # noqa: E402


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
