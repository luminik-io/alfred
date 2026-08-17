# OpenCode engine

Alfred can run a role through OpenCode 1.18.18 or newer. OpenCode is an
explicit engine choice. The default `hybrid` route remains Claude Code first,
with Codex fallback only for a classified capability gap.

## Install and sign in

Install OpenCode with one of its supported methods:

```sh
brew install anomalyco/tap/opencode
# or
npm install -g opencode-ai
```

Connect at least one provider, then check the stored login:

```sh
opencode auth login
opencode auth list
alfred engine doctor
```

Alfred's readiness check requires a provider stored by `opencode auth login`.
An environment-only provider key does not make the engine ready. This keeps
scheduled runs tied to an account that the host can verify before dispatch.

If the scheduler cannot find the binary, add its absolute path to
`$ALFRED_HOME/.env`:

```sh
OPENCODE_BIN=$HOME/.opencode/bin/opencode
```

## Select OpenCode for a role

```sh
alfred engine set senior-dev opencode
alfred engine status senior-dev
alfred model set senior-dev opencode anthropic/claude-sonnet-4-5
alfred engine doctor senior-dev
```

The model value uses OpenCode's `provider/model` form. Omit the model override
to let OpenCode use its configured default. The same engine and model controls
are available in Alfred Desktop.

Custom agents accept `--engine opencode`:

```sh
alfred agent add release-check \
  --display-name "Release Check" \
  --role-title "Release reviewer" \
  --prompt "Check the release diff and list concrete blockers." \
  --engine opencode \
  --schedule 1h \
  --repo your-org/your-repo
```

## Runtime contract

Each firing starts a new non-interactive OpenCode session with:

- `--pure`, which disables external plugins
- `--format json`, which emits newline-delimited events
- `--dir <worktree>`, which fixes the repository scope
- a temporary config directory removed after the process exits
- automatic sharing disabled
- automatic updates and LSP downloads disabled for the firing
- an explicit Alfred agent and permission map
- project subagents, skills, and MCP tools denied for the firing

The prompt goes through standard input. Alfred parses only completed text,
usage, session ID, and error fields. Raw output and stderr stay in
`$ALFRED_HOME/state/opencode/<role>/` for local diagnosis. They are not copied
into the result payload.

Alfred rejects malformed events, missing final text, tool errors, permission
errors, authentication failures, usage limits, and timeouts. A timeout stops
and reaps the complete process group.

## Opt-in live smoke test

The live test sends one read-only provider request. It is skipped unless you
enable it:

```sh
ALFRED_OPENCODE_LIVE_SMOKE=1 \
  uv run --frozen --extra dev pytest tests/unit/agent_runner/test_opencode_live.py -q
```

Run it only with the provider account and model that you intend Alfred to use.
The normal test suite never sends this request.

## Permissions

OpenCode runs with the local user's operating-system and network permissions.
The adapter is a workflow boundary, not a host sandbox.

For read-only roles, Alfred denies edits and shell commands. For roles that
already have an approved write path, Alfred allows edits and shell commands in
the worktree. Its command rules deny direct `git push`, `gh pr merge`, and
checkout or switch to `main`. Unexpected permission requests are rejected
because Alfred does not pass `--auto`.

External paths, interactive questions, plan-mode transitions, project
subagents, skills, and MCP tools are denied in both modes. System-managed
OpenCode policy has final authority. Use a dedicated user, virtual machine, or
container when the agent must not reach other files or network destinations.

## Failure states

| State | Meaning | Action |
|---|---|---|
| `missing` | Alfred cannot find `opencode`. | Install it or set `OPENCODE_BIN`. |
| `incompatible` | The CLI is older than 1.18.18 or lacks the required flags. | Upgrade OpenCode. |
| `auth_required` | `opencode auth list` has no stored provider. | Run `opencode auth login`. |
| `probe_failed` | A bounded readiness command failed. | Run `alfred engine doctor` and inspect the local CLI. |
| `error_permission` | A tool crossed the configured firing boundary. | Fix the role or command. Do not bypass the check. |
| `error_cancelled` | OpenCode or the host cancelled the firing. | Inspect the local event and stderr files before retrying. |

## Scope limits

- OpenCode is not added to the default hybrid fallback.
- Alfred does not import user OpenCode plugins into scheduled runs.
- Alfred does not manage provider keys or provider billing.
- OpenCode sessions are new per firing. Cross-engine session import belongs to
  the separate session and evidence work.

See [engine routing](ENGINE_ROUTING.md), [security](THREAT_MODEL.md), and the
[OpenCode CLI documentation](https://opencode.ai/docs/cli/).
