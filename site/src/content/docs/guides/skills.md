---
title: Claude Code skills
description: Recommended skill set for an autonomous engineering fleet, install commands, per-agent matrix.
---

Skills are small bundles (markdown + optional scripts) that extend Claude Code's
tool surface. Alfred ships a first-party starter set and a license-audited
registry for optional local and fetched packs. The default desktop setup
installs the first-party starter set locally so the fleet has planning, tests,
security review, observability, migration, and release-note help from the first
run. Curated third-party review, frontend, debugging, gstack, and headroom packs
are explicit operator installs.

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

## First-party starter set

These are installed by Alfred Desktop during the full local setup and by
`alfred skills install --starter` in the CLI-only path. They are all
Alfred-authored local copies, so the install is offline and deterministic.

| Skill | Source | Used by | Why |
|---|---|---|---|
| `spec-to-issues` | Alfred first-party | planner | Converts specs into issue queues |
| `write-tests` | Alfred first-party | test-engineer, feature-dev | Focused coverage additions |
| `review-security` | Alfred first-party | reviewer, feature-dev | Review checklist for risky code |
| `add-observability` | Alfred first-party | feature-dev, ops-watch | Logging and metrics prompts |
| `migrate-dependency` | Alfred first-party | feature-dev | Dependency upgrade workflow |
| `changelog-and-release-notes` | Alfred first-party | feature-dev, ops-watch | Release notes and changelog drafts |

## Curated optional packs

These are part of the registry, but not part of `--starter`. Install the
vendored packs explicitly when you want the heavier specialist lenses. They are
still local offline copies once installed. Fetched packs require `--yes` because
they reach out to third-party sources at install time.

| Skill | Source | Used by | Why |
|---|---|---|---|
| `code-review-and-quality` | addyosmani/agent-skills | feature-dev, reviewer, fixer | Multi-axis review |
| `security-and-hardening` | addyosmani/agent-skills | feature-dev, reviewer | Security-specific lens |
| `frontend-ui-engineering` | addyosmani/agent-skills | feature-dev | Production UI patterns |
| `debugging-and-error-recovery` | addyosmani/agent-skills | bug-triage, ops-watch | Systematic root-cause path |
| `vercel-react-best-practices` | vercel-labs/agent-skills | feature-dev | React and Next.js performance guardrails |
| `gstack` | garrytan/gstack | optional: reviewer, triage, e2e-runner | CLI-first review, QA, and ship flow |
| `headroom` | headroomlabs-ai/headroom | optional | Token and context inspection |

## Install

```sh
alfred skills list
alfred skills install --starter

alfred skills install code-review-and-quality
alfred skills install security-and-hardening
alfred skills install frontend-ui-engineering
alfred skills install debugging-and-error-recovery
alfred skills install vercel-react-best-practices

# Optional fetched packs. These require explicit confirmation because they
# pull from third-party sources at install time.
alfred skills install gstack --yes
alfred skills install headroom --yes
```

For the full CLI reference, see [`docs/SKILLS.md#the-alfred-skills-command`](https://github.com/luminik-io/alfred/blob/main/docs/SKILLS.md#the-alfred-skills-command).

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
first-party starter set for the default engineering fleet, plus optional
vendored and fetched packs for teams that want heavier tools. The registry
records source, license, install method, and default roles so a fleet can stay
batteries-included without hiding third-party code from the operator.
