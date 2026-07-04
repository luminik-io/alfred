---
name: changelog-and-release-notes
description: Reads the diff and writes a CHANGELOG entry in the repo's own voice, and bumps the VERSION when asked. Use when asked to "update the changelog", "write release notes", "add a changelog entry", "bump the version", or when a change is ready to ship and the CHANGELOG has not been updated. Use before or as part of shipping a user-visible change.
license: MIT
---

# Changelog and release notes

## When to use

- A change is ready to ship and the CHANGELOG has no entry for it.
- You are asked to write release notes for a version.
- A PR adds, changes, fixes, or removes user-visible behavior.

The entry is for the reader who upgrades and wants to know what changed and
whether it affects them. Write for that person, not for the committer. Skip
internal-only churn (a refactor with no observable effect) unless the repo's
CHANGELOG convention records it.

## Procedure

1. **Read the diff, not the commit messages.** Commit subjects drift from what
   actually shipped. Look at what the code now does differently: new behavior,
   changed defaults, fixed bugs, removed options.
2. **Match the repo's CHANGELOG voice and format.** Read the existing
   `CHANGELOG.md` first. Follow its structure (Keep a Changelog sections
   Added / Changed / Fixed / Removed, or whatever the repo uses), its tense, and
   its level of detail. Do not impose a new format.
3. **Write one entry per user-visible change**, grouped under the right section.
   Each line says what changed and, where it matters, the effect on the reader
   ("Changed the default page size to 50; callers relying on 20 must pass
   `?limit=20`"). Reference the PR or issue number if the repo does.
4. **Call out breaking changes explicitly.** If a default changed or an API was
   removed, mark it so an upgrader cannot miss it.
5. **Bump VERSION only if asked.** When requested, update the `VERSION` file (or
   the manifest version field) following semver: major for a breaking change,
   minor for a backward-compatible addition, patch for a fix. State the bump and
   why. Do not tag or publish; that is a human gate.
6. Keep the house style: no em-dashes, no marketing adjectives, no invented
   numbers. Plain and accurate.

## Output

- The CHANGELOG entry (or entries), in the repo's existing format and voice,
  under the correct sections, with breaking changes flagged.
- If a bump was requested: the new version and the semver reason for it.
- A one-line summary of what shipped, suitable for a release-notes headline.
