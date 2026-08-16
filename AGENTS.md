# AGENTS.md

Guidance for AI coding agents working in this repository. Humans: read
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md)
first; this file is the short version those agents need.

## What this repo is

Alfred is the open-source coordination and supervision layer for local coding
agents. It invokes Claude Code and Codex through authenticated local CLIs. The
OS scheduler (`launchd` on macOS or `systemd --user` on Linux) starts each role.
`lib/agent_runner/` gives runs shared locks, preflight checks, and limits.
Roles that change or review code use isolated git worktrees. Stable role slugs
control runtime identity. Optional
roster themes change display names only. `examples/` contains tutorial agents.

Users inspect and steer the team through the Alfred CLI (`bin/alfred`), the
optional `alfred serve` JSON API, the optional Tauri desktop client under
`clients/desktop`, and Slack. The desktop client carries a Claude and Codex
subscription usage rail (backed by the live `GET /api/usage` endpoint, read from
local CLI state with no billing API; the same data is available from
`alfred usage`) and an agent roster. Any issue carrying the approval
gate label (`agent:plan-pending-approval`) is held from scheduled pickup until
the configured approver clears it; runs emit step-level events so the run
timeline shows real progress.

## Design boundaries (do not cross without a discussion)

- **Single-host install.** One operator or small team, one trusted host, one
  config. Not multi-tenant and not a hosted SaaS.
- **The OS schedules; Alfred runs.** No long-running orchestration loop.
- **Local CLIs, not a model gateway.** Alfred shells out to `claude` / `codex`.
- **Use harness capabilities.** Prefer supported Claude Code and Codex
  capabilities over duplicate implementations.

Scope-broadening changes get declined. If a change touches these boundaries,
open a discussion before writing code. See [`ROADMAP.md`](ROADMAP.md).

## Conventions

- **No em-dashes** in prose or comments. Use periods, commas, colons, or
  parentheses.
- **No `Co-Authored-By` or AI-attribution trailers** on commits. Conventional
  commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- One role or runtime surface per PR, with prompt + tests + docs where
  applicable. Keep PRs scoped.
- This is a public repo: no host paths, no cloud account IDs, no secrets, no
  personal handles. `bin/scrub-check.sh` enforces this.

## Checks before opening a PR

```sh
uv run --with 'ruff>=0.6' ruff check .
uv run --with 'ruff>=0.6' ruff format --check .
uv run --with 'mypy>=1.10' mypy lib/
uv run --with pytest pytest tests/ -v
bash bin/scrub-check.sh
```

Shell scripts must pass `shellcheck -S warning`. The docs site
(`site/`) must `npm run build` cleanly if you touch it.
