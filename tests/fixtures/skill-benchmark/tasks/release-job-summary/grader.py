import json
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])
text = (repo / "CHANGELOG.md").read_text()
lower = text.lower()
version = (repo / "VERSION").read_text().strip()
findings = []
checks = {
    "cancelled jobs": "cancel" in lower and "status" in lower,
    "JSON output": "--json" in text and "json" in lower,
    "house sections": "## Unreleased" in text and "### Added" in text and "### Fixed" in text,
    "version unchanged": version == "0.7.0",
    "plain copy": "\u2014" not in text
    and not any(word in lower for word in ("seamless", "robust", "powerful")),
}
findings.extend(f"Missing {name}." for name, ok in checks.items() if not ok)
allowed = {".git", "VERSION", "CHANGELOG.md", "CHANGE.diff"}
extra = [p.name for p in repo.iterdir() if p.name not in allowed]
print(
    json.dumps(
        {
            "task_passed": all(checks.values()),
            "regression": bool(extra) or version != "0.7.0",
            "review_findings": findings,
        }
    )
)
