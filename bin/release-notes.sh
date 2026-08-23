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
target_count="$(
  awk -v ver="$version" '
    $0 == "## [" ver "]" || index($0, "## [" ver "] - ") == 1 { count++ }
    END { print count + 0 }
  ' "$changelog"
)"
if ((target_count > 1)); then
  echo "release-notes: duplicate sections for $version" >&2
  exit 1
fi
if [[ "$mode" == "--require-dated" ]]; then
  version_pattern="${version//./\\.}"
  target_heading="$(
    awk -v ver="$version" '
      $0 == "## [" ver "]" || index($0, "## [" ver "] - ") == 1 { print; exit }
    ' "$changelog"
  )"
  if ! grep -Eq "^## \\[$version_pattern\\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" <<< "$target_heading"; then
    echo "release-notes: dated heading required for $version" >&2
    exit 1
  fi
  release_date="${target_heading##* - }"
  IFS=- read -r release_year release_month release_day <<< "$release_date"
  year_number=$((10#$release_year))
  month_number=$((10#$release_month))
  day_number=$((10#$release_day))
  max_day=0
  case "$month_number" in
    1 | 3 | 5 | 7 | 8 | 10 | 12) max_day=31 ;;
    4 | 6 | 9 | 11) max_day=30 ;;
    2)
      max_day=28
      if ((year_number % 400 == 0 || (year_number % 4 == 0 && year_number % 100 != 0))); then
        max_day=29
      fi
      ;;
  esac
  if ((year_number < 1 || day_number < 1 || day_number > max_day)); then
    echo "release-notes: invalid release date for $version: $release_date" >&2
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
