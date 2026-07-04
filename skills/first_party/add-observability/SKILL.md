---
name: add-observability
description: Adds structured logging at decision points, one metric per new outcome, and a trace span per new I/O boundary, all behind config-driven levels. Use when asked to "add logging", "make this observable", "add metrics", "instrument this", "add a trace span", or when shipping a feature or path that would be a black box in production. Use when a change adds a new outcome, branch, or external call that operators will need to see.
license: MIT
---

# Add observability

## When to use

- A feature adds a new success or failure outcome an operator needs to see.
- A change adds an external call (HTTP, queue, DB) that can be slow or fail.
- A branch or decision point exists where "why did it do that?" will be asked
  in production.
- A path is currently a black box: it either works or it does not, with nothing
  in between to diagnose.

Instrument the decision points and boundaries, not every line. Noise is as bad
as silence: a log on every loop iteration drowns the one line that matters.

## Procedure

1. **Structured logging at decision points.** At each branch that changes the
   outcome, log one structured event (key-value fields, not an interpolated
   sentence) with enough context to reconstruct the decision: the inputs that
   mattered, the branch taken, the result. Use the repo's existing logger and
   field conventions. Never log secrets, tokens, or full request bodies.
2. **One metric per new outcome.** For each new outcome the code can produce,
   emit a counter (for example `widget_export_total{result="ok|error"}`). If the
   path has latency that matters, add a histogram or timer. One metric per
   outcome, named in the repo's existing metric convention, so a dashboard can
   show the rate and the error share.
3. **A trace span per new I/O.** Wrap each new external call in a span named for
   the operation (`db.widgets.query`, `http.acme-org.enrich`). Record the
   status and, on failure, the error class as a span attribute, so a trace shows
   where time went and which hop failed.
4. **Config-driven levels.** Do not hardcode verbosity. Read the log level and
   whether metrics or tracing are on from config or environment (mirroring how
   the repo already configures other tunables), so an operator can turn detail
   up in an incident and down in steady state without a redeploy.
5. Verify: run the path once and confirm the log event, the metric increment,
   and the span all appear, and that flipping the configured level actually
   changes what is emitted.

## Output

- The instrumented code: log events at each decision point, a metric per
  outcome, a span per new I/O, all reading level and toggles from config.
- A short note listing what was added: which events, which metric names, which
  spans, and the config keys that control them, so an operator knows what to
  watch and what knob to turn.
