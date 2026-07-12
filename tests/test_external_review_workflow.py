"""Security and trigger contract for the required external-review status."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "external-review-gate.yml"


def test_workflow_checks_out_default_branch_and_never_pull_request_code():
    text = WORKFLOW.read_text()
    assert "ref: ${{ github.event.repository.default_branch }}" in text
    assert "github.event.pull_request.head" not in text
    assert "pull_request_target:" in text
    assert "pull_request_review:" not in text
    assert "statuses: write" in text


def test_workflow_rechecks_every_review_evidence_event():
    text = WORKFLOW.read_text()
    assert "types: [opened, reopened, synchronize, ready_for_review]" in text
    assert "types: [created, edited, deleted]" in text
    assert 'cron: "*/5 * * * *"' in text
    assert "pulls?state=open&per_page=100" in text
    assert "--paginate --slurp" in text


def test_workflow_publishes_one_stable_required_context_to_exact_head():
    text = WORKFLOW.read_text()
    assert 'context="External review gate"' in text
    assert 'head_sha="$(gh pr view' in text
    assert '[ "$evaluated_sha" = "$head_sha" ]' in text
    assert '[ "$current" = "$STATE" ]' in text
    assert "group: external-review-gate-${{ github.repository }}-${{ matrix.pr }}" in text
    assert "cancel-in-progress: true" in text
    assert '"repos/$REPO/statuses/$SHA"' in text
