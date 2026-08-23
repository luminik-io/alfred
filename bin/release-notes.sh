#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
changelog="${2:-}"
mode="${3:-}"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "release-notes: invalid version: $version" >&2
  exit 1
fi
if [[ ! -f "$changelog" ]]; then
  echo "release-notes: changelog not found: $changelog" >&2
  exit 1
fi
if [[ -n "$mode" && "$mode" != "--require-dated" ]]; then
  echo "release-notes: invalid mode: $mode" >&2
  exit 1
fi
if [[ "$mode" == "--require-dated" ]]; then
  version_pattern="${version//./\\.}"
  if ! grep -Eq "^## \\[$version_pattern\\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" "$changelog"; then
    echo "release-notes: dated heading required for $version" >&2
    exit 1
  fi
fi

notes_file="$(mktemp)"
trap 'rm -f "$notes_file"' EXIT

awk -v ver="$version" '
  $0 == "## [" ver "]" || index($0, "## [" ver "] - ") == 1 { in_ver=1; next }
  in_ver && /^## \[/ { exit }
  in_ver && /^### [Hh]ighlights[[:space:]]*$/ { in_hl=1; next }
  in_hl && /^### / { exit }
  in_hl {
    if (NF == 0) { if (started) blanks++; next }
    while (blanks-- > 0) print ""
    blanks = 0
    started = 1
    print
  }
' "$changelog" > "$notes_file"

if [[ ! -s "$notes_file" ]]; then
  echo "release-notes: no Highlights found for $version" >&2
  exit 1
fi

cat "$notes_file"
