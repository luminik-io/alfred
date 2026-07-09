# Identity and themes

Every Alfred agent has two names, and it helps to keep them straight from the
start.

- The **role** is the canonical identity. It is a short, lowercase slug like
  `architect`, `senior-dev`, or `reviewer`. The role is what the machine uses:
  scheduler labels, GitHub labels, worktree paths, commit-trailer metadata, and
  merge gates all key off it. It never changes.
- The **display name** is what you see. It comes from the active theme. In the
  default `batman` theme the `architect` role shows as "Batman", `senior-dev`
  shows as "Lucius", and `reviewer` shows as "Ra's al Ghul". Switch to another
  theme and the same roles show different names.

So when you read "the senior-dev role (Lucius in the default theme)", the
`senior-dev` part is the real identity and "Lucius" is just the label the
default theme paints on it. If someone renames their team, the role stays
`senior-dev` and the machinery keeps working.

## Why roles are canonical, not the names

Earlier versions of Alfred treated the Batman cast as the identity: the code,
the labels, and the docs all said "Lucius" and "Ra's al Ghul". That made the
names impossible to change without breaking things. The rename to role-slugs
fixed that. The role is now the stable contract, and the cast lives on as the
display names of the default theme. You can rename your whole team, or invent a
brand-new team, without touching a single scheduler label or GitHub label.

## The roles

The default full fleet ships these roles. The right-hand column is the name each
one shows in the default `batman` theme.

| Role (canonical) | What it does | Name in the default theme |
|---|---|---|
| `architect` | Plans multi-repo rollouts and files scoped child work after approval. | Batman |
| `planner` | Scopes single-repo work into `agent:implement` issues. | Drake |
| `spec-planner` | Reads a spec directory and files multi-repo bundles. | Damian |
| `senior-dev` | Claims an issue, writes the code, opens the pull request. | Lucius |
| `test-engineer` | Adds tests for the lowest-coverage changed files. | Bane |
| `reviewer` | Reviews the code a different agent wrote. | Ra's al Ghul |
| `fixer` | Clears the P0 and P1 review comments. | Nightwing |
| `triage` | Classifies new bug reports and hands them off. | Robin |
| `e2e-runner` | Runs post-deploy smoke tests. | Huntress |
| `ops-watch` | Watches deploy health and reports drift. | Gordon |

The utility jobs (auto-merge, cleanup, code-map refresh, briefs, recaps, memory
harvest, telemetry) also have roles and themed names, but most people leave them
with their plain-English labels.

## Themes

A theme is a named set of display names, one per role. Alfred ships three:

- **Batman** (default): the Gotham cast above.
- **Transformers**: Optimus Prime leads the architecture, Ironhide is the senior
  developer, Ratchet reviews, and so on.
- **Justice League**: the architect leads and the League ships the pipeline.

You can also author a **custom** theme: your own name for every role. Custom
themes are stored on the host so every surface honors them, not just the machine
you picked them on.

## How name resolution works

The active theme is stored once, in
`$ALFRED_HOME/state/roster-theme/roster-theme.json`, under the runtime state
directory. It holds only the active theme id and, for a custom theme, the map of
role to display name and role to role label. No message text, no ids, nothing
else.

Every surface reads names through that one store, so a name change lands
everywhere at once:

- **Slack** posts resolve the display name from the active theme as messages are
  written. A running Slack listener picks up a theme change without a restart,
  because the store is written atomically.
- **The desktop app** shows themed names in the Agents roster, in onboarding,
  and anywhere an agent is named.
- **The CLI** resolves the same names when it prints agent activity.

The stable role-slug is always available underneath. Pull request titles,
worktree paths, and log filenames keep the role, so a themed name never leaks
into a place the machine depends on.

Example: the role remains `senior-dev` in scheduler labels, GitHub labels,
worktrees, and PR metadata. The same role can appear as Lucius in the default
Batman theme, Ironhide in the Transformers preset, or "Maya" in your custom
theme inside Desktop and Slack.

## Picking or building a theme

Two ways to set your team's names:

1. **Pick a preset.** In the desktop app's Setup step or the Agents roster
   picker, choose Batman, Transformers, or Justice League. That is one write to
   the theme store.
2. **Build a custom theme by chatting.** The desktop app has a conversational
   theme builder: describe the vibe you want ("name them after Greek gods") and
   Alfred proposes a full role-to-name mapping you can tweak and save. See the
   theme-builder section of [`ONBOARDING.md`](ONBOARDING.md).

A complete custom theme has to name every core engineering role. The ops and
release jobs are optional: leave them out and they fall back to their default
theme names.

### Constraints for custom names

- Keep them short and single-line. Long names clutter a Slack channel.
- Keep them pronounceable. You will say "senior-dev shipped #303" out loud, and
  the themed name should be easy to say too.
- Keep them distinct. Two roles cannot share one name; a broken roster is worse
  than a plain one.
- Keep them coherent. Pick one universe rather than mixing casts.

## See also

- [`AGENTS.md`](AGENTS.md): the full role list, schedules, and how the runtime
  codename gets wired.
- [`ONBOARDING.md`](ONBOARDING.md): conversational setup and the theme builder.
- [`GLOSSARY.md`](GLOSSARY.md): one-line definitions for every role, label, and
  runtime term.
