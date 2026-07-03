#!/usr/bin/env bash
# alfred install-hooks - install the dedup-check pre-push hook into your
# engineering repos. Idempotent: backs up any existing pre-push as
# pre-push.bak.<ts> and then symlinks the canonical hook shipped with Alfred.
# Pass --repo <name> to install into one only.
#
# The hook refuses a push if a referenced issue is currently claimed by an
# agent or already has an open PR, so you never race the fleet. Override a
# single push with `git push --no-verify`; disable globally with
# `LABEL_STATE_SKIP_DEDUP_CHECK=1` in your shell rc.
#
# Configuration (all optional):
#   WORKSPACE_ROOT       - directory holding your repo checkouts
#                          (default: $HOME/code).
#   ALFRED_HOOK_SOURCE   - path to the canonical pre-push hook
#                          (default: the examples/git-hooks/pre-push shipped
#                          in this repo).
#   ALFRED_HOOK_REPOS    - space-separated list of repo directory names to
#                          install into (default: every git repo directly
#                          under WORKSPACE_ROOT).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

WORKSPACE_ROOT="${WORKSPACE_ROOT:-$HOME/code}"
HOOK_SOURCE="${ALFRED_HOOK_SOURCE:-$REPO_ROOT/examples/git-hooks/pre-push}"

if [[ ! -f "$HOOK_SOURCE" ]]; then
  echo "alfred install-hooks: pre-push source not found at $HOOK_SOURCE" >&2
  echo "  set ALFRED_HOOK_SOURCE to the canonical hook path." >&2
  exit 1
fi
chmod +x "$HOOK_SOURCE"

# Resolve the target repo directory names. An explicit list (arg or env) wins;
# otherwise install into every git checkout directly under WORKSPACE_ROOT.
declare -a target_repos=()
if [[ "${1:-}" == "--repo" && -n "${2:-}" ]]; then
  target_repos=("$2")
elif [[ -n "${ALFRED_HOOK_REPOS:-}" ]]; then
  # shellcheck disable=SC2206
  target_repos=(${ALFRED_HOOK_REPOS})
else
  for dir in "$WORKSPACE_ROOT"/*/; do
    [[ -d "${dir}.git" ]] && target_repos+=("$(basename "$dir")")
  done
fi

if [[ ${#target_repos[@]} -eq 0 ]]; then
  echo "alfred install-hooks: no target repos found under $WORKSPACE_ROOT" >&2
  echo "  set WORKSPACE_ROOT or ALFRED_HOOK_REPOS, or pass --repo <name>." >&2
  exit 1
fi

ts="$(date -u +%Y%m%d-%H%M%SZ)"
installed=0
skipped=0
backed_up=0
for repo in "${target_repos[@]}"; do
  repo_dir="$WORKSPACE_ROOT/$repo"
  hook_dir="$repo_dir/.git/hooks"
  if [[ ! -d "$hook_dir" ]]; then
    echo "  $repo: no .git/hooks/ directory; skipping"
    skipped=$((skipped + 1))
    continue
  fi
  hook_path="$hook_dir/pre-push"
  if [[ -L "$hook_path" ]]; then
    current_target="$(readlink "$hook_path")"
    if [[ "$current_target" == "$HOOK_SOURCE" ]]; then
      echo "  $repo: already linked to canonical hook"
      installed=$((installed + 1))
      continue
    fi
  fi
  if [[ -e "$hook_path" ]]; then
    backup="$hook_path.bak.$ts"
    mv "$hook_path" "$backup"
    backed_up=$((backed_up + 1))
    echo "  $repo: backed up existing pre-push to $(basename "$backup")"
  fi
  ln -s "$HOOK_SOURCE" "$hook_path"
  echo "  $repo: installed pre-push -> $HOOK_SOURCE"
  installed=$((installed + 1))
done

echo
echo "alfred install-hooks: installed=$installed backed_up=$backed_up skipped=$skipped"
echo
echo "Override per-push: git push --no-verify"
echo "Override globally:  LABEL_STATE_SKIP_DEDUP_CHECK=1 (export in your shell rc)"
