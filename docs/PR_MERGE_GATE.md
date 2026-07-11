# Pull request merge gate

Use Alfred's operator gate for reviewed pull requests instead of calling
`gh pr merge` directly:

```bash
alfred pr check 491 --repo luminik-io/alfred
alfred pr merge 491 --repo luminik-io/alfred
```

Both commands fail closed unless the pull request is open and mergeable clean,
every reported CI check is complete and green, every review thread is resolved,
and Greptile 5/5 plus Codex have reviewed the exact current HEAD. Greptile's
summary is selected by its latest `updated_at`, because the bot edits an older
comment in place.

`merge` collects the complete snapshot twice and refuses to continue if it
changes. The final GitHub merge request is squash-only and includes the expected
HEAD SHA, so a force-push cannot merge different code.

For a repository where one reviewer is not installed, make that exception
explicit with `--skip-greptile` or `--skip-codex`. Alfred's own repository uses
neither exception.

Add `--json` for automation. A blocked gate exits nonzero and includes the exact
reason; it never resolves review threads or changes the pull request.
