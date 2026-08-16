# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Alfred is for solo builders and small engineering teams that use local coding
agents. They need to assign work, inspect progress, approve risky steps, and
recover from failures without watching each agent session.

## Product Purpose

Alfred runs a supervised fleet of coding agents on one computer. It turns an
approved request or specification into isolated engineering work. It schedules
short-lived roles, gives each run a separate git worktree, records evidence, and
keeps the operator in control of approvals and merges.

Success means that a builder can install Alfred, start a real task, understand
what each agent is doing, and review the resulting pull request. The product
must make blocked, failed, and waiting states as clear as successful states.

## Positioning

Alfred coordinates subscription-backed coding tools through the host operating
system. Its product boundary combines role-specific scheduling, worktree
isolation, approval gates, run evidence, local memory, and support for more than
one coding tool. These controls apply across the full workflow instead of one
interactive coding session.

## Operating Context

Alfred runs on a developer workstation. The operating system starts scheduled
jobs through launchd on macOS or systemd on Linux. Agents use local command-line
tools such as Claude Code and Codex. GitHub issues and pull requests hold work
state and review evidence. Operators use the Alfred CLI, the optional desktop
app, the local JSON API, or Slack to inspect and steer the fleet.

The normal workflow starts with a request or specification. Planning and
approval can precede implementation. Each run uses a lock, preflight checks,
spend limits, and an isolated worktree. A human remains responsible for product
decisions, review, and merge policy.

## Capabilities and Constraints

- Alfred is a local, single-operator product. It is not a hosted or multi-tenant
  service.
- The operating system owns scheduling. Alfred does not depend on a permanent
  orchestration process.
- Alfred invokes installed coding tools. It does not provide a model gateway.
- Claude Code and Codex are the validated dispatch engines. Other detected tools
  must pass the same capability, permission, and isolation checks before Alfred
  can dispatch work to them.
- Agent roles are runtime identities. Display themes can rename those roles but
  do not change permissions or routing.
- Approval gates, repository policy, and review rules must fail closed.
- Public examples and documentation must not contain secrets, private machine
  details, customer data, or unsupported product claims.

## Brand Commitments

The product name is Alfred. The voice is direct, calm, and technically precise.
Use familiar engineering terms when they add meaning. Avoid fictional claims,
agent theatrics, and language that suggests unsupervised authority.

Alfred can feel capable and distinctive without hiding its controls. Themes are
optional presentation. They are not the product identity.

## Evidence on Hand

- The CLI and runtime implementation are under `bin/` and `lib/`.
- The desktop app is under `clients/desktop/`.
- The public site is under `site/`.
- Runtime, isolation, policy, memory, and client behavior have automated tests
  under `tests/` and `clients/desktop/src/`.
- The repository includes screenshots and an offline memory benchmark. Any
  public performance claim must name its method and limits.
- There are no confirmed customer testimonials, adoption figures, or external
  performance comparisons. Do not fabricate them.

## Product Principles

1. Keep the operator in control. Show what will happen and what needs approval.
2. Make state inspectable. Preserve useful evidence for runs, failures, and
   handoffs.
3. Use isolation and deterministic checks as product features.
4. Support multiple coding tools through one clear safety contract.
5. Prefer a reliable end-to-end workflow over a longer list of agent roles.

## Accessibility & Inclusion

The desktop app and site must support keyboard use, visible focus, readable
contrast, reduced motion, and zoom. Status must not depend on color alone.
