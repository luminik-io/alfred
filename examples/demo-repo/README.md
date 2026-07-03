# textkit

A tiny, dependency-free string utility library. This is the sample project
that `alfred demo` works on: a small real codebase with tests, one obvious
missing feature, and one subtle planted bug for the review pass to catch.

It is intentionally small so the whole plan, build, review, fix, and ship
loop finishes in one short run.

## What is here

- `textkit.py` - the library (`titlecase`, `word_count`, `truncate`).
- `test_textkit.py` - the existing test suite.

## The gap

There is no `slugify` helper yet, even though a URL-safe slug is the most
common thing callers reach for. That is the feature the demo plans and builds.

## Running the tests

```sh
python -m pytest test_textkit.py -q
```
