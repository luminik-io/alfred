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
publish_module = importlib.import_module("publish")
telemetry = importlib.import_module("telemetry")


class Queue:
    def __init__(self, error=None):
        self.error = error

    def send(self, topic, payload):
        if self.error:
            raise self.error


secret_payload = {"token": "private-token", "event": "updated"}
telemetry.reset()
publish_module.publish(Queue(), "events", secret_payload)
publish_module.publish(Queue(), "", secret_payload)
publish_module.publish(Queue(error=RuntimeError("down")), "events", secret_payload)
event_text = json.dumps(telemetry.events).lower()
metric_text = json.dumps(telemetry.metrics).lower()
span_text = json.dumps(telemetry.spans).lower()
findings = []
checks = {
    "structured outcomes": all(term in event_text for term in ("sent", "rejected", "error")),
    "outcome metric": "publish" in metric_text
    and all(term in metric_text for term in ("sent", "rejected", "error")),
    "queue span": "queue" in span_text and "error" in span_text and "runtimeerror" in span_text,
    "secret redaction": "private-token" not in event_text + metric_text + span_text,
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
