# Mac Mini CI runner

Status: implementation-ready specification

## Objective

Run one Alfred CI job at a time on the operator's 16 GB Mac Mini without using
GitHub-hosted runner minutes and without letting pull-request code reach the
macOS host.

The Mac is the trusted control plane. Every job runs in a new Linux VM and the
VM is deleted after that job. The first rollout uses an organization runner
inside `mac-mini-disposable`, a group restricted to `luminik-io/alfred` and the
trusted workflow on `main`. It is not a deployment runner and it receives no
production credentials.

The control surface also supports a Hermes-operated fallback. If GitHub billing
prevents a workflow from starting at all, Hermes can run the same repository
checks in a disposable VM and, only after explicit approval, publish a commit
status from the trusted host.

This work is separate from [PR 598](https://github.com/luminik-io/alfred/pull/598).
That PR reduces workflow fan-out and hosted runner usage. This document and its
implementation do not edit those workflows.

## Assumptions

1. The operator host is Apple silicon macOS with 16 GB memory.
2. Lima 2.x is the VM boundary. It is not installed automatically.
3. The public Alfred repository is the only allowed repository in the first
   configuration.
4. Same-repository pull-request code is untrusted even when fork pull requests
   are rejected.
5. The host's existing GitHub authentication is not modified by setup or test
   commands.
6. The dispatch workflow becomes callable only after this reviewed pull request
   reaches `main`.

## Commands

All commands run from the Alfred checkout:

```sh
# Read-only host and configuration checks
python3 bin/alfred-ci-runner.py \
  --config examples/ci-runner/runner.toml \
  preflight

# Print the exact mutations without creating a VM or GitHub runner
python3 bin/alfred-ci-runner.py \
  --config examples/ci-runner/runner.toml \
  serve-one --pr PULL_REQUEST_NUMBER --dry-run

# After the manual GitHub setup and explicit operator approval
python3 bin/alfred-ci-runner.py \
  --config examples/ci-runner/runner.toml \
  serve-one --pr PULL_REQUEST_NUMBER --approve-registration

# Run fallback checks for one immutable commit without publishing a status
python3 bin/alfred-ci-runner.py \
  --config examples/ci-runner/runner.toml \
  fallback --sha FULL_40_CHARACTER_SHA

# Publish pending and final commit statuses from the host
python3 bin/alfred-ci-runner.py \
  --config examples/ci-runner/runner.toml \
  fallback --sha FULL_40_CHARACTER_SHA --publish-status

# Delete one named stale VM after inspecting it
python3 bin/alfred-ci-runner.py \
  --config examples/ci-runner/runner.toml \
  cleanup --instance alfred-ci-EXACT_NAME --approve-delete
```

Validation:

```sh
python3 -m pytest tests/test_ci_runner.py -q
ruff check lib/ci_runner.py bin/alfred-ci-runner.py tests/test_ci_runner.py
ruff format --check lib/ci_runner.py bin/alfred-ci-runner.py tests/test_ci_runner.py
mypy lib/ci_runner.py
python3 -m py_compile lib/ci_runner.py bin/alfred-ci-runner.py
actionlint .github/workflows/mac-mini-ci.yml
```

## Project structure

```text
bin/alfred-ci-runner.py              thin executable entry point
lib/ci_runner.py                     validated control plane
examples/ci-runner/lima.yaml         isolated VM template
examples/ci-runner/runner.toml       repository and resource allowlist
.github/workflows/mac-mini-ci.yml    trusted main-only dispatch workflow
tests/test_ci_runner.py              unit and command-boundary tests
docs/MAC_MINI_CI_RUNNER.md           spec, threat model, and runbook
```

## Security boundaries

### Always

- Resolve and check the full 40-character commit SHA before executing code.
- Resolve the pull request through GitHub and require an open, non-draft,
  same-repository head targeting `main`.
- Hold a non-blocking host file lock for the full VM lifetime.
- Use a new VM name and a new ephemeral GitHub runner registration for each job.
- Register with `--no-default-labels` and exactly one random label so another
  queued job cannot match a shared subset.
- Disable host mounts, dynamic port forwarding, containerd, Rosetta, and SSH
  agent forwarding with Lima plain mode.
- Remove password-based sudo and reject new guest connections to private,
  carrier-grade NAT, link-local, multicast, and reserved address ranges.
- Keep the Lima SSH control socket bound to host loopback.
- Download a pinned GitHub runner release and verify its SHA-256 digest.
- Keep GitHub credentials and commit-status calls on the host.
- Give the workflow only `contents: read`.
- Save bounded runner diagnostics under a mode-0700 host state directory before
  deleting the VM.
- Keep untrusted job output out of the host terminal. Store bounded raw logs
  mode 0600 and use GitHub's job log for normal inspection.
- Delete the VM in a `finally` path after success, failure, cancellation, or
  timeout.
- Remove the exact org runner registration after the VM stops if GitHub's
  ephemeral deregistration has not completed.

### Ask first

- Install Lima.
- Create or change a GitHub token.
- Register a runner.
- Merge or change the dispatch workflow.
- Publish a commit status.
- Delete a named recovery VM.

### Never

- Mount the host home, repository, Docker socket, SSH agent, Keychain, cloud
  configuration, or Hermes state into the guest.
- Run fork pull requests.
- Put registration tokens in command arguments, files, logs, TOML, or workflow
  YAML.
- Add production, deployment, package-publishing, or signing secrets to this
  runner or its workflow.
- Use `pull_request_target` for a job that checks out or runs pull-request code.
- Reuse a VM, runner registration, or workspace between jobs.
- Run more than one guest at a time.
- Publish status for a symbolic branch or abbreviated SHA.

## Threat model

### Protected assets

- macOS files, Keychain entries, SSH keys, cloud CLI credentials, browser
  sessions, and Hermes state
- GitHub repository administration credentials on the host
- integrity of required commit statuses
- availability of the 16 GB Mac Mini

### Adversaries

- a fork author changing workflow or repository code
- a compromised dependency or action executed during a job
- a same-repository branch containing malicious code
- an operator mistake that targets the wrong repository, SHA, or VM
- a failed job that leaves a stale runner or consumes all host resources

### Controls

| Risk | Control |
|---|---|
| Fork code reaches the Mac | The host rejects any PR whose head repository differs from `luminik-io/alfred`, then dispatches a workflow from trusted `main` with a random one-use runner label. |
| Another queued job steals the guest | The runner has no default or shared labels. It has only the random label required by the trusted dispatch. |
| PR code escapes into host files | Lima plain mode, no mounts, no forwarded agent, no static port forwards, disposable guest. |
| PR code probes services on the Mac or LAN | The guest has no password-based sudo, and host-installed firewall rules reject new egress to private, carrier-grade NAT, link-local, multicast, and reserved ranges while preserving established SSH control traffic and DNS. |
| Job output attacks the host terminal | The guest redirects untrusted process output to guest files. The host stores only bounded mode-0600 diagnostics and does not render them. |
| Job steals host GitHub credentials | Only the short-lived runner registration token crosses the boundary. Host `gh` credentials never enter the guest. |
| Registration token leaks through process listing | The host writes it only to the guest process stdin. GitHub's `config.sh` necessarily receives it inside the disposable guest. |
| Job gains repository write access | Workflow permissions are `contents: read`; no secrets or environments are referenced. |
| Job reaches production | No cloud credentials, deploy commands, signing material, environments, or production secrets exist in the guest. |
| Stale workspace poisons a later job | The runner is registered with `--ephemeral`; the entire VM is deleted after one job. |
| Resource exhaustion | One host lock, four virtual CPUs, 6 GiB RAM, 40 GiB disk, and a 90-minute wall timeout. |
| Wrong commit receives a fallback result | The CLI requires a full SHA, verifies it through the repository API, fetches that exact object, and checks guest `HEAD` before tests. |
| Guest forges a success status | The guest returns only an exit code. The host maps that code to a status and calls GitHub after cleanup. |
| Runner binary is replaced upstream | Version and SHA-256 digest are pinned in trusted TOML. |
| VM or offline registration survives a crash | The recovery command requires an exact allowlisted prefix and an explicit delete flag, then targets only that VM and exact runner name. |

### Residual risks

- VM isolation is stronger than a persistent self-hosted runner but is not a
  proof against a hypervisor or Lima vulnerability. Keep macOS and Lima
  patched.
- Same-repository code can use the guest's network and the read-only job token.
  Repository write permissions must remain disabled. Internet egress is
  intentionally available for source and package downloads.
- The guest downloads normal test dependencies from their public registries.
  Lockfiles and checksum-aware package managers remain the repository's supply
  chain controls.
- GitHub recommends external diagnostics for ephemeral runners. This design
  keeps only bounded local diagnostics. If unattended operation becomes
  business-critical, add a low-cost encrypted log sink before calling the
  service production-grade.

GitHub documents that ephemeral runners deregister after one job and that the
runner requires outbound HTTPS on port 443:
[self-hosted runner reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners).
GitHub also warns against exposing persistent self-hosted runners to public
forks:
[runner access guidance](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access).
Lima plain mode disables mounts, dynamic forwarding, containerd, Rosetta, and
SSH agent forwarding:
[Lima plain mode](https://lima-vm.io/docs/config/plain/).

## Workflow contract

`.github/workflows/mac-mini-ci.yml` uses `workflow_dispatch`, not
`pull_request`, because pull-request code can propose changes to workflow files.
GitHub will not dispatch this new workflow until it exists on the default
branch. After a reviewed merge, the host verifies PR provenance first and passes
the exact SHA plus a random one-use runner label to the trusted workflow on
`main`. Before merging a change to it:

1. Keep the `workflow_dispatch` event. Do not add `pull_request` or
   `pull_request_target`.
2. Keep the exact-SHA checkout and verification step.
3. Keep the one-use runner label input.
4. Keep `permissions: contents: read`.
5. Keep `runs-on` restricted to the one-use label input. Do not add default or
   shared labels.
6. Do not add `environment`, `secrets`, deployment, release, or publishing
   steps.
7. Keep the workflow independent from PR 598. After both land, update branch
   protection deliberately instead of requiring duplicate CI contexts.

The guest establishes outbound connections to GitHub and normal dependency
registries. New connections to the Mac, LAN, link-local services, and reserved
address ranges are rejected. No guest service or port is exposed to the LAN or
internet.

Lima 2.x creates a one-time guest password when `passwordlessSudo` is false.
Before copying a helper or registering a runner, the trusted controller uses
that password only inside the guest to lock the account, invalidates all sudo
timestamps, deletes the password file, and verifies non-interactive sudo is
unavailable. Repository code does not enter the guest unless that check passes.
If a new system package is needed, add it to the reviewed template instead of
granting a job sudo access.

## Manual installation and first run

These steps are intentionally not automated.

1. Inspect the local Homebrew formula, then install Lima:

   ```sh
   brew info lima
   brew install lima
   limactl --version
   ```

2. Prefer a dedicated fine-grained credential or GitHub App installation for
   the host control plane:

   - organization Self-hosted runners: write, to mint registration tokens
   - Actions: write, only to dispatch the trusted `mac-mini-ci.yml` workflow
   - Commit statuses: write, only for the Hermes fallback
   - Metadata: read

   The current operator `gh` account has an authorized `admin:org` scope for
   setup. Prefer replacing that broad scope with the permissions above after
   the smoke run. Store the credential in the macOS Keychain-backed `gh`
   account. Do not export it in a launchd plist or shell profile.

3. Inspect the dry run:

   ```sh
   python3 bin/alfred-ci-runner.py \
     --config examples/ci-runner/runner.toml \
     serve-one --pr PULL_REQUEST_NUMBER --dry-run
   ```

4. Review and merge `.github/workflows/mac-mini-ci.yml` through the normal
   signed pull-request path. Do not dispatch a workflow definition from a PR
   branch.

5. In organization Actions settings, verify `mac-mini-disposable` has all of
   these exact properties:

   - repository visibility: selected
   - selected repository: `luminik-io/alfred` only
   - public repositories: allowed
   - workflow restriction: enabled
   - selected workflow:
     `luminik-io/alfred/.github/workflows/mac-mini-ci.yml@refs/heads/main`

   The selected workflow cannot be configured until that file exists on
   `main`. Do not register a runner while the group is less restrictive.

6. Run the read-only preflight. It fails closed unless Lima and the complete
   runner-group policy are present:

   ```sh
   python3 bin/alfred-ci-runner.py \
     --config examples/ci-runner/runner.toml \
     preflight
   ```

7. Queue one same-repository test PR, then start one ephemeral runner:

   ```sh
   python3 bin/alfred-ci-runner.py \
     --config examples/ci-runner/runner.toml \
     serve-one --pr PULL_REQUEST_NUMBER --approve-registration
   ```

8. Confirm in GitHub that the runner deregisters after the job and on the Mac
   that no `alfred-ci-*` instance remains:

   ```sh
   limactl list
   ```

## Hermes fallback runbook

Use this only when GitHub reports that a workflow could not start because of
billing or hosted-runner capacity. It is not a way to bypass a failing check.

1. Copy the exact head SHA from the pull request.
2. Run without status publication and inspect the local result:

   ```sh
   python3 bin/alfred-ci-runner.py \
     --config examples/ci-runner/runner.toml \
     fallback --sha FULL_40_CHARACTER_SHA
   ```

3. If the result and commit are correct, rerun with `--publish-status`.
   Publication writes `Hermes / Local CI` as pending, then success or failure.
4. Link the local diagnostic directory in the pull-request handoff. Do not paste
   or render raw diagnostics without reviewing them as untrusted binary data.
5. Branch protection must explicitly recognize the fallback context before it
   can replace a GitHub Actions check.

## Cleanup and recovery

Normal cleanup runs automatically. If the process or Mac crashes:

1. List instances:

   ```sh
   limactl list
   ```

2. Inspect the exact `alfred-ci-*` name and diagnostics:

   ```sh
   limactl shell EXACT_INSTANCE_NAME uname -a
   ls -la "${HOME}/.local/state/alfred-ci-runner/diagnostics"
   ```

3. Delete only that instance:

   ```sh
   python3 bin/alfred-ci-runner.py \
     --config examples/ci-runner/runner.toml \
     cleanup --instance EXACT_INSTANCE_NAME --approve-delete
   ```

4. If GitHub still shows an offline runner, remove that exact runner in
   organization settings. Do not bulk-delete runners.
5. If a dispatch stayed queued because runner setup failed, cancel only the run
   whose one-use `alfred-job-*` label was printed by the control plane.
6. Remove a stale lock only after confirming there is no control process and no
   matching VM:

   ```sh
   ps aux | grep '[a]lfred-ci-runner'
   limactl list
   rm "${HOME}/.local/state/alfred-ci-runner/control.lock"
   ```

## Success criteria

- Preflight performs no installation, registration, status publication, or
  deletion.
- Dry-run mode performs no mutation.
- Invalid repository names, abbreviated SHAs, resource values above the Mac
  caps, unsafe labels, and non-prefixed cleanup targets fail closed.
- A second process cannot create a runner or fallback VM while the lock is held.
- Runner registration is org-scoped inside the selected-repository,
  main-workflow-restricted group. It is ephemeral, checksum-verified, and
  supplied over guest stdin.
- Runner dispatch starts from trusted `main`, uses an exact verified
  same-repository PR SHA, and routes through a random one-use label.
- Every exit path attempts diagnostic capture and deletion.
- Fallback code runs only at an exact verified SHA inside a disposable guest.
- Commit status publication is opt-in and occurs only from the host.
- The active workflow has no automatic event, accepts only host-dispatched
  inputs, and cannot run until reviewed code reaches `main`.
