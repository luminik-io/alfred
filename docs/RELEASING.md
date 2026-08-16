# Releasing Alfred

How a tagged Alfred release goes out, end to end. This is the process runbook.
For the pre-tag gate list (tests, scrub, docs build) see
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md). For how the desktop installer is
built and what artifacts it produces, see
[`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md).

## Why the release starts as a draft

The release body is the `Highlights` block from `CHANGELOG.md`. From v0.5.0 on,
that block tells the reader Alfred Desktop can be downloaded. The signed and
notarized macOS `.dmg` / `.app.zip`, plus Linux `.AppImage` / `.deb` assets, are
produced in the trusted signing environment and attached to the tagged draft.
`release.yml` creates the draft and prints the source tarball checksum for the
Homebrew formula.

So the release is created as a **draft**. A draft is not public and is not the
latest release. This prevents the release page from offering a desktop
download before the files exist. Nobody can read
"download the desktop app" on a published release page until the assets are
attached. A human attaches the assets and presses Publish.
Repository release immutability locks the tag and attached assets when the
draft is published. Draft assets remain editable until that point. The active
`Protect Release Tags` ruleset blocks updates and deletion of `v*` tags before
publication.

## Flow for a version (vX.Y.Z)

1. **Prepare the release in a signed PR.** Set the same version, without a
   leading `v`, in `VERSION`, `pyproject.toml`, `site/package.json`,
   `clients/desktop/package.json`, and `clients/desktop/src-tauri/Cargo.toml`.
   Regenerate both npm lockfiles and the Cargo lockfile. Move the shipped
   changelog entries into a dated version section and leave only future work in
   `Unreleased`. Land the PR on `main`.

2. **Tag from the release commit.** From `main` at the merged prep commit:

   ```sh
   git tag -s "v$(cat VERSION)" -m "v$(cat VERSION)"
   git push origin "v$(cat VERSION)"
   ```

3. **Run `release.yml` from protected `main`.** The tag push does not create a
   release. Dispatch the trusted workflow from `main` and pass the tag as data:

   ```sh
   gh workflow run release.yml --repo luminik-io/alfred --ref main \
     -f tag="v$(cat VERSION)"
   ```

   The `Release` workflow:
   - rejects a run that is not dispatched from `main`,
   - verifies that the tag is annotated, signed, and points to a verified
     commit on `origin/main`,
   - verifies the tag matches the `VERSION` file and fails if they differ,
   - extracts the matching `CHANGELOG.md` section into the release body,
   - creates (or updates) the GitHub release as a **draft**,
   - prints the source tarball `sha256` for the Homebrew formula.

   The workflow treats the tag as data. It does not check out or execute files
   from the tag before verification. At this point the release exists but is
   not public and has no desktop assets.

4. **Build the desktop packages against the tag.** The trusted signing
   environment builds the signed and notarized macOS `.dmg` /
   `.app.zip` and the Linux `.AppImage` / `.deb` from the tagged source and uploads them to the draft
   release created in step 3. The desktop bundle version is already aligned to
   the release in the prep step (`clients/desktop/package.json` and
   `src-tauri/Cargo.toml` are set to the release number, and `tauri.conf.json`
   reads the version from `package.json`), so the desktop installers carry the
   release version with no separate manual bump here. Confirm every expected
   asset is attached before moving on. The public download page uses
   `/releases/latest/download/...`, so the draft release must include these
   stable asset names before it is published:

   - `Alfred.dmg`
   - `Alfred.app.zip`
   - `Alfred.AppImage`
   - `Alfred.deb`

5. **Publish the release.** Once the desktop assets are attached, a human opens
   the draft release, checks the body and the asset list, and presses Publish.
   Publishing marks it as the latest release. Now the download claim in the
   `Highlights` is backed by real, attached assets.

6. **Update the Homebrew source formula.** Put the source `sha256` from step 3
   into `Formula/alfred-os.rb` and push it to the tap.

7. **Update the Homebrew Cask.** Set the release version in
   `Casks/alfred-os.rb`, calculate the `sha256` of the published `Alfred.dmg`,
   and put that checksum in the Cask. Commit the Formula and Cask update in a
   signed follow-up PR. Do not reuse the source archive checksum for the DMG.

8. **Verify the download page.** Re-run the `Site` workflow and verify the live
   page. The page points at the latest release's stable asset names, so no site
   code change is needed when those names are present.

## Required order

The single rule: the release stays a draft until the desktop assets are attached.
Steps 3 and 4 produce the release and the assets; step 5 is the human gate that
makes the download claim public only after both are done. Do not add `--latest`
to the `release.yml` create or edit step, because `--latest` publishes the
release and removes the draft gate.
