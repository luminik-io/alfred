# Proposed skill drafts

`alfred skills evolve` writes SKILL.md DRAFTS here, one per cluster of related
promoted memory lessons. Each draft is a starting point, not a shipped skill:

- Nothing in this directory is installed or registered. `alfred skills evolve`
  never auto-installs (the substrate rule) and never writes into the live
  `skills/first_party/` set.
- A draft carries `status: proposed` frontmatter so it can never be mistaken for
  a real skill.

## Reviewing a draft

1. Read the draft's lessons and the TODO sections.
2. If it describes a real, repeatable practice, move it up to
   `skills/first_party/<name>/SKILL.md`, remove the `status: proposed` line,
   and write the Procedure and Output sections properly.
3. Register it in `skills/packs.toml` with `install = "first_party"`.

Generated drafts (the `*/SKILL.md` under this directory) are git-ignored so a
review run does not create noise in version control. This README is tracked so
the directory always exists.
