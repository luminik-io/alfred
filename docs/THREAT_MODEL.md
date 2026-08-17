# Threat model

Alfred runs coding CLIs with your local user permissions. It does not add
an operating-system sandbox. This document describes the controls Alfred adds
and the controls the operator must provide.

Report exploitable vulnerabilities through the private process in
[`SECURITY.md`](../SECURITY.md).

## Trust boundary

One firing is a short-lived process for one task. Alfred checks the run policy,
invokes the configured local tools, and records events. Roles that change or
review code select a configured repository and use a separate git worktree.
Operational roles can run without a worktree.

Alfred assumes that the host, local user account, configured coding CLIs, and
installed project tools are trusted. A coding CLI or build command has the same
filesystem and network access as that user account.

For a stronger boundary, run Alfred under a dedicated operating-system user,
virtual machine, or container with only the required repositories and
credentials mounted.

## Controls Alfred provides

| Control | Purpose |
|---|---|
| Explicit repository configuration | Limits the repositories Alfred schedules and indexes |
| Worktree for code-changing and review roles | Separates concurrent git changes from the main checkout |
| Short-lived scheduled process | Gives each firing a defined start and end |
| Lock and recovery state | Prevents duplicate claims and supports cleanup after interruption |
| Preflight checks | Verifies auth, disk, policy, and engine readiness before model work |
| Bounded retry and self-halt | Stops repeated failures from becoming an open-ended loop |
| Approval labels and operator holds | Prevents configured work from entering the autonomous queue before approval |
| Pull-request workflow | Keeps changes reviewable before the repository's merge policy runs |
| Code-memory scope and cache isolation | Prevents blank-scope discovery and separates indexes by exact resolved scope |
| Pinned code-memory binary | Prevents an ambient `PATH` executable from entering the default toolchain |

These controls are scheduling and workflow boundaries. They do not prevent a
coding CLI or project command from reading another file that the operating-system
user can read.

## Repository scope

Alfred schedules and indexes only configured repositories. Code-memory serving
also requires at least one resolved repository and stores each exact scope in a
separate cache. Old scope caches remain on disk until the operator removes them.

Repository scope is not a filesystem access-control list. Use operating-system
permissions or isolation when the host contains repositories or files that an
agent must not read.

## Network use

Alfred itself can contact:

- the selected model provider through Claude Code, Codex, or OpenCode;
- GitHub;
- Slack, when configured;
- Alfred's aggregate telemetry endpoint, when enabled;
- package and download endpoints used by installation or optional batteries.

Coding CLIs, skills, MCP servers, and project commands can add other network
destinations. Alfred does not enforce an outbound network allowlist. Use host,
container, or network policy controls when egress must be restricted.

Disable Alfred telemetry with:

```sh
alfred telemetry off
alfred telemetry status
```

The telemetry payload excludes code, prompts, paths, repository names, branch
names, and hostnames. See [`TELEMETRY.md`](TELEMETRY.md) for the payload and
endpoint contract.

## Credentials and merge authority

Alfred uses the credentials already available to the local CLIs and `gh`.
Grant only the scopes required for the configured repositories and services.
For cloud tasks, use a dedicated least-privilege identity rather than an
administrator session.

Alfred does not merge its own pull requests by default. A repository can still
have automerge or another policy that merges qualifying changes. Review that
policy separately from Alfred.

## Untrusted inputs

Treat these values as untrusted data:

- issue, pull-request, review, and CI text from GitHub;
- Slack message bodies and thread replies;
- repository files;
- tool and command output;
- recalled lessons and external MCP results.

Control commands validate identifiers and permitted actions before they change
Alfred state. A Slack reply or GitHub comment is not merge approval unless an
explicit configured policy says it is.

## Out of scope

- Vulnerabilities in Claude Code, Codex, OpenCode, or another coding CLI.
- Third-party skills, MCP servers, and binaries installed by the operator.
- Secrets exposed through host or project configuration.
- Filesystem or network isolation beyond the local user's permissions.
- A malicious operator or compromised host.

## Verification

Run Alfred in a test account or isolated host and inspect:

- the configured repository list;
- generated worktree paths;
- `alfred doctor` and engine readiness output;
- process and network activity during a firing;
- GitHub and Slack token scopes;
- optional battery binaries and checksums;
- telemetry state and payloads.

Open a public audit issue for a safe-to-disclose mismatch. Use the private
security process for an exploitable vulnerability.
