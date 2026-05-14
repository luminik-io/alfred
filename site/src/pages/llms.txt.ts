import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

// Served at /llms.txt — the llmstxt.org convention: a curated, link-rich
// markdown index an LLM can read to understand the site. Generated from the
// Starlight `docs` collection so it stays in sync as pages are added.

// Section order + labels mirror the docs-site sidebar. Anything whose id does
// not start with one of these prefixes is skipped from the grouped lists; the
// root page (id "") is handled separately as the intro.
const SECTIONS: { prefix: string; label: string }[] = [
  { prefix: "getting-started/", label: "Getting started" },
  { prefix: "concepts/", label: "Concepts" },
  { prefix: "guides/", label: "Guides" },
  { prefix: "reference/", label: "Reference" },
  { prefix: "about/", label: "About" },
];

export const GET: APIRoute = async ({ site }) => {
  const base = (site ?? new URL("https://alfred.luminik.io")).href.replace(/\/$/, "");
  const docs = await getCollection("docs");

  const url = (id: string) => `${base}/${id}`.replace(/\/$/, "") + (id ? "/" : "/");
  const root = docs.find((d) => d.id === "");
  const summary =
    root?.data.description ??
    "A local agent-fleet runtime for solo builders. Claude Code agents scheduled by launchd or systemd, on one machine you own.";

  const lines: string[] = [
    "# Alfred",
    "",
    `> ${summary}`,
    "",
    "Alfred OS is the open-source framework for running a fleet of autonomous",
    "Claude Code agents on a single machine you own. The OS scheduler (launchd",
    "on macOS, systemd on Linux) fires each agent; the harness wraps every",
    "firing in a lock, preflight, spend cap, and an isolated git worktree. The",
    "engineering fleet ships today; content, sales, and ops departments are the",
    "roadmap. Source: https://github.com/luminik-io/alfred-os",
    "",
  ];

  for (const { prefix, label } of SECTIONS) {
    const entries = docs
      .filter((d) => d.id.startsWith(prefix))
      .sort((a, b) => a.id.localeCompare(b.id));
    if (entries.length === 0) continue;
    lines.push(`## ${label}`, "");
    for (const e of entries) {
      const desc = e.data.description ? `: ${e.data.description}` : "";
      lines.push(`- [${e.data.title}](${url(e.id)})${desc}`);
    }
    lines.push("");
  }

  lines.push(
    "## Source",
    "",
    "- [GitHub repository](https://github.com/luminik-io/alfred-os): the framework, examples, and issues.",
    "- [Roadmap](https://github.com/luminik-io/alfred-os/blob/main/ROADMAP.md): shipped, in flight, and the design boundaries.",
    "",
  );

  return new Response(lines.join("\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
