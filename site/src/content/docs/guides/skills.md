---
title: Agent skills
description: Measured starter skills for Alfred roles, plus explicit optional packs.
---

Skills are local instruction bundles for a specific task. Alfred can pass them
to Claude Code, Codex, or OpenCode through its role-scoped prompt injector. The
default setup installs only the first-party skills that improved deterministic
task results. Other first-party and third-party packs remain explicit installs.

Full guide at [`docs/SKILLS.md`](https://github.com/luminik-io/alfred/blob/main/docs/SKILLS.md). Highlights:

## Where the default starter set lives

After Alfred Desktop's full local setup, or after `alfred skills install
--starter` in the CLI-only path, the first-party starter skills land here.
Optional vendored and fetched packs install beside these only when you run their
explicit install commands.

```
~/.claude/skills/
├── spec-to-issues/SKILL.md
├── review-security/SKILL.md
└── add-observability/SKILL.md
```

## First-party starter set

These are installed by Alfred Desktop during full local setup and by `alfred
skills install --starter` in the CLI-only path. Each passed two held-out paired
tasks with no skill-arm failure, regression increase, or review-finding
increase. The install is local, offline, and deterministic.

| Skill | Source | Used by | Why |
|---|---|---|---|
| `spec-to-issues` | Alfred first-party | planner | Converts specs into issue queues |
| `review-security` | Alfred first-party | reviewer, feature-dev | Review checklist for risky code |
| `add-observability` | Alfred first-party | feature-dev, ops-watch | Logging and metrics prompts |

`write-tests`, `migrate-dependency`, and `changelog-and-release-notes` remain
available as explicit first-party installs. `write-tests` was neutral. The
`migrate-dependency` and `changelog-and-release-notes` skills each improved one
task but did not meet the two-task evidence floor. Alfred does not install or
inject them by default.

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

Skills run with the same permissions as the coding CLI. They can read files the
local user can read, write files the user can write, run shell commands, invoke
tools, and use the network. Treat a new skill like any other executable
dependency:

1. Read the `SKILL.md`.
2. Skim the scripts the skill might invoke.
3. Run a Snyk / CodeQL scan on unfamiliar sources.
4. Pin to a specific commit when installing from a third-party tap.

A worktree separates git changes. It does not isolate the process from the home
directory, credentials, or network. Use a dedicated operating-system user,
virtual machine, or container, plus egress controls where required.

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
