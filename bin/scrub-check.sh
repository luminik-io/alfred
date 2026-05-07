#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "scrub-check: git is required" >&2
  exit 2
fi

ALLOWLIST_RE='^(\./)?(bin/scrub-check\.sh|\.github/workflows/ci\.yml|CHANGELOG\.md|site/package-lock\.json|.*\.lock)$'
SKIP_PATH_RE='^(\./)?(\.git/|site/node_modules/|infra/agents/launchd/_generated/)'

patterns=(
  "/Users/batman"
  "/Users/prasad"
  "/home/prasad"
  "luminik-internal"
  "prasad@luminik\\.io"
  "C0ATTT5DDGA"
  "T024P63979U"
)

secret_patterns=(
  "https://hooks\\.slack\\.com/services/[A-Z0-9]{8,}/[A-Z0-9]{8,}/[A-Za-z0-9]{20,}"
  "xox[baprs]-[A-Za-z0-9-]{20,}"
  "xapp-[A-Za-z0-9-]{20,}"
  "AKIA[0-9A-Z]{16}"
  "ASIA[0-9A-Z]{16}"
)

candidate_files() {
  local path
  while IFS= read -r -d "" path; do
    [ -n "$path" ] || continue
    [[ "$path" =~ $ALLOWLIST_RE ]] && continue
    [[ "$path" =~ $SKIP_PATH_RE ]] && continue
    printf "%s\0" "$path"
  done < <(
    git ls-files -z
    git ls-files --others --exclude-standard -z
  )
}

scan_patterns() {
  local label="$1"
  shift
  local fail=0

  for pat in "$@"; do
    if candidate_files | xargs -0 grep -InE "$pat" -- 2>/dev/null; then
      echo "::error::Found $label pattern: $pat" >&2
      fail=1
    fi
  done

  return "$fail"
}

fail=0
scan_patterns "private path or identifier" "${patterns[@]}" || fail=1
scan_patterns "secret" "${secret_patterns[@]}" || fail=1

if [ "$fail" -ne 0 ]; then
  echo "scrub-check: failed" >&2
  exit 1
fi

echo "scrub-check: clean"
