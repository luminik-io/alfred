<!-- alfred:auto-seed v1 -->
<!--
  Role: reviewer

  Alfred installs this starter at $ALFRED_HOME/prompts/reviewer.md. The live
  reviewer already enforces its read-only workflow, review shape, freshness
  checks, and selected Claude Code, Codex, or hybrid engine. Edit this file only
  to add rules that are specific to your repositories. To activate an edited
  file, replace the first line with this exact marker:

    <!-- alfred:operator-guidance v1 -->

  Files without that first-line marker are ignored. This makes operator intent
  explicit and prevents old generated prompts from becoming runtime policy.

  Available placeholders:
    AGENT_CODENAME   stable runtime role slug ("reviewer")
    GH_ORG           GitHub organization
    ALFRED_HOME      Alfred runtime home
    WORKSPACE_ROOT   root containing local repository checkouts
    REVIEW_REPOS     comma-separated reviewer scope
    REPO_SLUG        repository being reviewed
    PR_NUMBER        pull-request number
    PR_TITLE         pull-request title
    LOCAL_REPO       resolved local checkout path
-->

# Reviewer operator guidance

Review `${GH_ORG}/${REPO_SLUG}#${PR_NUMBER}` as the `${AGENT_CODENAME}` role.

Keep this file for repository-specific facts that cannot be inferred reliably
from code, tests, or Alfred's code map. Useful additions include:

- invariants that every change in this repository must preserve;
- locations of related contracts or schemas in other repositories;
- mandatory test commands for high-risk areas;
- release, migration, or rollback constraints;
- security boundaries that deserve extra scrutiny.

Do not duplicate the review workflow here. The runtime already fetches the
exact PR head, existing bot feedback, changed paths, deterministic impact
evidence, and surrounding source. It rechecks that the PR is open before
posting and records the reviewed head SHA in its comment.

Repository scope: `${REVIEW_REPOS}`
Local checkout: `${LOCAL_REPO}`
PR title: `${PR_TITLE}`
