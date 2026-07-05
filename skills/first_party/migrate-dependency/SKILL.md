---
name: migrate-dependency
description: Migrates a dependency to a target version by pinning it, reading the changelog for breaking changes, updating call sites, running the tests, and writing a migration note. Use when asked to "upgrade this dependency", "bump the version of X", "migrate to X v2", "update the library", or when a dependency is out of date, deprecated, or has a security advisory. Use before merging any dependency version bump.
license: MIT
---

# Migrate dependency

## When to use

- A dependency is out of date, deprecated, or has a security advisory.
- You are asked to move from one major version of a library to the next.
- A transitive dependency needs pinning to a known-good version.

A version bump is a behavior change. Treat it like one: pin it deliberately,
find out what broke between versions, fix the call sites, and prove the tests
still pass. "Bumped and it built" is not done.

## Procedure

1. **Pin the target version.** Set the exact version you are migrating to in the
   manifest (`package.json`, `pyproject.toml`, `build.gradle`, etc.) and the
   lockfile. Never migrate to a floating range; you cannot review what you
   cannot name.
2. **Read the changelog and migration guide** for every version between the
   current and target. List the breaking changes that touch APIs this repo
   actually uses. If the jump crosses several majors, note whether an
   intermediate step is safer than one leap.
3. **Migrate the call sites.** For each breaking change, update every usage:
   renamed functions, changed signatures, removed options, new required
   arguments. Search the repo for the old API to be sure none is missed
   (`grep` the old symbol). Do not paper over a removed API with a shim unless
   the guide recommends one.
4. **Run the tests.** Run the full suite, not just the touched module. A
   dependency change can break a caller far from where you edited. If tests
   assumed the old behavior, update the test to the new correct behavior (and
   say why), do not weaken the assertion to make it pass.
5. **Write a migration note.** Record what moved and why, so the next person
   reading the diff understands the version jump.

## Output

- The pinned manifest and lockfile change.
- The updated call sites, one edit per breaking change.
- The test result (full suite green; note any test updated to match new
  behavior and why).
- A migration note: from-version to to-version, the breaking changes that
  mattered, what was changed to accommodate them, and anything deferred to a
  follow-up. Keep it in the PR body or a `CHANGELOG` entry in the repo's voice.
