# Claude Code skills

Skills are small bundles (markdown plus optional scripts) that extend Claude
Code for a specific purpose: code review, refactoring, browser QA, security
checks. alfred-os ships a curated, license-audited set of skill packs and a
CLI to install them. You can also skip the registry and install skills by hand.

## Curated skill packs

The registry lives in [`skills/packs.toml`](../skills/packs.toml). Each pack
records its source repo, license, install method, and which agent roles get it
by default. Attribution for every bundled or referenced skill is in
[`skills/NOTICE.md`](../skills/NOTICE.md).

Two install shapes:

- **vendored** -- the skill is copied into this repo under
  `skills/vendored/<name>/` with its upstream `LICENSE` kept next to it.
  Installing copies it into your skills dir. No network, works offline and in
  CI.
- **first_party** -- an Alfred-authored MIT skill under
  `skills/first_party/<name>/`. Installs like a vendored pack (a local copy, no
  network), but it is our own source, so it carries no upstream attribution.
  Packs flagged `default_install` form the **starter set** (`alfred skills
  install --starter`).
- **fetch** (reference-install) -- the skill is not in this repo. Installing
  runs a network command to pull it from source. Used for large skills and heavy
  dependencies that are better pinned to upstream.

### The packs

| Pack | License | Shape | Source | Default roles |
|---|---|---|---|---|
| `code-review-and-quality` | MIT | vendored | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | pr-review, feature-dev, review-fix |
| `security-and-hardening` | MIT | vendored | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | pr-review, feature-dev |
| `frontend-ui-engineering` | MIT | vendored | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | feature-dev |
| `debugging-and-error-recovery` | MIT | vendored | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | bug-triage, deploy-monitor |
| `vercel-react-best-practices` | MIT | vendored | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) | feature-dev |
| `spec-to-issues` | MIT | first_party | Alfred | planner |
| `write-tests` | MIT | first_party | Alfred | test-coverage, feature-dev |
| `review-security` | MIT | first_party | Alfred | pr-review, feature-dev |
| `add-observability` | MIT | first_party | Alfred | feature-dev, deploy-monitor |
| `migrate-dependency` | MIT | first_party | Alfred | feature-dev |
| `changelog-and-release-notes` | MIT | first_party | Alfred | feature-dev, deploy-monitor |
| `gstack` | MIT | fetch | [garrytan/gstack](https://github.com/garrytan/gstack) | e2e-smoke, pr-review, bug-triage |
| `headroom` | Apache-2.0 | fetch (opt-in) | [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom) | (none) |

The six `first_party` packs are Alfred-authored. They are the starter set:
`alfred skills install --starter` lays all of them down in one offline copy.

Licensing rationale: all bundled sources are permissive (MIT or Apache-2.0). No
copyleft (GPL/AGPL) source is vendored -- copyleft would be reference-install
only for this MIT repo, and the test suite guards against a copyleft skill
sneaking into the vendored set. Vercel declares MIT in its README and SKILL.md
frontmatter but ships no LICENSE file, so the vendored copy carries a
reconstructed MIT notice (see `skills/NOTICE.md`).

## The `alfred skills` command

```sh
alfred skills list                       # all curated packs, with install state
alfred skills list --role feature-dev    # only packs recommended for a role
alfred skills list --json                # machine-readable
alfred skills install <pack>             # install one pack into the skills dir
alfred skills install --starter          # install the default first-party set (offline)
alfred skills install <pack> --dry-run   # preview without writing or fetching
alfred skills install <pack> --yes       # confirm a reference-install network fetch
alfred skills installed                  # what is installed under the skills dir
alfred skills evolve                     # draft SKILL.md proposals from memory (never installs)
alfred skills evolve --since 2026-06-01  # only cluster lessons from this date on
alfred skills evolve --dry-run           # report the proposals without writing a draft
```

Installs land in `~/.claude/skills/` by default. Override with
`ALFRED_SKILLS_DIR` (for example, point it at a project's `.claude/skills` for a
project-scoped install; paths with spaces are handled, the fetch command is
shell-quoted). Vendored installs are idempotent -- re-installing replaces the
directory cleanly. Reference-install packs (`gstack`, `headroom`) run a network
command and require `--yes`; `--dry-run` shows the exact command first.

The command works from all three layouts: a source checkout, a deployed runtime
(`deploy.sh` copies `skills/` into `$ALFRED_HOME` next to `lib/`), and an
installed wheel, where the same verbs are exposed as `alfred-os skills ...`
(the manifest and vendored tree ship inside the wheel).

## Where skills live and how the fleet uses them

```
~/.claude/skills/
├── code-review-and-quality/SKILL.md
├── security-and-hardening/SKILL.md
├── frontend-ui-engineering/SKILL.md
├── debugging-and-error-recovery/SKILL.md
├── vercel-react-best-practices/
│   ├── SKILL.md
│   └── rules/
└── gstack/                  # the gstack setup installs a directory of subskills
    ├── browse/
    ├── qa/
    ├── review/
    └── ship/
```

### Do skills load under `claude -p` (headless)?

Yes, with one caveat you must know. The fleet invokes `claude -p` **without**
`--bare`, and in that mode Claude Code auto-discovers skills in
`~/.claude/skills/` and `<project>/.claude/skills/` exactly as an interactive
session does. Under `--permission-mode bypassPermissions` (how firings run), the
`Skill` tool is permitted, so a matching skill can auto-activate.

The honest caveat: auto-activation is a model decision, not a guarantee, and
`--bare` (which skips all skill, hook, and MCP discovery) is slated to become the
`-p` default in a future Claude Code release. So the reliable, future-proof way
to get a skill into a headless run is to **name it in the agent's prompt**. Two
supported mechanisms:

1. Direct invocation: put `/skill-name` at the start of the `-p` prompt string.
   Claude Code expands the rendered SKILL.md into the message before the model
   runs. Works even in `--bare`.
2. Instruction line: tell the model to use the skill, for example:

   ```
   After implementing the change, use the `code-review-and-quality` skill on
   every file you edited. Apply any P0 or P1 finding before you commit.
   ```

`lib/skill_packs.py` exposes `skill_prompt_snippet(pack)` which returns exactly
such a line for a pack, so a role prompt builder can append it. This is the
`--bare`-proof path and does not depend on the model choosing to activate a
skill on its own.

### The runner skill-injector (metadata-only)

`lib/agent_runner/skills_context.py` is the automatic, `--bare`-proof path. When
a firing runs, `invoke_agent_engine` appends a compact block naming the skills
recommended for that firing's **role**, with the path to each `SKILL.md` so the
agent reads the body on demand (progressive disclosure). It parses **only** the
YAML frontmatter (name + description), never the body, and caps each file read
at 10 MB (mirroring deepagents' `MAX_SKILL_FILE_SIZE`), so it is cheap. The
block looks like:

```
Available skills (invoke by name when the trigger matches; read the SKILL.md ...):
- write-tests: Derive tests from acceptance criteria ... [read: ~/.claude/skills/write-tests/SKILL.md]
```

Role filtering uses the manifest `roles`: a firing only sees skills recommended
for its role. The role is derived automatically from the agent codename via the
canonical roster map (`lib/agent_roster.py`, e.g. `senior-dev` -> `feature-dev`,
`rasalghul` -> `pr-review`), so injection is active for every existing caller
with no code change; a caller may still pass an explicit `role=` to override.
Operational codenames with no skill role (automerge, memory-harvest, ...) inject
nothing. It is on by default and gated by `ALFRED_SKILLS_INJECT` (set to
`0`/`false`/`no`/`off` to disable), mirroring the `ALFRED_*_MCP` convention. When
no installed skill matches the role, no role resolves, or the gate is off, the
prompt is left untouched.

The injector discovers skills ONLY from global, operator-controlled locations:
the configured global skills dir (`ALFRED_SKILLS_DIR`, default `~/.claude/skills`,
where `alfred skills install` places skills) plus the in-repo `skills/first_party`
tree. It does **not** scan the firing's working directory. That is a security
boundary, not just a convention: firings run under
`--permission-mode bypassPermissions`, so scanning a checked-out repo's
`<workdir>/.claude/skills/` would let that repo shadow or inject an unreviewed
`SKILL.md` into an autonomous run just by committing one. Injection therefore
comes only from the operator-curated set, and a firing sees the same skills
regardless of which repo it is working in. **Follow-up:** per-repo project-skill
discovery (reading `<firing-workdir>/.claude/skills` so a repo can ship its own
project skills to its own firings) is a deliberate, trust-gated future step.
Project skills are repo-controlled, so they must be treated as lower-trust than
the curated global set (and gated behind an explicit opt-in) before they can be
injected; that is why repo-local skills are intentionally NOT auto-injected here.

### `alfred skills evolve`: from memory to skill proposals

Over time Alfred's memory accumulates promoted lessons a firing learned about a
codename/repo. `alfred skills evolve` reads those lessons through the normal
recall chain (`memory.config.recall_lessons`), clusters them by `(repo, tag)`,
and writes `SKILL.md` **drafts** under `skills/first_party/_proposed/<name>/`
for an operator to review. It **never installs** a skill and never writes into
the live `skills/first_party/` set (the substrate rule plus Alfred's approval
gate). Each draft carries `status: proposed` frontmatter and TODO sections; an
operator turns a good draft into a real skill and registers it in `packs.toml`
by hand. `--since` filters lessons by date; `--dry-run` reports the plan without
writing. The generated drafts are git-ignored.

There is no CLI flag that registers a skills directory, and no `--settings`
field that adds one -- discovery is purely by the `~/.claude/skills/` and
`.claude/skills/` conventions (plus `--add-dir` and plugins). So the CLI's job
is to place skills where discovery finds them; naming them in the prompt is what
makes a headless run deterministic.

### Per-agent skill matrix

A typical engineering-fleet matrix (skills are opt-in per agent; the framework
does not wire a "skill bus"):

| Codename | Role | Skills it invokes |
|---|---|---|
| Lucius | feature-dev | `code-review-and-quality` (self-check), `security-and-hardening` (auth paths), `frontend-ui-engineering` + `vercel-react-best-practices` (FE repos), gstack `/review` (pre-push) |
| Bane | test-coverage | `code-review-and-quality`, gstack `/qa` |
| Ra's al Ghul | pr-review | `code-review-and-quality`, `security-and-hardening`, gstack `/review` |
| Nightwing | review-fix | `code-review-and-quality`, gstack `/review` |
| Robin | bug-triage | `debugging-and-error-recovery`, gstack `/investigate` |
| Gordon | deploy-monitor | `debugging-and-error-recovery` |
| Huntress | e2e-smoke | gstack `/browse`, `/qa` |

## Token optimization: headroom (opt-in) vs the built-in condenser

The registry lists **headroom** ([headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom),
Apache-2.0) as an opt-in reference-install for token/context compression. Before
adding it, know that alfred-os already ships an overlapping capability, so this
is a comparison, not a double-integration.

**Built-in: `lib/conversation_condenser.py`.** A rolling transcript condenser for
long Ask/Slack chats and long autonomous runs. It keeps the opening turns (the
original task) and the most recent `keep_last` turns verbatim, and replaces the
middle run with one model-written summary block. It has a proactive trigger
(turn-count or character budget) and a reactive `condense_on_overflow` path that
fires on a provider context-overflow error and retries with a smaller prompt.
Every pass emits an auditable `CondensationRecord`. It is config-driven
(`ALFRED_CONDENSER_*`) and has zero model dependency (the summarizer is
injected). This is the framework's answer to "the conversation is too long."

**headroom.** A general-purpose LLM compression toolkit: content-aware routing
to specialist compressors (JSON, AST-aware code, a custom ML model for prose)
with reversible compression (originals cached locally, retrievable on demand). It
ships as a Python or TypeScript library (`compress(messages)`), an HTTP proxy, an
MCP server, and a CLI wrapper.

**How they differ, and where headroom would hook.**

- The condenser operates on **conversation turns** (summarize the middle of a
  long chat). headroom operates on **arbitrary payloads** (compress a large tool
  output, a RAG chunk, a JSON blob, a code file) before they enter the prompt.
  They are complementary, not redundant: the condenser shrinks history, headroom
  shrinks individual large inputs.
- If you adopt headroom, the hook point is **prompt assembly**, not transcript
  condensation. The framework builds a run prompt in the role builders (for
  example `build_prompt` in `bin/senior-dev.py`), which inline a repo `CLAUDE.md` and
  issue payload. A `compress()` call there, on the largest inlined blocks, is the
  natural insertion. It should stay behind an env flag (opt-in), mirroring the
  condenser's `ALFRED_CONDENSER_ENABLED` switch, so a fleet that does not want a
  compression dependency runs unchanged.
- Do **not** route the transcript condenser through headroom. The condenser's
  summary is auditable and memory-promotable by design; replacing it with lossy
  compression would lose that property. Keep them as two independent tools:
  condenser for history, headroom (if adopted) for oversized single inputs.

headroom is reference-install and opt-in for a reason: it carries a heavy ML
model and is Apache-2.0 (vendorable into MIT with LICENSE plus NOTICE retained,
but better pinned to a released version). `alfred skills install headroom --yes`
runs `pip install headroom-ai==<pinned version>` -- the manifest pins the exact
release that was license-audited, so a future upstream release cannot silently
change the installed code or its license; bump the pin and re-audit when
upgrading. Wiring it into prompt assembly is a separate, deliberate step, not
automatic.

## Installing skills by hand

You do not need the registry. Anthropic's official skills, for example:

```sh
mkdir -p ~/.claude/skills
git clone --depth 1 https://github.com/anthropics/claude-code.git /tmp/cc-skills-src
cp -R /tmp/cc-skills-src/skills/* ~/.claude/skills/
rm -rf /tmp/cc-skills-src
```

gstack, if you prefer its own installer over `alfred skills install gstack`:

```sh
git clone https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup
```

## Security note

Skills run with the same permissions as `claude`. They can read and write files
in the agent's worktree, run shell commands, and invoke other tools. Treat any
new skill the way you would any other dependency:

1. Read the `SKILL.md` before installing.
2. Skim the scripts the skill might invoke.
3. Run a Snyk or CodeQL scan if the source is unfamiliar.
4. Pin to a specific commit when installing from a third-party source.

The vendored packs are pinned by virtue of being copied in at a known revision
and license-reviewed (see `skills/NOTICE.md`). Reference-install packs require
`--yes`, so a network fetch is never silent; headroom is pinned to the audited
release, while gstack clones upstream `main` because its skills are versioned
and upgraded by its own `./setup` and `gstack-upgrade` flow.

The fleet's IAM-per-agent and per-firing-worktree isolation limit blast radius:
a compromised skill in one worktree cannot reach the operator's home or a second
Claude account. Mitigation, not prevention.

## Skills NOT recommended for an autonomous fleet

- **Anything that auto-publishes** (auto-tweet, auto-deploy, auto-merge). Use
  these as draft-then-review only.
- **Skills that reach the network without an allowlist.** Egress from a worktree
  is a known agent attack vector (exfiltration, prompt injection from fetched
  content). Default to disabling.
- **Skills the operator has not read.** Skills are markdown. Read them.
