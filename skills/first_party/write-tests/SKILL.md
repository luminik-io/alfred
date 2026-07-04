---
name: write-tests
description: Derives tests from acceptance criteria, writes a failing regression test before fixing a bug, runs the suite, and reports coverage of the specific behavior. Use when asked to "write tests", "add test coverage", "test this change", "write a regression test", or when implementing a feature or fixing a bug that ships without tests. Use before committing any behavior change.
license: MIT
---

# Write tests

## When to use

- You implemented a feature and the acceptance criteria are not yet covered.
- You are about to fix a bug (write the failing test FIRST).
- A PR changes behavior but adds no test.
- A reviewer asks "how do we know this works?".

This skill is about tests that verify the SPECIFIC behavior that changed, not
about chasing a line-coverage number. A test that passes whether or not the
code is correct is worse than no test.

## Procedure

1. Restate the behavior under test in one sentence. If it came from a spec,
   quote the acceptance criterion. If it is a bug, state the wrong behavior and
   the correct behavior.
2. For a bug: write the regression test FIRST and run it. It must FAIL against
   the current code, reproducing the bug. A test that passes before the fix
   proves nothing. Only then write the fix and confirm the test flips to green.
3. For a feature: turn each acceptance criterion into at least one test. Cover
   the happy path, one boundary (empty, null, max), and one error path. Name
   each test after the behavior, e.g. `test_widget_search_returns_empty_on_no_match`.
4. Use the repo's existing test framework and fixtures. Read a neighboring test
   first and match its style; do not introduce a new runner or assertion library.
5. Run the full suite (for example `npm test`, `pytest`, or `./gradlew test`
   depending on the repo), not just the new file, so you catch a regression the
   change caused elsewhere.
6. If a test is flaky or environment-dependent, fix the test's determinism
   (seed, clock, fixed input) rather than adding a retry or a sleep.

## Output

- The new or changed test files, each test named after the behavior it pins.
- For a bug: a note confirming the regression test failed before the fix and
  passes after.
- The suite result (pass count, any skips and why).
- A short coverage statement in prose: which acceptance criterion or bug each
  test covers. Do not report a coverage percentage as if it were proof; report
  which behaviors are now pinned. Example: "GET /v1/widgets pagination is
  covered by 3 tests (page 1, last page, out-of-range page)."
