---
title: Claude Code skills
description: Recommended skill set for an autonomous engineering fleet, install commands, per-agent matrix.
---

Skills are small bundles (markdown + optional scripts) that extend Claude Code's
tool surface. Alfred ships a curated, license-audited starter set and a registry
for optional packs. The default desktop setup installs the starter set locally
so the fleet has review, security, frontend, debugging, and test-writing help
from the first run.

Full guide at [`docs/SKILLS.md`](https://github.com/luminik-io/alfred/blob/main/docs/SKILLS.md). Highlights:

## Where they live

```
~/.claude/skills/
├── code-review/SKILL.md
├── code-review-and-quality/SKILL.md
├── debugging-and-error-recovery/SKILL.md
├── frontend-ui-engineering/SKILL.md
├── security-and-hardening/SKILL.md
├── spec-driven-development/SKILL.md
├── autofix/SKILL.md
└── gstack/                  # gstack tap installs as a directory of subskills
    ├── browse/
    ├── investigate/
    ├── qa/
    ├── review/
    └── ship/
```

## Starter set for an autonomous engineering fleet

| Skill | Source | Used by | Why |
|---|---|---|---|
| `code-review-and-quality` | addyosmani/agent-skills | feature-dev, reviewer, fixer | Multi-axis review |
| `security-and-hardening` | addyosmani/agent-skills | feature-dev, reviewer | Security-specific lens |
| `frontend-ui-engineering` | addyosmani/agent-skills | feature-dev | Production UI patterns |
| `debugging-and-error-recovery` | addyosmani/agent-skills | bug-triage, ops-watch | Systematic root-cause path |
| `vercel-react-best-practices` | vercel-labs/agent-skills | feature-dev | React and Next.js performance guardrails |
| `spec-to-issues` | Alfred first-party | planner | Converts specs into issue queues |
| `write-tests` | Alfred first-party | test-engineer, feature-dev | Focused coverage additions |
| `review-security` | Alfred first-party | reviewer, feature-dev | Review checklist for risky code |
| `add-observability` | Alfred first-party | feature-dev, ops-watch | Logging and metrics prompts |
| `migrate-dependency` | Alfred first-party | feature-dev | Dependency upgrade workflow |
| `changelog-and-release-notes` | Alfred first-party | feature-dev, ops-watch | Release notes and changelog drafts |
| `gstack` | garrytan/gstack | optional: reviewer, triage, e2e-runner | CLI-first review, QA, and ship flow |
| `headroom` | headroomlabs-ai/headroom | optional | Token and context inspection |

## Install

```sh
alfred skills list
alfred skills install --starter

# Optional fetched packs. These require explicit confirmation because they
# pull from third-party sources at install time.
alfred skills install gstack --yes
alfred skills install headroom --yes
```

For a single fresh-install script, see [`docs/SKILLS.md#skill-install-automation`](https://github.com/luminik-io/alfred/blob/main/docs/SKILLS.md#skill-install-automation).

## Security note

Skills run with the same permissions as `claude`. They can read/write files in the agent's worktree, run shell commands, invoke tools. Treat any new skill the way you'd treat any other dependency:

1. Read the `SKILL.md`.
2. Skim the scripts the skill might invoke.
3. Run a Snyk / CodeQL scan on unfamiliar sources.
4. Pin to a specific commit when installing from a third-party tap.

The fleet's IAM-per-agent + per-firing-worktree-isolation patterns limit blast radius (a malicious skill in the Lucius worktree can't reach your home directory or the secondary Claude account). Mitigations, not prevention.

## Anti-recommendations

- **Anything that auto-publishes** (auto-tweet, auto-deploy, auto-merge). Use as draft-then-review only.
- **Skills that fork to the network without explicit allowlists.** Network egress from a worktree is a known agent attack vector.
- **Skills you have not read.** Skills are markdown. Read them.

## Where skills live in the framework's mental model

Skills are local operator assets, not a hosted service. Alfred ships a curated
starter set for the default engineering fleet, plus optional fetched packs for
teams that want heavier tools. The registry records source, license, install
method, and default roles so a fleet can stay batteries-included without hiding
third-party code from the operator.
