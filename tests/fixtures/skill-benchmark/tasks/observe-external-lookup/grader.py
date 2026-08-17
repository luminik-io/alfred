import importlib
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
sys.path.insert(0, str(repo))
lookup = importlib.import_module("lookup")
telemetry = importlib.import_module("telemetry")


class Client:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def fetch(self, key):
        if self.error:
            raise self.error
        return self.value


telemetry.reset()
lookup.lookup(Client("ok"), "private-key")
lookup.lookup(Client(None), "missing-key")
lookup.lookup(Client(error=RuntimeError("down")), "error-key")
event_text = json.dumps(telemetry.events).lower()
metric_text = json.dumps(telemetry.metrics).lower()
span_text = json.dumps(telemetry.spans).lower()
findings = []
checks = {
    "structured outcomes": all(term in event_text for term in ("success", "not_found", "error")),
    "outcome metric": "lookup" in metric_text
    and all(term in metric_text for term in ("success", "not_found", "error")),
    "I/O span": "fetch" in span_text and "error" in span_text and "runtimeerror" in span_text,
    "secret redaction": "private-key" not in event_text + metric_text + span_text,
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
