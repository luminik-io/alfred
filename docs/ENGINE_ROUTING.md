# Engine routing

How Alfred decides whether a codename's next firing runs through Claude Code, Codex, or a Claude-first hybrid with Codex fallback.

Alfred is the scheduler and guardrail layer. The actual LLM work is done by the engine: a local CLI you have already authenticated. The framework owns the per-codename decision of which engine that is. Default posture is local subscription auth; Alfred does not need Anthropic or OpenAI API keys for the normal Claude Code or Codex CLI flow.

This page covers the three modes, the precedence chain, the fallback behavior, the default routing matrix for the shipped fleet, and where the multi-engine roadmap is going.

## Readiness is a verified contract

Finding an executable on `PATH` is not enough. Alfred calls an engine ready only
after bounded local probes verify the CLI version, the non-interactive flags the
runtime depends on, and a signed-in account. Probe output is discarded so account
details cannot leak into setup status or logs. A changed or expired CLI fails
closed and onboarding tells the operator what needs attention.

The desktop app may detect additional harnesses under **Advanced: engine probe**.
OpenCode and Cline are visible there as candidates, but Alfred does not dispatch
autonomous work to either one yet. Each must pass a hermetic temporary-worktree
mutation, cancellation, permissions, and structured-output suite before its
descriptor can become dispatchable. Detection is not advertised as support.

## Three modes

| Mode | Behavior |
|---|---|
| `claude` | Use Claude Code only. No fallback. |
| `codex` | Use Codex only. No fallback. |
| `hybrid` | Use Claude Code first. Retry the same engine on transient faults; fall back to Codex only on a capability gap (engine ran, produced nothing useful). Default for most codenames. |

`hybrid` is the default for builder agents because it gives them a second shot when Claude ran but produced no usable result, without hiding quota, auth, or transport faults behind another provider. Reviewer agents that are happy with either engine often run pure `codex` so they preserve Claude quota for builders.

## Per-agent overrides

The framework reads the engine for each firing from a precedence chain. The first source that returns a normalized mode wins.

1. `ALFRED_<CODENAME>_ENGINE`, where the codename is the role slug (e.g. `ALFRED_SENIOR_DEV_ENGINE=claude`, `ALFRED_REVIEWER_ENGINE=codex`).
2. `ALFRED_ENGINE` for fleet-wide testing (useful in `alfred-dry-run`).
3. `$ALFRED_HOME/state/engines/<codename>`, written by `alfred engine set`.
4. The codename's compiled-in default, usually `hybrid`.

Alfred CLI:

```sh
alfred engine status                 # one line per codename, resolved mode
alfred engine status senior-dev          # one codename, plus where the value came from
alfred engine set senior-dev hybrid      # persist to $ALFRED_HOME/state/engines/senior-dev
alfred engine set reviewer codex
alfred codex status                  # check the Codex CLI is reachable
alfred codex probe                   # run one tiny non-interactive request
alfred auth status                   # auth-surface check across both engines
```

Set the env-var form in `$ALFRED_HOME/.env` when you want the override to follow the operator's shell. Set the state-file form when you want the override to follow the host scheduler (it survives a `deploy.sh` re-render).

## Per-agent model overrides

Engine mode and model choice are separate. An agent can stay on `hybrid` while
using one Claude model for the first attempt and one Codex model for a fallback.
When no model override exists, Alfred omits `--model` and lets the authenticated
provider CLI choose its current default.

```sh
alfred model status
alfred model status senior-dev
alfred model set senior-dev claude opus
alfred model set senior-dev codex gpt-5-codex
alfred model clear senior-dev claude
```

The same choices are available under **Agents** in Alfred Desktop. Open an
agent, enter a Claude or Codex alias, and save it. The drawer shows when a
per-agent or fleet environment variable is taking precedence over the saved
choice.

Model names are intentionally not hard-coded into Alfred. Claude Code and Codex
change their available model aliases independently, so the provider CLI remains
the source of truth. Alfred validates the alias as one safe command argument and
passes it only to the matching provider.

Resolution is per provider. The first configured value wins:

1. `ALFRED_<ROLE>_<ENGINE>_MODEL`, such as `ALFRED_REVIEWER_CODEX_MODEL`.
2. `ALFRED_<ENGINE>_MODEL`, such as `ALFRED_CLAUDE_MODEL`, for a fleet-wide provider default.
3. `$ALFRED_HOME/state/models/<role>/<engine>`, written atomically by `alfred model set`.
4. The provider CLI default.

`alfred model clear` removes only the named provider. Claude and Codex use separate
state files, so simultaneous updates cannot overwrite each other.

## Hybrid fallback behavior

Hybrid mode tries Claude first. Every invocation outcome is run through one classifier (`classify_result`) that maps it to one of three failure classes, and the class decides what happens next:

- **TRANSIENT** (`error_rate_limit`, `error_overloaded`, `error_timeout`, `error_api`, connection resets, context-overflow): a temporary provider or transport fault the same engine is likely to clear. The runner retries the SAME engine with exponential backoff and full jitter, honouring any server `Retry-After` hint (it waits `max(Retry-After, backoff)`). It does NOT fall back. A single transient 429 on the fallback engine no longer kills a task that would have succeeded on retry.
- **FATAL** (`error_authentication`, `error_budget`, 401/403/422): a problem retrying cannot fix. The runner surfaces it honestly and never burns the fallback. For auth, the credentials remedy is the one the scheduled-firing preflight already names; falling back to Codex would only hide it.
- **CAPABILITY** (`error_max_turns`, `parse-failed`, `error_loop_detected`, or any failure we cannot place): the engine ran and returned cleanly but produced nothing useful. This is the only class that triggers the Claude->Codex fallback, because a different engine may have the capability this one lacked.

The core rule: **the fallback fires only on a capability gap, not on a transient blip.** This is the single biggest reliability change from earlier versions, where any rate-limit or auth subtype dropped straight to Codex.

When the codex result carries a fallback, it is stamped with `fallback_from_subtype` so event logs can show the Claude capability gap that triggered the Codex attempt without rewriting the Codex result subtype.

### Per-engine circuit breaker

Each engine has its own breaker, backed by `$ALFRED_HOME/state/_breaker/<engine>.json`. After `ALFRED_BREAKER_THRESHOLD` consecutive transient failures on an engine, the breaker trips and pauses calls to THAT engine for `ALFRED_BREAKER_COOLDOWN_SECONDS`. Because the state file is shared by every worker on the host, this auto-throttles the shared provider quota instead of needing a human to scale workers down: parallel workers can no longer lockstep-retry into a deeper rate-limit. A clean call resets the streak; the first call after the cooldown is allowed through (half-open).

### Loop-fingerprint detection

While a Claude firing streams, each tool step is fingerprinted as a stable hash of `(tool/action, result-preview)`. `ALFRED_LOOP_WINDOW` identical fingerprints in a row means the agent is spinning on a no-progress action: the runner kills the subprocess and returns `error_loop_detected` (a capability gap) rather than letting it burn to the wall-clock timeout. A hard per-task step ceiling (`ALFRED_MAX_STEPS`) catches a task that never repeats but also never finishes. Disable the whole guard with `ALFRED_LOOP_DETECT=0`.

When a Claude-backed firing returns `error_rate_limit` or `error_budget`, the runner also calls `set_global_block(hours=1, reason=...)`. That writes `$ALFRED_HOME/state/global-blocked-until.json`, which every other Claude-backed firing reads at the top of `main()`. They print `[<AGENT>-GLOBAL-BLOCKED]` and exit 0 for the next hour. The block stops the stampede; without it, the whole fleet would spend the hour firing into the same rate-limit wall.

All shipped agents check the global block before dispatch today, regardless of engine mode. The block is a fleet-wide pause, not a Claude-only router bypass.

### Reliability tunables

All four pieces are config-driven with env-overridable defaults, so a launchd plist or deployment config can retune behaviour without a redeploy. Defaults are clamped, so a typo cannot unbound a budget.

| Env var | Default | What it controls |
|---|---|---|
| `ALFRED_TRANSIENT_MAX_RETRIES` | `3` | Extra same-engine retries on a TRANSIENT failure (`0` disables retry). |
| `ALFRED_RETRY_BASE_SECONDS` | `2` | Base of the exponential backoff window. |
| `ALFRED_RETRY_CAP_SECONDS` | `60` | Max backoff window (before jitter). |
| `ALFRED_RETRY_AFTER_MAX_SECONDS` | `300` | Ceiling applied to a server `Retry-After` hint. |
| `ALFRED_BREAKER_THRESHOLD` | `5` | Consecutive transient failures before an engine breaker trips. |
| `ALFRED_BREAKER_COOLDOWN_SECONDS` | `300` | How long a tripped engine breaker stays open. |
| `ALFRED_LOOP_DETECT` | on | Set to `0`/`false` to disable loop-fingerprint detection. |
| `ALFRED_LOOP_WINDOW` | `3` | Identical step fingerprints in a row that count as a loop. |
| `ALFRED_MAX_STEPS` | `200` | Hard per-task step ceiling. |

## Default routing matrix

The shipped fleet has the following defaults. Override per codename when your account economics or quality posture call for it.

| Codename | Default mode | Why |
|---|---|---|
| **architect** | `hybrid` | Architect for cross-repo execution. Long-context planning prefers Claude; Codex fallback gives the architect lane a second model when Claude produced no useful plan. |
| **senior-dev** | `hybrid` | Builder. Wants Claude for first-class code generation, with Codex available only for capability gaps. |
| **planner** | `claude` | Planner. Cross-repo grep plus issue-filing benefits from Claude's longer effective context and tool integration. |
| **test-engineer** | `hybrid` | Test-coverage builder. Same posture as senior-dev; tests are valuable enough to fall back rather than skip. |
| **reviewer** | `codex` | Reviewer. An independent reviewer on a different model surfaces blind spots the builder model shares. Also preserves Claude quota for builders. |
| **fixer** | `hybrid` | Review-fix builder. Needs Claude for the same reasons as senior-dev. |
| **triage** | `hybrid` | Bug triage. Light-touch; either engine works. |
| **e2e-runner** | `claude` | Post-deploy smoke. Lower volume; Claude is fine. |
| **ops-watch** | `claude` | Deploy-health. Read-only; quiet on healthy days. |
| **automerge** | n/a | No engine call. |
| **agent-cleanup** | n/a | No engine call. |

These are starting points, not laws. If you have a Claude Max plan and abundant quota, push more codenames to pure `claude`. If you have OpenAI credits to burn and want a second opinion on every PR, push more reviewers to pure `codex`. The override surface is per-codename for exactly this reason.

## Subscription economics

Alfred's default posture is to use the local CLI subscription auth you have already paid for. It does not need API keys for normal operation.

- Claude Code with a Pro or Max plan: keep `ANTHROPIC_API_KEY` unset. Claude Code gives env-var API keys priority over subscription auth, which silently moves a firing onto API billing.
- Codex with a ChatGPT plan: sign in through the Codex CLI with your ChatGPT account. Keep `OPENAI_API_KEY` unset. Alfred never treats a generic SDK key as proof that the Codex CLI can run.
- AWS: only used when an agent needs Secrets Manager, and only with per-agent IAM (see [AWS setup](./AWS_SETUP.md)).

The shipped fleet is designed to run on subscriptions you already have. No double billing. An API-billed Codex CLI must be authenticated through Codex's own login flow so `codex login status` can verify it; placing a key in Alfred's environment is not an authentication contract.

## Multi-engine contract

Claude Code and Codex are dispatchable today. The registry also knows how to
identify OpenCode and Cline without pretending they are ready. `AgentResult`
carries `success`, `subtype`, `num_turns`, `cost_usd`, `session_id`, and
`result_text` regardless of which engine produced it.

A new engine needs all of the following before it can join a fleet:

1. A stable, deterministic, non-interactive command with structured output.
2. Explicit repository read and worktree write boundaries.
3. A bounded cancellation contract and a reliable process exit code.
4. Auth and model-selection probes that do not expose credentials.
5. Hermetic mutation tests plus one opt-in live smoke test.
6. Failure mappings for retry, breaker, and fallback classification.

This registry is the extension point for OpenCode, Cline, Gemini CLI, and local
model harnesses. Alfred enables them by proven capabilities, not by brand name.

## See also

- [Architecture](./../ARCHITECTURE.md): why the engine is a fresh subprocess per firing.
- [Agent lifecycle](./ARCHITECTURE.md#agent-lifecycle): the firing trace including the engine call.
- [Claude Code](./CLAUDE_CODE.md): install, auth, Pro vs Max sizing, account swap.
- [Codex provider](./CODEX_PROVIDER.md): the Codex runtime contract and write-boundary posture.
- [State and memory](./STATE_AND_MEMORY.md): the `engines/<codename>` state file.
- [Install](./../INSTALL.md): first-run install flow.
