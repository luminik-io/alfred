#!/usr/bin/env bash
set -euo pipefail

tag="${1:-}"
main_ref="${2:-origin/main}"

if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "release-source-gate: invalid release tag: $tag" >&2
  exit 1
fi

tag_ref="refs/tags/$tag"
if [[ "$(git cat-file -t "$tag_ref" 2>/dev/null || true)" != "tag" ]]; then
  echo "release-source-gate: $tag must be an annotated tag" >&2
  exit 1
fi

tag_commit="$(git rev-parse "$tag_ref^{commit}")"
if ! git merge-base --is-ancestor "$tag_commit" "$main_ref"; then
  echo "release-source-gate: $tag is not on $main_ref" >&2
  exit 1
fi

printf '%s\n' "$tag_commit"
