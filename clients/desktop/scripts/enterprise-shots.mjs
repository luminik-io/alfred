// Enterprise-glass design-review capture. Screenshots the key surfaces at
// desktop + mobile widths in both themes against the live dev server, with a
// boundary sanitizer that rewrites private repo names and home paths out of the
// rendered DOM so the committed frames are OSS-clean by construction.
//
//   SHOTS_BASE=http://localhost:1420 node scripts/enterprise-shots.mjs
//
// Dev-only. Not part of the shipped client.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = join(__dirname, "..", ".design-review", "enterprise");
const BASE = process.env.SHOTS_BASE || "http://localhost:1420";

// Boundary sanitizer: run in-page before every screenshot so no private repo
// name or operator home path is ever painted into a committed frame.
const SANITIZE = () => {
  const rewrite = (s) =>
    s
      .replace(/luminik-io\/luminik-[a-z-]+/g, "acme-org/api")
      .replace(/luminik-io/g, "acme-org")
      .replace(/\/Users\/[^/\s"']+/g, "~")
      .replace(/luminik\.io/g, "example.com");
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        if (child.nodeValue && /luminik|\/Users\//.test(child.nodeValue))
          child.nodeValue = rewrite(child.nodeValue);
      } else if (child.nodeType === 1) {
        for (const attr of ["title", "href", "aria-label"]) {
          const v = child.getAttribute && child.getAttribute(attr);
          if (v && /luminik|\/Users\//.test(v)) child.setAttribute(attr, rewrite(v));
        }
        walk(child);
      }
    }
  };
  walk(document.body);
};

// `agents-roster` and `agents-graph` hit the same fleet URL and only differ by
// the persisted roster view, so each surface pins its own `alfred.rosterView`:
// the roster frame forces the list, the graph frame forces the workflow canvas.
const SURFACES = [
  { id: "inbox", q: "tab=inbox", wait: ".command-center" },
  { id: "ask", q: "tab=ask", wait: ".ask" },
  { id: "work", q: "tab=work", wait: ".board-page" },
  { id: "agents-roster", q: "tab=agents", wait: "[aria-label='Agents'], .agents-deck", rosterView: "list" },
  { id: "agents-graph", q: "tab=agents", wait: ".workflow-graph", graph: true, rosterView: "workflow" },
];
const WIDTHS = [{ tag: "desktop", w: 1440 }, { tag: "mobile", w: 390 }];
const THEMES = ["dark", "light"];

async function shoot(browser, { surface, width, theme }) {
  const ctx = await browser.newContext({
    viewport: { width: width.w, height: 900 },
    colorScheme: theme,
    deviceScaleFactor: 1,
  });
  const p = await ctx.newPage();
  await p.addInitScript((seed) => {
    try {
      localStorage.setItem("alfred-theme-name", "mineral");
      localStorage.setItem("alfred-theme", seed.theme);
      localStorage.setItem("alfred.rosterView", seed.rosterView);
    } catch {}
  }, { theme, rosterView: surface.rosterView || "workflow" });
  await p.goto(`${BASE}/?${surface.q}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForTimeout(1600);
  try { await p.waitForSelector(surface.wait, { timeout: 5000, state: "attached" }); } catch {}
  await p.waitForTimeout(surface.graph ? 1400 : 400);
  await p.evaluate(SANITIZE);
  await p.waitForTimeout(150);
  const name = `${surface.id}_${width.tag}_${theme}.png`;
  if (surface.graph && width.tag === "desktop") {
    const el = await p.$(".workflow-graph");
    if (el) { await el.scrollIntoViewIfNeeded(); await p.waitForTimeout(500); await el.screenshot({ path: join(OUT, name) }); }
    else await p.screenshot({ path: join(OUT, name) });
  } else {
    await p.screenshot({ path: join(OUT, name), fullPage: false });
  }
  await ctx.close();
  return name;
}

async function run() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch({ args: ["--disable-gpu"] });
  const done = [];
  try {
    for (const theme of THEMES)
      for (const width of WIDTHS)
        for (const surface of SURFACES) {
          if (surface.graph && width.tag === "mobile") continue; // graph is desktop-first
          done.push(await shoot(browser, { surface, width, theme }));
        }
  } finally { await browser.close().catch(() => {}); }
  console.log(`captured ${done.length} frames -> ${OUT}`);
  for (const d of done) console.log("  " + d);
}
run().catch((e) => { console.error(e); process.exit(1); });
