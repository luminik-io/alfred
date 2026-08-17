import json
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])
source = (repo / "reports.py").read_text()
original = (pathlib.Path(__file__).parent / "seed" / "reports.py").read_text()
findings = []
try:
    payload = json.loads((repo / "security-review.json").read_text())
except Exception:
    payload = {}
rows = payload.get("findings", []) if isinstance(payload, dict) else []
if not isinstance(rows, list):
    rows = []
normalized = [json.dumps(row).lower() for row in rows if isinstance(row, dict)]


def has(lens, terms):
    return any(lens in row and all(term in row for term in terms) for row in normalized)


def has_any(lens, required, alternatives):
    return any(
        lens in row
        and all(term in row for term in required)
        and any(term in row for term in alternatives)
        for row in normalized
    )


checks = {
    "injection": has("injection", ["sql", "parameter"]),
    "authorization": has_any("author", ["account"], ["check", "verify", "allow"]),
    "secret": has("secret", ["token", "log"]),
    "ssrf": has("ssrf", ["callback", "allow"]),
}
for name, ok in checks.items():
    if not ok:
        findings.append(f"The review missed the {name} boundary or its concrete fix.")
required_fields = {"file", "line", "lens", "severity", "risk", "fix"}
if any(not required_fields.issubset(row) for row in rows if isinstance(row, dict)):
    findings.append("A finding is missing a required field.")
print(
    json.dumps(
        {
            "task_passed": all(checks.values()) and len(rows) == 4,
            "regression": source != original,
            "review_findings": findings,
        }
    )
)
