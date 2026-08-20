# Release Checklist

Use this before tagging a public Alfred release.

## Preflight

- Confirm every release version field has the intended version without a
  leading `v`: `VERSION`, `pyproject.toml`, `site/package.json`,
  `clients/desktop/package.json`, and `clients/desktop/src-tauri/Cargo.toml`.
  Regenerate the npm and Cargo lockfiles after the change.
- Confirm `CHANGELOG.md` has a dated section for that version and its
  `Unreleased` section contains only future work.
- Confirm GitHub Pages is set to workflow publishing, not branch/root publishing:

  ```sh
  gh api repos/luminik-io/alfred/pages --jq '.build_type'
  # expected: workflow
  ```

- Confirm immutable releases are enabled:

  ```sh
  gh api -H 'X-GitHub-Api-Version: 2026-03-10' \
    repos/luminik-io/alfred/immutable-releases --jq '.enabled'
  # expected: true
  ```

- Confirm the active `Protect Release Tags` ruleset targets tags:

  ```sh
  gh api repos/luminik-io/alfred/rulesets \
    --jq '.[] | select(.name == "Protect Release Tags") | [.target, .enforcement]'
  # expected: ["tag","active"]
  ```

- Run the local gates:

  ```sh
  uv run --with pytest pytest tests/
  uv run --with 'ruff>=0.6' ruff check .
  uv run --with 'ruff>=0.6' ruff format --check .
  uv run --with 'mypy>=1.10' mypy lib/
  bash bin/scrub-check.sh
  ./bin/alfred doctor
  ```

- Run the complete fresh-home path on the release source:

  ```sh
  uv run --extra dev pytest -q tests/test_scratch_home_e2e.py
  ```

  This installs Alfred into temporary state, configures the fleet, starts the
  local API, verifies Desktop readiness and battery status, and removes the
  temporary state when the test ends.

- If shell scripts changed, run `shellcheck` on the changed files.
- If docs site content changed, run `npm --prefix site run build`.

## Scrub Gate

`bash bin/scrub-check.sh` must pass before tagging. It scans tracked and untracked worktree files, excluding generated dependency trees and lockfiles, for:

- Host-specific paths or identifiers from local development machines.
- Real-looking Slack webhook URLs, Slack bot or app tokens, and AWS access key IDs.

Keep example secrets obviously fake, for example `xoxb-...` or `https://hooks.slack.com/services/T.../B.../...`.

## Tag And Release

1. Land the signed release-preparation PR with the aligned version fields,
   changelog, lockfiles, and docs updates.
2. Tag from the release commit:

   ```sh
   git tag -s "v$(cat VERSION)" -m "v$(cat VERSION)"
   git push origin "v$(cat VERSION)"
   ```

3. Dispatch the release workflow from protected `main`, then watch it:

   ```sh
   gh workflow run release.yml --repo luminik-io/alfred --ref main \
     -f tag="v$(cat VERSION)"
   ```

   It verifies the annotated tag, signed tag object, signed commit, main-branch
   ancestry, `VERSION`, and release notes before it creates the GitHub Release
   as a **draft**. It also prints the source tarball sha256 for Homebrew. The
   draft is not public yet, by design.
4. Dispatch the Linux packaging workflow from protected `main`:

   ```sh
   gh workflow run package-linux.yml --repo luminik-io/alfred --ref main \
     -f tag="v$(cat VERSION)"
   ```

   It verifies the tag and commit, builds with read-only repository access,
   inspects both packages, then uploads `Alfred.AppImage` and `Alfred.deb` to
   the existing draft from a separate write-scoped job. Build the macOS
   packages from the same tag in the trusted signing environment. Sign,
   notarize, and staple the macOS build, then upload `Alfred.dmg` and
   `Alfred.app.zip`. The release body claims a desktop download, so all four
   assets must exist before publication.
5. Open the draft release, confirm the body and the attached assets, then press Publish. Publishing marks it as the latest release.
6. Update `Formula/alfred-os.rb` with the printed source archive sha256.
7. Set the release version in `Casks/alfred-os.rb` and update its sha256 from
   the published `Alfred.dmg`. Do not use the source archive checksum for the
   Cask. Land both Homebrew changes in a signed follow-up PR.
8. Re-run the `Site` workflow and verify the live docs page:

   ```sh
   gh workflow run site.yml --repo luminik-io/alfred --ref main
   curl -fsSL https://alfred.luminik.io/ | grep -E 'Alfred|Starlight'
   ```

9. Smoke-test the published CLI and Cask install paths from a fresh directory.

The full tag-to-publish flow, and why the draft gate keeps the download claim
accurate, is in [`RELEASING.md`](RELEASING.md).
