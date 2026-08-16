#!/usr/bin/env bash
set -Eeuo pipefail

# Shellcheck every Bash script in the repository with the same severity used by
# CI. Keep generated shell scripts visible to this check by materialising them
# before this loop if the repository adds any in the future.

checked=0
while IFS= read -r -d '' file; do
  case "$file" in
    *.sh | *.bash)
      ;;
    *)
      [[ -x "$file" ]] || continue
      head -n 1 "$file" | grep -qE '^#!.*\b(bash|sh)\b' || continue
      ;;
  esac
  echo "==> $file"
  shellcheck -S warning "$file"
  checked=$((checked + 1))
done < <(
  find . -type f \
    ! -path './site/node_modules/*' \
    ! -path './.git/*' \
    -print0
)

echo "shellcheck: ${checked} files clean"
