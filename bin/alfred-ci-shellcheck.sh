#!/usr/bin/env bash
set -Eeuo pipefail

# Shellcheck every bash script in the repo, the way CI does.
#
# The .github/workflows/ci.yml shellcheck job runs the same find and the same
# `shellcheck -S warning`; this is the copy you can run before pushing. It is
# deliberately a duplicate rather than the CI job calling this file, because a
# CI job that shells out to a repo script is a CI job an attacker can edit in a
# pull request.
#
# This script used to do one more thing: the self-hosted CI runner generated
# guest scripts as Python strings (guest-privilege-lock, runner-guest,
# fallback-guest, and the Lima provisioning blocks), so they existed nowhere on
# disk and shellcheck could not see them. It materialised those into a temp
# directory and checked them too. That subsystem is gone, so that section went
# with it; there are no generated shell scripts left in this repo, and if one
# reappears it needs this treatment again.

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
