#!/bin/sh
# alfred-os, one-command remote installer.
#
# Designed to be piped straight from the web:
#
#   curl -fsSL https://raw.githubusercontent.com/luminik-io/alfred/main/get.sh | sh
#
# Because it runs under /bin/sh (dash on Debian/Ubuntu, not bash), this file
# is deliberately POSIX-only: no arrays, no [[ ]], no `set -o pipefail`. The
# heavier bootstrap (Homebrew/apt packages, npm, the Python venv) stays in
# install.sh, which runs under bash from a checkout.
#
# What this script does (idempotent, safe to re-run):
#   1. Detects the host OS/arch (macOS arm64/x86_64, Linux) and prints an
#      honest note about what each target supports.
#   2. Checks the prerequisites the fast demo path needs (git, python3.11+,
#      a `claude` or `codex` CLI) and the ones the full fleet needs (gh),
#      with plain-language guidance for anything missing.
#   3. Clones the repo to ~/alfred (override with ALFRED_CHECKOUT). A re-run
#      updates the existing checkout instead of cloning again.
#   4. Ends by printing exactly what to run next, `alfred demo` first.
#
# The demo on-ramp needs none of the heavy install: a cloned repo plus an
# authenticated `claude` CLI is enough. Running the full package install is
# opt-in, set ALFRED_RUN_INSTALL=1 to have this script run install.sh
# non-interactively after the clone.
#
# Environment overrides:
#   ALFRED_CHECKOUT     Where to clone (default: $HOME/alfred)
#   ALFRED_REPO_URL     Repo to clone (default: the public GitHub repo)
#   ALFRED_REPO_REF     Branch/tag/ref to check out (default: the repo default)
#   ALFRED_RUN_INSTALL  Set to 1 to run install.sh after cloning
#   ALFRED_NONINTERACTIVE Passed through to install.sh when it runs

set -eu

# --------------------------------------------------------------------------
# Pretty output (mirrors install.sh's voice). Colors hold real ESC bytes so
# they render whether emitted by printf (step/ok/...) or the cat heredoc at
# the end; a piped, non-tty stdout gets no codes at all.
# --------------------------------------------------------------------------
if [ -t 1 ]; then
  C_BLUE="$(printf '\033[1;34m')"
  C_GREEN="$(printf '\033[1;32m')"
  C_YELLOW="$(printf '\033[1;33m')"
  C_RED="$(printf '\033[1;31m')"
  C_DIM="$(printf '\033[2m')"
  C_OFF="$(printf '\033[0m')"
else
  C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_DIM='' C_OFF=''
fi

step() { printf "${C_BLUE}==>${C_OFF} %s\n" "$*"; }
ok()   { printf "${C_GREEN}  ok${C_OFF} %s\n" "$*"; }
warn() { printf "${C_YELLOW}  !${C_OFF}  %s\n" "$*" >&2; }
die()  { printf "${C_RED}  !!${C_OFF} %s\n" "$*" >&2; exit 1; }
note() { printf "${C_DIM}     %s${C_OFF}\n" "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

REPO_URL="${ALFRED_REPO_URL:-https://github.com/luminik-io/alfred.git}"
REPO_REF="${ALFRED_REPO_REF:-}"
CHECKOUT="${ALFRED_CHECKOUT:-$HOME/alfred}"

# --------------------------------------------------------------------------
# 1. Host detection
# --------------------------------------------------------------------------
step "Checking host"
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)
    case "$ARCH" in
      arm64)
        ok "macOS on Apple silicon ($ARCH), the primary target"
        ;;
      x86_64)
        ok "macOS on Intel ($ARCH)"
        note "Intel Macs run Alfred from source. There is no signed app build"
        note "for Intel, but the CLI and the demo work the same."
        ;;
      *)
        ok "macOS ($ARCH)"
        ;;
    esac
    ;;
  Linux)
    ok "Linux ($ARCH), scheduled with systemd --user timers"
    note "Full fleet setup on Linux is documented in docs/LINUX.md."
    ;;
  *)
    die "Unsupported host: $OS. alfred-os runs on macOS or Debian/Ubuntu Linux. See docs/LINUX.md."
    ;;
esac

# --------------------------------------------------------------------------
# 2. Prerequisites
# --------------------------------------------------------------------------
# The demo path needs git (to clone), python3.11+ (bin/alfred is a
# dependency-free python3 script), and a coding CLI (claude or codex) to make
# the model calls. gh is only needed once you wire up real repos, so a missing
# gh warns rather than stops.
step "Checking prerequisites"

# git: required to clone the repo.
if have git; then
  ok "git $(git --version 2>/dev/null | awk '{print $3}')"
else
  die "git is not installed. Install it, then re-run. macOS: 'xcode-select --install'. Debian/Ubuntu: 'sudo apt-get install -y git'."
fi

# python3.11+: required to run the alfred CLI and the demo.
python_bin=""
if have python3.11; then
  python_bin="python3.11"
elif have python3; then
  python_bin="python3"
fi
if [ -z "$python_bin" ]; then
  die "python3 is not installed. Alfred needs Python 3.11+. macOS: 'brew install python@3.11'. Debian/Ubuntu: 'sudo apt-get install -y python3'."
else
  py_ver="$("$python_bin" --version 2>&1 | awk '{print $2}')"
  py_major="${py_ver%%.*}"
  py_rest="${py_ver#*.}"
  py_minor="${py_rest%%.*}"
  case "${py_major}-${py_minor}" in
    *[!0-9-]*|-*|*-)
      warn "could not parse python version '$py_ver'; assuming it is recent enough"
      ;;
    *)
      if [ "$py_major" -eq 3 ] && [ "$py_minor" -ge 11 ]; then
        ok "$python_bin $py_ver"
      elif [ "$py_major" -gt 3 ]; then
        ok "$python_bin $py_ver"
      else
        warn "$python_bin is $py_ver; Alfred targets Python 3.11+. Install 3.11 before the full install (the demo may still run)."
      fi
      ;;
  esac
fi

# claude or codex: at least one coding CLI is required for the demo.
if have claude; then
  ok "claude CLI on PATH"
elif have codex; then
  ok "codex CLI on PATH"
  note "The demo prefers claude; codex works as the fleet engine."
else
  die "No coding CLI found. Alfred drives 'claude' (recommended) or 'codex'. Install Claude Code: 'npm install -g @anthropic-ai/claude-code', then run 'claude' once to sign in."
fi

# gh: needed for real repos, not for the demo. Warn only.
if have gh; then
  ok "gh $(gh --version 2>/dev/null | head -1 | awk '{print $3}')"
else
  warn "gh (GitHub CLI) is not installed. The demo does not need it, but the full fleet does. Install it before 'gh auth login'."
fi

# --------------------------------------------------------------------------
# 3. Clone (or update) the checkout
# --------------------------------------------------------------------------
step "Fetching alfred-os into $CHECKOUT"
if [ -d "$CHECKOUT/.git" ]; then
  ok "existing checkout found, updating"
  if git -C "$CHECKOUT" pull --ff-only >/dev/null 2>&1; then
    ok "updated $CHECKOUT"
  else
    warn "could not fast-forward $CHECKOUT (local changes?); leaving it as-is"
  fi
elif [ -e "$CHECKOUT" ] && [ -n "$(ls -A "$CHECKOUT" 2>/dev/null)" ]; then
  die "$CHECKOUT already exists and is not an Alfred checkout. Move it aside or set ALFRED_CHECKOUT to an empty path."
else
  if [ -n "$REPO_REF" ]; then
    note "git clone --depth 1 --branch $REPO_REF $REPO_URL"
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$CHECKOUT"
  else
    note "git clone --depth 1 $REPO_URL"
    git clone --depth 1 "$REPO_URL" "$CHECKOUT"
  fi
  ok "cloned into $CHECKOUT"
fi

# --------------------------------------------------------------------------
# 4. Optional full install
# --------------------------------------------------------------------------
# The demo needs nothing more than the clone above. Installing the full fleet
# (packages, npm, venv) is opt-in because it touches the system and may prompt
# for sudo, which is unfriendly inside a piped `curl | sh`.
if [ -n "${ALFRED_RUN_INSTALL:-}" ]; then
  if [ -f "$CHECKOUT/install.sh" ]; then
    step "Running install.sh (ALFRED_RUN_INSTALL is set)"
    if have bash; then
      ( cd "$CHECKOUT" && ALFRED_NONINTERACTIVE="${ALFRED_NONINTERACTIVE:-1}" bash install.sh --non-interactive )
    else
      warn "bash not found; skipping install.sh. Run it yourself from $CHECKOUT."
    fi
  else
    warn "install.sh not found in $CHECKOUT; skipping."
  fi
fi

# --------------------------------------------------------------------------
# 5. Next steps
# --------------------------------------------------------------------------
cat <<EOF

${C_GREEN}===> Alfred is on your machine.${C_OFF}

Start with the two-minute demo. It needs nothing but an authenticated
'claude' CLI, no GitHub, no Slack, no tokens:

  ${C_BLUE}cd $(printf '%s' "$CHECKOUT")${C_OFF}
  ${C_BLUE}./bin/alfred demo${C_OFF}

When you are ready to wire up the full fleet (packages, scheduler, repos):

  ${C_BLUE}bash install.sh${C_OFF}                 # from $(printf '%s' "$CHECKOUT")
  ${C_BLUE}gh auth login${C_OFF}                   # GitHub
  ${C_BLUE}claude auth login${C_OFF}               # Claude Code auth
  ${C_BLUE}./bin/alfred-init.py${C_OFF}            # choose repos, team names, schedule

Full walkthroughs: INSTALL.md (from-zero), docs/DEMO.md (the demo),
docs/LINUX.md (systemd --user on Linux).

If anything went sideways, open an issue at
https://github.com/luminik-io/alfred/issues with the output above.
EOF
