import json
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])
findings = []
try:
    payload = json.loads((repo / "issues.json").read_text())
except Exception:
    payload = []
issues = payload.get("issues", []) if isinstance(payload, dict) else payload
if not isinstance(issues, list) or len(issues) != 1:
    findings.append("The approved single-repo slice must produce one issue.")
    issue = {}
else:
    issue = issues[0] if isinstance(issues[0], dict) else {}
text = json.dumps(issue).lower()
checks = {
    "repo": "acme/job-api" in text,
    "route": "/v1/jobs/{job_id}/cancel" in text and "202" in text and "cancelling" in text,
    "not found": "404" in text,
    "authorization": "authenticated" in text or "operator" in text,
    "label": "agent:implement" in text,
    "test": bool(issue.get("test")) if isinstance(issue, dict) else False,
    "out_of_scope": "scheduler" in text and "desktop" in text and "started" in text,
    "title": str(issue.get("title", "")).startswith("feat(") if isinstance(issue, dict) else False,
}
findings.extend(f"Missing {name}." for name, ok in checks.items() if not ok)
changed_spec = (repo / "SPEC.md").read_text() != (
    pathlib.Path(__file__).parent / "seed" / "SPEC.md"
).read_text()
allowed = {".git", "SPEC.md", "issues.json"}
extra = [p.name for p in repo.iterdir() if p.name not in allowed]
print(
    json.dumps(
        {
            "task_passed": all(checks.values()) and len(issues) == 1,
            "regression": changed_spec or bool(extra),
            "review_findings": findings,
        }
    )
)
