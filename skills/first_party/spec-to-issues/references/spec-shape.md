# Minimal spec shape

A spec is ready to break into issues when it is short enough for an agent to
read in full and concrete enough for a reviewer to verify. Derived from the
project's spec-driven-development guide.

```md
# Feature: <name>

Status: draft | approved | shipped
Owner: <human owner>
Repos: your-backend, your-frontend, your-mobile

## Goal

What user or system behavior changes, in one or two sentences.

## Current Behavior

What the product does today. Ground the change in reality, not a wish.

## Target Behavior

What should be true after this ships.

## Acceptance Criteria

- [ ] A reviewer can verify this with <command, endpoint, screen, or file>.
- [ ] Tests cover <specific behavior>.
- [ ] The PR does not change <explicit out-of-scope area>.

## Rollout

1. <repo A first, because ...>
2. <repo B after ..., because it depends on A>

## Out Of Scope

- <thing this feature deliberately does not do>
```

## Turning the shape into issues

- **Goal + Target Behavior** become the context paragraph of each issue.
- **Acceptance Criteria** map one-to-one onto issue checklists. Split them by
  repo: a backend criterion goes in the `your-backend` issue, a UI criterion in
  the `your-frontend` issue.
- **Rollout** becomes the `Depends on:` ordering line at the top of each issue.
- **Out Of Scope** is copied (and narrowed per repo) into every issue's
  out-of-scope block so no single run wanders past its slice.

If any of Goal, Target Behavior, or Acceptance Criteria is missing, the spec is
not ready. Ask for the missing section rather than guessing; a guessed
criterion becomes a guessed PR.
