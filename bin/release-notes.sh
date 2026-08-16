#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
changelog="${2:-}"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "release-notes: invalid version: $version" >&2
  exit 1
fi
if [[ ! -f "$changelog" ]]; then
  echo "release-notes: changelog not found: $changelog" >&2
  exit 1
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
