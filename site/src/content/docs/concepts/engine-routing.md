---
title: Engine routing
description: How Alfred selects Claude Code, Codex, OpenCode, or a Claude-first hybrid route for each role.
---

Alfred is the scheduler and control layer. The model work is done by an
authenticated local CLI. Claude Code and Codex can use their subscription
login. OpenCode uses a provider stored by `opencode auth login`.

This page covers the four modes, the precedence chain, fallback behavior, and
the engine contract. Full doc at [`docs/ENGINE_ROUTING.md`](https://github.com/luminik-io/alfred/blob/main/docs/ENGINE_ROUTING.md).

## Four modes

| Mode | Behavior |
|---|---|
| `claude` | Use Claude Code only. No fallback. |
| `codex` | Use Codex only. No fallback. |
| `opencode` | Use OpenCode only. No fallback. |
| `hybrid` | Use Claude Code first. Retry transient failures on the same engine, and fall back to Codex only when Claude ran but produced no useful result. Default for most codenames. |

`hybrid` is the default for builder agents because it gives them a second shot when Claude ran but produced no usable result, without hiding quota, auth, or transport faults behind another provider. Reviewer agents that are happy with either engine often run pure `codex` so they preserve Claude quota for builders.

## Per-agent overrides

The framework reads the engine for each firing from a precedence chain. The first source that returns a normalized mode wins.

1. `ALFRED_<CODENAME>_ENGINE` (e.g. `ALFRED_SENIOR_DEV_ENGINE=claude`, `ALFRED_REVIEWER_ENGINE=codex`).
2. `ALFRED_ENGINE` for fleet-wide testing (useful in `alfred-dry-run`).
3. `$ALFRED_HOME/state/engines/<codename>`, written by `alfred engine set`.
4. The codename's compiled-in default, usually `hybrid`.

Alfred CLI:

```sh
alfred engine status                 # one line per codename, resolved mode
alfred engine status senior-dev      # one codename, plus where the value came from
alfred engine set senior-dev hybrid  # persist to $ALFRED_HOME/state/engines/senior-dev
alfred engine set reviewer codex
alfred engine set release-check opencode
alfred model set release-check opencode anthropic/claude-sonnet-4-5
alfred engine doctor release-check
alfred codex status                  # check the Codex CLI is reachable
alfred codex probe                   # run one tiny non-interactive request
alfred auth status                   # auth-surface check across both engines
```

Set the env-var form in `$ALFRED_HOME/.env` when you want the override to follow your shell. Set the state-file form when you want the override to follow the host scheduler (it survives a `deploy.sh` re-render).

## Hybrid fallback behavior

Hybrid mode tries Claude first. Every invocation outcome is classified before
Alfred decides what to do next:

- **TRANSIENT** (`error_rate_limit`, `error_overloaded`, `error_timeout`,
  `error_api`, connection resets, context overflow): retry the same engine with
  exponential backoff and jitter.
- **FATAL** (`error_authentication`, `error_budget`, 401/403/422): surface the
  failure and do not use the fallback.
- **CAPABILITY** (`error_max_turns`, parse failure, loop detection, or another
  no-useful-result failure): fall back to Codex because a different engine may
  handle the task better.

The fallback only fires on a capability gap. It does not hide auth, quota, or
transport faults behind a different provider.

When a Claude-backed firing returns `error_rate_limit` or `error_budget`, the runner also calls `set_global_block(hours=1, reason=...)`. That writes `$ALFRED_HOME/state/global-blocked-until.json`, which every other Claude-backed firing reads at the top of `main()`. They print `[<AGENT>-GLOBAL-BLOCKED]` and exit 0 for the next hour. The block stops the stampede; without it, the whole fleet would spend the hour firing into the same rate-limit wall.

All shipped agents check the global block before dispatch today, regardless of engine mode. The block is a fleet-wide pause, not a Claude-only router bypass.

## Default routing matrix

The shipped fleet has the following defaults. Override per codename when your account economics or quality posture call for it.

| Codename | Default mode | Why |
|---|---|---|
| **architect** | `hybrid` | Cross-repo execution. Long-context planning prefers Claude; Codex fallback gives the architect lane a second model when Claude produced no useful plan. |
| **senior-dev** | `hybrid` | Builder. Wants Claude for first-class code generation, with Codex available only for capability gaps. |
| **planner** | `claude` | Planner. Cross-repo grep plus issue-filing benefits from Claude's longer effective context and tool integration. |
| **test-engineer** | `hybrid` | Test-coverage builder. Same posture as senior-dev; tests are valuable enough to fall back rather than skip. |
| **reviewer** | `codex` | Independent reviewer on a different model surfaces blind spots the builder model shares. Also preserves Claude quota for builders. |
| **fixer** | `hybrid` | Review-fix builder. Needs Claude for the same reasons as senior-dev. |
| **triage** | `hybrid` | Bug triage. Light-touch; either engine works. |
| **e2e-runner** | `claude` | Post-deploy smoke. Lower volume; Claude is fine. |
| **ops-watch** | `claude` | Deploy-health. Read-only; quiet on healthy days. |
| **automerge** | n/a | No engine call. |
| **agent-cleanup** | n/a | No engine call. |

These are starting points, not laws. If you have a Claude Max plan and abundant quota, push more codenames to pure `claude`. If you have OpenAI credits to burn and want a second opinion on every PR, push more reviewers to pure `codex`. The override surface is per-codename for exactly this reason.

## Provider accounts and billing

Alfred uses the account already authenticated in the selected coding CLI. It
does not proxy model traffic or add an Alfred model fee.

- Claude Code with a Pro or Max plan: keep `ANTHROPIC_API_KEY` unset. Claude Code gives env-var API keys priority over subscription auth, which silently moves a firing onto API billing.
- Codex with a ChatGPT plan: sign in through the Codex CLI with your ChatGPT account. Keep `OPENAI_API_KEY` unset. Alfred never treats a generic SDK key as proof that the Codex CLI can run.
- OpenCode: select a provider with `opencode auth login`. Provider billing and limits apply to each firing.
- AWS: only used when an agent needs Secrets Manager, and only with per-agent IAM (see [AWS setup](/guides/aws/)).

Alfred accepts Codex's own login state or its documented
`CODEX_ACCESS_TOKEN` automation context. A generic `OPENAI_API_KEY` value alone
is not an authentication contract and never makes the engine ready.

## OpenCode and the engine contract

Claude Code, Codex, and OpenCode are dispatchable today. OpenCode is explicit;
it is not part of the default hybrid route. Each OpenCode firing uses an exact
worktree, isolated config, disabled external plugins, and JSON events. Setup can
detect Cline, but detection alone does not make it dispatchable. `AgentResult`
carries `success`, `subtype`, `num_turns`, `cost_usd`, `session_id`, and
`result_text` for supported engines.

Claude Code 2.1.41 or newer is required because Alfred's readiness contract uses `claude auth status`, introduced in that release. Alfred uses the stable version command because Claude's top-level help is intentionally incomplete and cannot prove that a documented flag is absent.

A new engine needs all of the following before it can join a fleet:

1. A stable, deterministic, non-interactive command with structured output.
2. Explicit repository read and worktree write boundaries.
3. A bounded cancellation contract and a reliable process exit code.
4. Auth and model-selection probes that do not expose credentials.
5. Hermetic mutation tests plus one opt-in live smoke test.
6. Failure mappings for retry, breaker, and fallback classification.

The registry reports inventory and readiness. It is not a runtime adapter. A
new engine also needs a command builder, an output parser, cancellation
behavior, and failure mapping before Alfred can dispatch work to it.

A readiness probe failure stops the current firing. Alfred does not repeat a failed probe or fall back within that firing. The next scheduled firing can probe again.

## See also

- [Architecture](/concepts/architecture/): why the engine is a fresh subprocess per firing.
- [How it works](/concepts/how-it-works/): the firing trace including the engine call.
- [Claude Code and Codex](/guides/claude-code/): install, auth, Pro vs Max sizing, account swap.
- [OpenCode](/guides/opencode/): install, auth, permissions, models, and diagnostics.
- [State and memory](/concepts/state-and-memory/): the `engines/<codename>` state file.
- [Install](/getting-started/install/): first-run install flow.
