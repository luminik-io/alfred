#!/usr/bin/env bash
set -Eeuo pipefail

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

embedded_directory=$(mktemp -d)
cleanup_embedded() {
  rm -rf -- "$embedded_directory"
}
trap cleanup_embedded EXIT HUP INT TERM

python3 - "$embedded_directory" "$PWD" <<'PY'
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

destination = Path(sys.argv[1])
repository_root = Path(sys.argv[2])
sys.path.insert(0, str(repository_root / "lib"))

from ci_runner import (  # noqa: E402
    _fallback_guest_script,
    _guest_privilege_lock_script,
    _runner_guest_script,
    load_config,
)


def lima_provision_scripts(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    scripts: list[str] = []
    for index, line in enumerate(lines):
        if (
            re.fullmatch(
                r"script:\s*\|(?:[1-9][+-]?|[+-][1-9]?)?(?:\s+#.*)?",
                line.strip(),
            )
            is None
        ):
            continue
        marker_indent = len(line) - len(line.lstrip())
        block: list[str] = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                block.append("")
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip())
            if candidate_indent <= marker_indent:
                break
            block.append(candidate)
        script = textwrap.dedent("\n".join(block)).strip()
        if script:
            scripts.append(f"{script}\n")
    if not scripts:
        raise RuntimeError(f"no Lima provision scripts found in {path}")
    return scripts


config = load_config(repository_root / "examples" / "ci-runner" / "runner.toml")
embedded_scripts = {
    "guest-privilege-lock.sh": _guest_privilege_lock_script(),
    "runner-guest.sh": _runner_guest_script(),
    "fallback-guest.sh": _fallback_guest_script(config, "a" * 40),
}
for index, script in enumerate(lima_provision_scripts(config.lima_template), start=1):
    embedded_scripts[f"lima-provision-{index}.sh"] = script

for name, script in embedded_scripts.items():
    (destination / name).write_text(script, encoding="utf-8")
PY

while IFS= read -r -d '' file; do
  echo "==> embedded:$file"
  shellcheck -S warning "$file"
  checked=$((checked + 1))
done < <(find "$embedded_directory" -type f -name '*.sh' -print0)

echo "shellcheck: ${checked} files clean"
