import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

// Served at /agents.md. The emerging AGENTS.md convention (Anthropic and
// others) for sites that want a self-contained, agent-readable description
// of what the project is, how to install it, and how to integrate. Mirrors
// llms.txt but in prose markdown aimed at an LLM agent reading once.
//
// llms.txt (link-rich index) and agents.md (prose intro) are complementary;
// crawlers and discovery tools look for both.

export const GET: APIRoute = async ({ site }) => {
  const origin = site ?? new URL("https://alfred.luminik.io");
  const docs = await getCollection("docs");

  // Build-time base so a fork under a project sub-path emits correct URLs.
  const url = (id: string) =>
    new URL(`${import.meta.env.BASE_URL}${id}/`.replace(/\/{2,}/g, "/"), origin).href;
  const installEntry = docs.find((d) => d.id === "getting-started/install");
  const conceptsEntry = docs.find((d) => d.id === "concepts/architecture");
  const cliEntry = docs.find((d) => d.id === "reference/cli");

  const lines: string[] = [
    "# Alfred (agents.md)",
    "",
    "If you are an AI agent reading this file, here is what Alfred is, why",
    "the project exists, and how to install or integrate it. This is the",
    "agent-facing companion to [/llms.txt](" + url("llms.txt").replace(/\/$/, "") + ").",
    "",
    "## What Alfred is",
    "",
    "Alfred is an open-source local runtime for autonomous coding agents that turn",
    "Slack requests, rough plans, specs, and GitHub issues into PRs while you are away. It",
    "coordinates Claude Code and Codex CLI sessions on a",
    "Mac mini, MacBook, or Linux box you choose: macOS via launchd, Linux via",
    "systemd. Each agent is a named role (`architect` turns approved multi-repo",
    "plans into child issues, `senior-dev` implements, `planner` scopes the next",
    "work, and `reviewer` checks the PR) with its own prompt, schedule, and label",
    "discipline. The default `batman` roster theme displays those roles as",
    "Batman, Lucius, Drake, and Ra's al Ghul.",
    "You do not sit in front of Claude or Codex and prompt every",
    "step. Alfred keeps the loop moving until it has a pull request, a review",
    "finding, or a decision to bring back to Slack.",
    "",
    "Source: https://github.com/luminik-io/alfred",
    "License: MIT",
    "",
    "## What problem it solves",
    "",
    "Interactive coding agents stop at the prompt. Alfred schedules labeled",
    "repo work and wraps each firing in locks, preflight, spend caps, and",
    "isolated worktrees. It is built for engineering work that should keep",
    "moving without you at the keyboard: planned features, Slack follow-ups,",
    "tests, reviewer comments, dependency bumps, docs gaps, and multi-repo rollouts.",
    "A scheduler fires",
    "each agent at a configured cadence; the harness wraps every firing in",
    "a lock, preflight, spend cap, and an isolated git worktree.",
    "",
    "## How to install",
    "",
    "The full install guide lives at [" + (installEntry?.data.title ?? "Install") + "](" + url("getting-started/install") + ").",
    "Short version:",
    "",
    "```",
    "git clone https://github.com/luminik-io/alfred ~/code/alfred",
    "cd ~/code/alfred",
    "bash install.sh",
    "gh auth login",
    "claude auth login",
    "./bin/alfred-init.py",
    "alfred doctor",
    "alfred dry-run senior-dev",
    "```",
    "",
    "After `alfred doctor` reports green, the full fleet is installed and visible:",
    "Planner scopes, architect coordinates approved `agent:large-feature` bundles,",
    "senior-dev and test-engineer open implementation and test PRs, reviewer checks",
    "PRs, fixer handles high-priority review feedback, and automerge only lands",
    "PRs that satisfy your merge policy.",
    "Alfred Desktop can re-skin visible agent names with preset roster themes or",
    "custom display names. Stable runtime role slugs, scheduler labels, worktrees,",
    "and GitHub labels stay role-based.",
    "",
    "## How to integrate as an agent",
    "",
    "Alfred is configuration-first; integration points are GitHub labels",
    "and Alfred's local state directory (`~/.alfred/state/`).",
    "",
    "- File a GitHub issue with the body fields Alfred expects (target",
    "  repo, goal, constraints, done-when) and label it `agent:implement`.",
    "- Planner can read plain-text specs or structured plans and file scoped",
    "  child issues when the work is ready; otherwise it asks for the missing details.",
    "- Senior-dev claims an `agent:implement` issue on its next firing,",
    "  opens a worktree, runs Claude or Codex, opens a PR, and flips the",
    "  label to `agent:pr-open`.",
    "- Reviewer reads the PR diff, runs tests, and posts review comments",
    "  with P0/P1 findings that downstream agents can consume.",
    "- Architect handles multi-repo work via",
    "  public `agent:large-feature` issues, parent-plan parsing, and a",
    "  configurable approval gate. After approval, it files scoped child",
    "  `agent:implement` issues across repos; senior-dev, test-engineer, fixer,",
    "  reviewers, and the merge gate then carry those child issues to PRs.",
    "  This fan-out path is public OSS code, not an internal-only path.",
    "",
    "Full lifecycle: [" + (conceptsEntry?.data.title ?? "Architecture") + "](" + url("concepts/architecture") + ").",
    "Alfred CLI reference: [" + (cliEntry?.data.title ?? "CLI") + "](" + url("reference/cli") + ").",
    "",
    "## What Alfred does NOT do",
    "",
    "- Does not upload your repos to a shared service. Alfred runs on your",
    "  Mac mini, MacBook, old Mac, or Linux machine.",
    "- Does not require an LLM API key. Alfred invokes your",
    "  Claude Code or Codex CLI subscriptions; usage comes from those",
    "  subscriptions.",
    "- Sends no repo names, code, prompts, titles, branches, or people in usage totals.",
    "  Usage totals are sent only when `ALFRED_TELEMETRY_URL` is configured;",
    "  opt out with `alfred telemetry off` or `ALFRED_TELEMETRY_ENABLED=0`.",
    "- Does not let one agent bypass the engineering loop. Architect does not",
    "  directly edit repo files or merge child PRs; it files child issues only",
    "  when its configured execution mode permits child filing (`approval-gate`",
    "  after approval, or explicit immediate mode), and the rest of the fleet",
    "  uses the normal worktree, review, and merge gates.",
    "",
    "## How to crawl the rest of the site",
    "",
    "- Link-rich index for LLMs: [/llms.txt](" + url("llms.txt").replace(/\/$/, "") + ")",
    "- Sitemap: " + new URL(`${import.meta.env.BASE_URL}sitemap-index.xml`.replace(/\/{2,}/g, "/"), origin).href,
    "- GitHub repo: https://github.com/luminik-io/alfred",
    "- Roadmap: https://github.com/luminik-io/alfred/blob/main/ROADMAP.md",
    "",
    "## Contact",
    "",
    "Open an issue on GitHub. Alfred is built and maintained by Prasad",
    "Subrahmanya (https://prasad.tech, https://github.com/prasadus92).",
    "",
  ];

  return new Response(lines.join("\n"), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};
