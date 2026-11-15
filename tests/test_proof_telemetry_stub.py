"""The removed proof-telemetry reporter leaves a stub so old scheduler rows exit cleanly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

STUB = Path(__file__).resolve().parent.parent / "bin" / "proof-telemetry.py"


def test_stub_exits_zero_and_says_how_to_clean_up() -> None:
    proc = subprocess.run(
        [sys.executable, str(STUB)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0
    assert "proof telemetry was removed" in proc.stdout
    assert "alfred.proof-telemetry" in proc.stdout
    assert "agents.conf" in proc.stdout
    assert "redeploy" in proc.stdout
    # bin/doctor.sh counts a -DISABLED sentinel as healthy.
    assert "[PROOF-TELEMETRY-DISABLED]" in proc.stdout
