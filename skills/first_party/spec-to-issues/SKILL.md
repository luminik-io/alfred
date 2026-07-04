---
name: spec-to-issues
description: Turns an approved spec or roadmap item into scoped, single-repo agent:implement issues with testable acceptance criteria and an explicit out-of-scope block. Use when asked to "break down this spec", "turn this into issues", "scope this roadmap item", "create implementation issues", or "plan the work for this feature". Use when a spec is approved and needs to become the tickets a fleet can pick up.
license: MIT
---

# Spec to issues

## When to use

- A spec or roadmap item is APPROVED and someone asks to turn it into work.
- You are handed a design doc and told to "make issues out of this".
- A feature touches more than one repo and needs to be split so each issue is
  single-repo and independently mergeable.
- Before any implementation firing runs: a good issue is what makes an
  autonomous run land cleanly instead of sprawling.

Do NOT use this to invent scope. If the spec is a draft or the goal is unclear,
stop and say so. Issues derived from a fuzzy spec produce fuzzy PRs.

## Procedure

1. Read the spec. Confirm it has the minimal shape (goal, current behavior,
   target behavior, acceptance criteria, out-of-scope). If it does not, see
   `references/spec-shape.md` and ask for the missing pieces before scoping.
2. Identify the repos the spec touches. One issue per repo. If a single repo's
   work is large, split it along a seam a reviewer can verify independently
   (an endpoint, a screen, a migration), never along "part 1 / part 2" of the
   same file.
3. Order the issues by dependency. If `your-backend` must ship an endpoint
   before `your-frontend` can call it, say so in each issue's first line
   ("Depends on: your-backend #NN") so the fleet does not start the dependent
   work early.
4. For each issue write:
   - A one-line title in the form `feat(<area>): <verb> <thing>`.
   - A short context paragraph: what the spec wants and why this slice exists.
   - Acceptance criteria as a checklist, each verifiable by a named command,
     endpoint, screen, or file. "Works correctly" is not a criterion; "GET
     /v1/widgets returns 200 with a paginated list" is.
   - A test line: what the PR must test (derive from the criteria).
   - An **Out of scope** block naming what this issue must NOT change. This is
     what keeps an autonomous run from wandering into unrelated code.
5. Label each issue `agent:implement` and set the single repo. Do not create a
   multi-repo issue; the fleet fires one repo per run.
6. Re-read the set as a whole: every acceptance criterion in the spec must be
   covered by exactly one issue, and no issue should carry criteria the spec
   never asked for.

## Output

A list of ready-to-file issues. For each: title, repo, context, acceptance
criteria checklist, test line, and an out-of-scope block. End with a coverage
line mapping each spec acceptance criterion to the issue that owns it, so a
reviewer can confirm nothing was dropped or invented. Never auto-file the
issues; present them for approval first.
