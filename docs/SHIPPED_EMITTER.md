# Public shipped-work evidence

Alfred has two separate evidence paths:

- The canonical site reads public GitHub data for `luminik-io/alfred`.
- Operators can generate a scrubbed feed from their own local Alfred state.

The canonical site never reads an operator's repository list or local shipped
state. Refresh its public repository dataset with:

```sh
cd site
npm run proof:update
```

This command updates `site/src/data/impact-proof.json`. Exact provenance labels
qualify a merged PR. A role-like branch name does not qualify it, and
Dependabot is excluded.

## Operator feed

`bin/alfred-shipped-public.py` reads
`$ALFRED_HOME/state/shipped/prs.json`, applies a field allowlist and redaction
rules, and writes a versioned JSON feed. Use it only when you have reviewed the
configured repository scope and the generated output.

The upstream Alfred site does not ingest or publish this feed.

### Allowed fields

The emitter can copy only these pull-request fields:

```text
repo, number, title, codename, merged_at,
lines_added, lines_removed, files_changed,
reviewed_by, url
```

It drops diffs, issue bodies, author emails, comments, labels, prompts, and all
unknown fields. Adding a field requires a code change.

### Repository scope

A record passes only when its repository has a valid `owner/name` slug and
passes the configured public allowlist. Built-in private-name patterns still
block a record when the allowlist contains it.

Review old state before you allow a repository whose visibility or ownership
changed. An allowlist confirms scope. It does not prove that every stored title
is suitable for publication.

### Text redaction

The emitter replaces built-in organization, product, partner, and integration
tokens with generic category terms. Review and extend the redaction table for
your installation.

Redaction is a second control. Repository scope and a manual output review are
still required before publication.

### Reviewer and role names

- A known role slug remains unchanged.
- An unknown reviewer becomes `human`.
- An unknown role name becomes `agent`.

## Command

```text
alfred-shipped-public.py
  --emit-public-json PATH          required; use '-' for stdout
  --state DIR                      override $ALFRED_HOME/state
  --operator NAME                  override $ALFRED_PUBLIC_OPERATOR
  --public-allowlist REPO          repeatable repository allowlist
  --since YYYY-MM-DD               UTC window start
  --until YYYY-MM-DD               UTC window end
  --summary-extra PATH             optional aggregate summary JSON
  --quiet                          suppress informational stderr output
```

Environment variables:

- `ALFRED_HOME`, default `~/.alfred`
- `ALFRED_PUBLIC_OPERATOR`, default `your-org`
- `ALFRED_PUBLIC_REPO_ALLOWLIST`, a comma-separated repository list

## Schema

The v1 JSON Schema is
[`schema/weekly.schema.json`](../schema/weekly.schema.json). A sample feed is
[`schema/weekly.sample.json`](../schema/weekly.sample.json).

```json
{
  "version": 1,
  "generated_at": "ISO 8601 UTC timestamp",
  "operator": "string",
  "window": { "from": "ISO 8601", "to": "ISO 8601" },
  "summary": {
    "prs_merged": "integer >= 0",
    "prs_reverted": "integer >= 0",
    "issues_closed": "integer >= 0",
    "agents_active": "integer >= 0",
    "repos_touched": "integer >= 0",
    "spend_cents": "integer >= 0",
    "merge_clean_pct": "integer 0-100"
  },
  "trend": [
    { "week": "ISO week", "prs_merged": "integer >= 0" }
  ],
  "prs": [
    {
      "repo": "owner/name",
      "number": "integer >= 1",
      "title": "scrubbed string",
      "codename": "known role or agent",
      "merged_at": "ISO 8601 UTC",
      "lines_added": "integer >= 0",
      "lines_removed": "integer >= 0",
      "files_changed": "integer >= 0",
      "reviewed_by": ["known role or human"],
      "url": "https URL"
    }
  ]
}
```

Run the emitter on a schedule only after the output target and repository
allowlist are explicit. The same input produces the same feed except for the
`generated_at` timestamp.
