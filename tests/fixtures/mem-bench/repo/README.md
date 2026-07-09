# mem-bench sample repo

A tiny, deterministic repo the memory A/B benchmark points an engine at. It
exists so a real `alfred benchmark memory --engine claude` run has actual files
to edit; the offline harness never edits it.

Conventions the fleet has already learned about this repo (seeded as lessons in
`../lessons.json`, one per known mistake the paired tasks re-tempt):

- Use timezone-aware datetimes (`datetime.now(UTC)`), never naive.
- Never swallow an exception with a bare `except: pass`; log and re-raise.
- No mutable default arguments; default to `None` and create inside.
- Batch database lookups into a single `IN` query, never N+1.
