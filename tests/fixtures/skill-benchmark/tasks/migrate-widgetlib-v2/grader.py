import json
import os
import pathlib
import subprocess
import sys

repo = pathlib.Path(sys.argv[1])
env = dict(os.environ)
env["PYTHONDONTWRITEBYTECODE"] = "1"
tests = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-v"],
    cwd=repo,
    env=env,
    capture_output=True,
    text=True,
)
pyproject = (repo / "pyproject.toml").read_text()
lock = (repo / "uv.lock").read_text()
app = (repo / "app.py").read_text()
note = (repo / "MIGRATION_NOTES.md").read_text() if (repo / "MIGRATION_NOTES.md").exists() else ""
findings = []
checks = {
    "manifest pin": "widgetlib==2.0.0" in pyproject,
    "lockfile pin": 'name = "widgetlib"' in lock and 'version = "2.0.0"' in lock,
    "new call site": "widgetlib_v2" in app
    and "format_value" in app
    and "compact=False" in app.replace(" ", ""),
    "old call removed": "widgetlib_v1" not in app and ".render(" not in app,
    "migration note": all(term in note.lower() for term in ("1.4.0", "2.0.0", "format_value")),
}
findings.extend(f"Missing {name}." for name, ok in checks.items() if not ok)
print(
    json.dumps(
        {
            "task_passed": tests.returncode == 0 and all(checks.values()),
            "regression": tests.returncode != 0,
            "review_findings": findings,
        }
    )
)
