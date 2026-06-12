# Technical Architecture and Code Organization RFC

- **Status**: Draft for review
- **Date**: 2026-06-12
- **Author**: Alfred maintainers
- **Honors**: ADR 0002 (surface architecture), `docs/DESIGN_SPEC.md` (one lifecycle)
- **Constraints**: no over-engineering, no backward-compat burden, aggressive refactor welcomed

This RFC does not re-litigate ADR 0002 or the lifecycle. It takes them as fixed and proposes how the code should be organized to serve them.

---

## 0. TL;DR

The runtime is healthy and well tested, but the code organization has rotted in two specific ways: a handful of god-modules carry most of the surface area, and the persona runners in `bin/` each re-implement the same firing scaffold.

The recommendation: converge on a single installable package `alfred` with stable subpackages, collapse the persona runners onto one config-driven runner fed by a fleet registry, and land a typed sequenced event envelope (F1) and a real `lib/gates.py` (F2) as the two new load-bearing primitives.

---

## 1. Current-state map

### 1.1 Where the code actually lives

`bin/` holds the persona runners and CLI entrypoints; `lib/` holds the runtime modules. Three subpackages already exist (`agent_runner/`, `server/`, `memory/`, `connectors/`, `fleet_brain/`, `claude_proxy/`) and are the cleanest parts of the tree. The rest of `lib/` is a flat namespace.

| Area | Path | Grade | Note |
|---|---|---|---|
| Persona runners | `bin/*.py` | C | Heavy scaffold duplication (see 1.3) |
| Runner core | `lib/agent_runner/` | B | Already a proper package |
| Slack | `lib/slack_listener.py`, `slack_control.py`, `slack_intent.py`, plus more | D | The worst monolith cluster |
| Server | `lib/server/` package, `views.py` | B- | Already a package; `views.py` is the one god-file left |
| Fleet brain | `lib/fleet_brain/` package | B | Clean package |
| Memory | `lib/memory/` package | B | Clean small package |
| Connectors | `lib/connectors/` package | B+ | Good shape, plugin-ish already |
| Tests | `tests/` flat | B+ | Strong coverage, flat namespace |
| Desktop client | `clients/desktop/` (Tauri + React) | B | Working SSE parser, typed `types.ts` |

Honest summary grade for organization: **C+**. For correctness and test coverage: **B+**. The code works and is defended; it is just shaped badly for the next year of change.

### 1.2 The monolith problem

A few files carry a disproportionate share of complexity:

- `lib/slack_listener.py` - event intake, routing, conversation, ambient handling, formatting glue. This is the single biggest readability and merge-conflict liability.
- `lib/server/views.py` - every JSON route in one module.
- `lib/slack_control.py` and `lib/slack_intent.py` - control surface and intent routing, with unclear boundaries against the listener.

### 1.3 The persona runners and what they duplicate

The persona runners are the engineering fleet (`lucius`, `drake`, `rasalghul`, `nightwing`, `bane`, `robin`, the coordinator `batman`, and deterministic jobs like `gordon`, `fleet-doctor`, `damian`, `huntress`). Every one imports `agent_runner` and re-assembles the same firing scaffold by hand:

1. **Preflight** - each builds its own `PreflightSpec` and calls `preflight()` at the top of `main`.
2. **Locking** - each acquires a `with_lock(name)` mutex on its own.
3. **Spend** - each wires `SpendState` and a cost cap separately; the caps drift.
4. **Engine invocation** - each selects an engine and calls `invoke_agent_engine` / `claude_invoke` / `codex_invoke` with near-identical argument assembly.
5. **GitHub IO** - each repeats claim/release comment handling and PR IO calls.
6. **Event emission and firing close** - each opens an `EventLog`, emits `firing_*` events, and runs its own close epilogue.

The persona-specific logic (which issue to pick, how to build the prompt, what counts as success) is maybe 20-30% of each file. The other 70-80% is copied orchestration. This is the highest-value refactor target in the repo.

### 1.4 The `lib/` flat namespace

`lib/` has many top-level Python modules with no grouping. The `slack_*` cluster and several `issue_*` / `goal_*` / `slack_thread_*` clusters sit flat next to each other. The existing subpackages are the cleanest parts of the tree, which is the proof that packaging works here. Everything else is a flat bag where import order and `sys.path.insert` hacks substitute for real package structure.

---

## 2. Target organization

### 2.1 One installable package

Converge on a single Python package, importable as `alfred` (drop the `sys.path.insert` hack and the explicit `lib` path entirely). `pyproject.toml` already exists; make it the package root.

```
alfred/
  engine/        # claude + codex adapters, preflight, invocation, model selection
  fleet/         # the generic runner, firing lifecycle, lock, spend, events, gates
  slack/         # listener, control, intent, format, approval, trust (split the monoliths)
  server/        # FastAPI JSON/SSE API (already a package; split views.py)
  memory/        # memory tiers (already a package)
  brain/         # fleet brain + MCP (already a package)
  github/        # claim/release, PR IO, cross-repo
  connectors/    # linear, sentry, runner (already a package, keep as-is)
bin/             # thin entrypoints only: argv parse -> alfred.fleet.run(config)
prompts/         # delegation prompts (already versioned markdown)
fleet.yaml       # the fleet registry, consumed by code (not just docs)
```

### 2.2 One generic runner, config-driven

Collapse the persona scripts onto a single `alfred.fleet.run(agent_config)` driven by a fleet registry. The registry encodes the per-agent variation that today is hardcoded across separate files: `engine`, `writes`, `repos`, `schedule`, `approval_gates`. That is exactly the config a generic runner needs.

The runner owns the shared scaffold (preflight, lock, spend, engine invocation, github claim/release, event envelope, firing close, gate dispatch). Each persona contributes only three hooks:

- `pick()` - what work to claim (an issue, a PR, a bundle, nothing).
- `build_prompt(work)` - assemble the delegation prompt from versioned markdown plus dynamic payload.
- `evaluate(result)` - what counts as success and what to emit.

`bin/lucius.py` becomes a config reference plus those three hooks. A deterministic job (`gordon`) becomes a `pick`/`evaluate` pair with `engine: deterministic`.

**How far config-driven goes without over-abstracting.** The line to hold: the registry describes *what* (engine, repos, writes, gates, schedule). Python describes *how* (the three hooks). Do not push prompt logic, branching, or per-repo lookups into YAML. The moment the YAML grows conditionals or templating it has become a worse programming language. The deterministic jobs get `engine: deterministic` and skip the engine-invocation leg entirely; they still benefit from shared lock/spend/event scaffold. Config for shape, code for behavior.

### 2.3 Where the new primitives land

- **Typed sequenced event envelope (F1)** lands in `alfred/fleet/events.py`, replacing the freeform `EventLog.emit(event, **fields)`. A closed set of event dataclasses (`stage_started`, `pr_opened`, `gate_pending`, `checkpoint`, `firing_complete`, ...) each with a monotonic `seq` and a stable firing/session id. Keep the JSONL on disk; validate on emit. The desktop client (which already parses SSE and has a typed `types.ts`) switches on `body.type` and can resume a tail by `seq`. This is the single highest-leverage trust fix.
- **Fail-closed gates (F2)** land in a new `alfred/fleet/gates.py`. A `gate(question, choices, default, timeout)` primitive that pauses a firing, posts choices to Slack and desktop, and advances only on explicit answer, timeout-default, or `--auto-approve`. The `approval_gates` block in the fleet registry becomes the declarative input this primitive enforces. `automerge.py` routes through it so silence never equals approval.

Both primitives live in `fleet/` because they are properties of a firing, shared by every persona through the generic runner. That is the reuse payoff of section 2.2.

### 2.4 What NOT to refactor

Working, tested, low-churn code earns its keep. Explicitly out of scope:

- **`lib/connectors/`** - already a clean package with a plugin shape. Leave it.
- **`lib/memory/`** - clean small package. Do not restructure.
- **The `agent_runner` small single-purpose modules** (`disk.py`, `paths.py`, `process.py`, `config.py`) - they fold into `alfred/engine/` and `alfred/fleet/` by relocation, not by rewrite.
- **The AgentLock mutex** - the mkdir-atomic lock with PID-liveness and age-based stale recovery is subtle, correct, and well tested. Move it, do not touch its logic.
- **The test files** - keep them. Renames follow the package moves mechanically; the assertions do not change.
- **The desktop client** - ADR 0002 froze the surface design. The client only changes to consume the F1 typed envelope. No structural rework.
- **`slack_format.py` Block Kit builder** - good landing pad for chat cards, already clean. Keep.

The refactor is decomposition and relocation, not a rewrite. Nothing in the firing semantics, lock behavior, or gate intent changes; the code just moves into a shape where the next change is cheap.

---

## 3. Gaps register

Implementation gaps found while reading the code (not the docs):

- **No real gates primitive.** The fleet registry can list `approval_gates` but there is no `lib/gates.py` and nothing enforces them as a fail-closed choice node. They are inert documentation strings today. F2 is the fix.
- **Event envelope is untyped.** `EventLog.emit(event, **fields)` guarantees only `ts`/`agent`/`firing_id`/`event`; everything else is freeform kwargs. No `seq`, no typed body, no stable node/stage identity. The desktop timeline guesses at kwargs. F1 is the fix.
- **No state delete/cleanup path in the server.** `lib/server/views.py` has read and limited mutate routes but no DELETE/purge/prune handler. State cleanup happens only through `agent-cleanup.py` and a cleanup cron, never through the API that ADR 0002 designates as "the only state-access path."
- **Half the env-flag features are off by default.** Several feature flags (intent router, auto-promote, memory consolidate, bridge) are opt-in. Audit which the docs imply are on, and align doc and default.

---

## 4. Refactoring roadmap

Sequenced around the F1 typed-envelope work and the runner collapse. Sizes S/M/L.

### Phase 1 - Fix correctness bugs first (S each, do before refactor)
Fix any known correctness bugs in code that is about to move (the firing-close path, engine-adapter fallbacks, compose draft handling) so the fixes move cleanly later rather than being re-derived post-move.

### Phase 2 - F1 typed sequenced event envelope (M)
Implement `alfred/fleet/events.py` with the closed event set, `seq`, stable ids. Validate on emit, keep JSONL. Update the desktop client to switch on `body.type` and resume by `seq`. **This is the keystone**: the generic runner (Phase 4) emits through it, and the gates (Phase 3) emit through it. **Risk:** medium; touches every runner's emit call. Mitigated by doing it before the runner collapse so it lands once.

### Phase 3 - F2 fail-closed gates + pre-flight token budget (M)
New `alfred/fleet/gates.py` `gate(question, choices, default, timeout)` primitive; route `automerge.py` through it; make the registry `approval_gates` the declarative input. Add a pre-flight token-budget check before invoke (abort/warn per agent) in the same lifecycle leg the gates use, which directly addresses cost-cap drift. **Risk:** medium; gates change merge behavior. Default to fail-closed; `--auto-approve` is the explicit override.

### Phase 4 - Generic config-driven runner (L)
Collapse the persona scripts onto `alfred.fleet.run(config)` per section 2.2. Make the fleet registry the consumed source and add a registry/schedule parity CI lint. Each persona reduces to `pick`/`build_prompt`/`evaluate`. **Risk:** highest in the roadmap. Mitigated by Phases 2-3 having already centralized events, gates, and spend, so the runner collapse is mostly deletion of duplicated scaffold, not new logic. Convert one persona end-to-end (lucius), prove it against its tests, then fan out.

### Phase 5 - Package consolidation (M)
Promote `lib/` flat modules into the `alfred/` subpackages (section 2.1); split `slack_listener.py`, `slack_control.py`, `slack_intent.py`, and `server/views.py` along their existing internal seams. Drop the `sys.path.insert` hack. **Risk:** medium-mechanical; the tests catch regressions, renames are syntactic.

---

## 5. Recommended target organization (summary)

- **One `alfred` package** with `engine/ fleet/ slack/ server/ memory/ brain/ github/ connectors/` subpackages; `bin/` holds thin entrypoints only; the fleet registry is consumed by code, not just docs.
- **One generic runner** driven by the registry; each persona supplies `pick`/`build_prompt`/`evaluate` and nothing else. Config describes shape, Python describes behavior; YAML never grows conditionals.
- **Two new load-bearing primitives** in `fleet/`: the F1 typed sequenced event envelope and a real fail-closed `gates.py`. Both shared by every persona through the runner.
- **Do not touch**: connectors, memory package internals, the AgentLock, the tests' assertions, the desktop surface design, `slack_format.py`. Relocate, do not rewrite.

## 6. Top roadmap items

1. **Land the F1 typed sequenced event envelope** in `alfred/fleet/events.py` (Phase 2) - the keystone every later phase emits through.
2. **Ship fail-closed `gates.py` plus a pre-flight token budget** (Phase 3) - real approval gates and the cure for cost-cap drift.
3. **Collapse the persona runners onto one config-driven runner** fed by the fleet registry (Phase 4) - the largest duplication-removal win in the codebase.
4. **Consolidate the package layout** and split the remaining monoliths (Phase 5).
