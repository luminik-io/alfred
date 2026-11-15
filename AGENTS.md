# AGENTS.md

Guide for AI coding agents and contributors. `CLAUDE.md` points here.

## Status

This repository is archived and no longer maintained. Read it as a reference
or fork it; do not expect issues or pull requests to be reviewed. The README's
[status section](README.md#status) explains why and lists alternatives.

## What this repo is

Alfred runs authenticated Claude Code, Codex and OpenCode CLIs as a small
engineering team on one host. The OS scheduler (launchd on macOS,
`systemd --user` on Linux) starts short-lived role scripts in `bin/`. Each run
takes a lock, checks caps and auth, does one unit of work against GitHub
(usually in an isolated git worktree), records events and exits.

## Repository map

- `bin/`: role scripts (`planner.py`, `triage.py`, `senior-dev.py`,
  `test-engineer.py`, `reviewer.py`, `fixer.py`, `automerge.py`,
  `architect.py`), the operator CLI `bin/alfred`, and `bin/agent-launch`.
- `lib/agent_runner/`: shared runtime. Locks, spend ledger, fail-streak halt
  and pause markers (`state.py`); engine calls (`process.py`,
  `engine_registry.py`); GitHub claims and worktrees (`github.py`).
- `lib/merge_gate.py`: the fail-closed merge predicate.
- `lib/labels.py`: the GitHub label state machine.
- `lib/scheduler.py`: launchd and systemd wrapper.
- `lib/alfred_config.py`: registry of every `ALFRED_*` variable.
- `lib/server/`: the `alfred serve` JSON API.
- `clients/desktop/`: Tauri and React desktop client.
- `site/`: Astro docs and project site.
- `tests/`: pytest suite. Tests put `lib/` on `sys.path` and load `bin/`
  scripts by file path; follow the pattern in a neighboring test.

## Commands

Python 3.11 to 3.13:

```sh
uv run --extra dev pytest tests/
uv run --with 'ruff==0.15.22' ruff check .
uv run --with 'ruff==0.15.22' ruff format --check .
uv run --with 'mypy==2.3.0' mypy lib/
bash bin/scrub-check.sh
```

Shell scripts must pass `shellcheck -S warning`.

Desktop client (Node 22.23.5, Rust stable):

```sh
cd clients/desktop
npm ci && npm run typecheck && npm run lint && npm run test && npm run build
cargo fmt --manifest-path src-tauri/Cargo.toml --check
(cd src-tauri && cargo test)
```

Site (Node 22.23.4 or newer): `cd site && npm ci && npm run build`.

## Contracts

- A new `ALFRED_*` variable is declared in `lib/alfred_config.py`, then
  `uv run python bin/alfred-config-doc.py` regenerates `.env.example` and
  `docs/CONFIG.md`. `tests/test_config_registry.py` fails otherwise.
- `bin/reviewer.py` writes the `Reviewed-head-sha:` marker and
  `bin/automerge.py` parses it. Change both together, with a test.
- Labels are added or renamed only in `lib/labels.py`, using role slugs.
- Pytest runs with `filterwarnings = error`.

## Do not run

- `deploy.sh`, `install.sh`, `get.sh`: they install into and reload a live
  runtime.
- `launchctl`, `systemctl --user`, `bin/agent-launch` or any role script
  outside tests: they fire real agents against real repositories.
- `claude`, `codex`, `opencode`, or the `alfred auth`/`engine` probes: they
  call paid engines.

## Conventions

- No em dashes. American spelling.
- Comments say why, in one line. History and measurements go in the commit
  message or PR body.
- Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`), with no AI
  attribution trailers.
- Public repository: no host paths, account identifiers, private repository
  names or secrets. `bin/scrub-check.sh` enforces this.
