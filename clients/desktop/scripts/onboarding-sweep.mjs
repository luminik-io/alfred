// Onboarding-stepper visual + invariant sweep. Walks every first-run step across
// named palettes, light/dark modes, and verification widths.
//
//   node scripts/onboarding-sweep.mjs
//   SWEEP_BASE=http://localhost:5294 node scripts/onboarding-sweep.mjs
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, "..", ".onboarding-sweep");
const BASE = process.env.SWEEP_BASE || "http://localhost:5294";
const THEME_NAME_STORAGE_KEY = "alfred-theme-name";
const THEME_MODE_STORAGE_KEY = "alfred-theme";

function parseWidths(value) {
  const widths = value.split(",").map((entry) => Number(entry.trim()));
  if (widths.length === 0 || widths.some((width) => !Number.isInteger(width) || width <= 0)) {
    throw new Error(`SWEEP_WIDTHS must be a comma-separated list of positive integers: ${value}`);
  }
  return [...new Set(widths)];
}

function parsePalettes(value) {
  const supported = new Set(["mineral", "carbon"]);
  const palettes = value.split(",").map((entry) => entry.trim());
  const invalid = palettes.filter((palette) => !supported.has(palette));
  if (palettes.length === 0 || invalid.length > 0) {
    throw new Error(`SWEEP_PALETTES must contain only mineral or carbon: ${value}`);
  }
  return [...new Set(palettes)];
}

const WIDTHS = parseWidths(process.env.SWEEP_WIDTHS || "375,390,768,1024,1280,1680");
const PALETTES = parsePalettes(process.env.SWEEP_PALETTES || "mineral,carbon");
const MODES = ["dark", "light"];
const HEIGHT = 900;
const STEPS = [
  "welcome",
  "engine",
  "github",
  "repos",
  "batteries",
  "team",
  "slack",
  "request",
];
const PROBE = () => {
  const out = [];
  const srOnly = (el) => el.closest(".sr-only, .visually-hidden, [data-sr-only]") !== null;
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const label = (el) => {
    const cls =
      typeof el.className === "string"
        ? el.className.split(/\s+/).filter(Boolean).slice(0, 3).join(".")
        : "";
    const aria = el.getAttribute("aria-label");
    return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}${aria ? `[aria-label="${aria}"]` : ""}`;
  };

  const docOverflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  if (docOverflow > 1) out.push({ kind: "doc-hscroll", detail: `+${docOverflow}px` });
  const bodyOverflow = document.body.scrollWidth - window.innerWidth;
  if (bodyOverflow > 1) out.push({ kind: "body-hscroll", detail: `+${bodyOverflow}px` });

  const chromeSel = [
    ".alfred-onboarding-shell",
    ".alfred-stepper",
    ".alfred-stepper__track",
    ".alfred-step",
    "[data-slot='card']",
    "header",
    "footer",
  ];
  for (const sel of chromeSel) {
    for (const el of document.querySelectorAll(sel)) {
      if (!vis(el)) continue;
      const s = getComputedStyle(el);
      const scrolls = /(auto|scroll)/.test(s.overflowX);
      const over = el.scrollWidth - el.clientWidth;
      if (over > 1 && !scrolls) {
        out.push({ kind: "chrome-overflow", detail: `${label(el)} +${over}px overflow-x:${s.overflowX}` });
      }
    }
  }

  const textSel = "h1,h2,h3,h4,strong,span,p,small,a,button,label,li";
  for (const el of document.querySelectorAll(textSel)) {
    if (!vis(el) || srOnly(el)) continue;
    const txt = (el.textContent || "").trim();
    if (!txt) continue;
    if (el.querySelector("h1,h2,h3,h4,p,div,ul,ol,section,article")) continue;
    const s = getComputedStyle(el);
    let lh = parseFloat(s.lineHeight);
    if (Number.isNaN(lh)) lh = parseFloat(s.fontSize) * 1.2;
    const r = el.getBoundingClientRect();
    const clipsY =
      /(hidden|clip)/.test(s.overflowY) ||
      s.webkitLineClamp !== "none" ||
      s.display === "-webkit-box";
    if (clipsY && r.height > 0 && r.height < lh - 1.5) {
      out.push({ kind: "subline-clip", detail: `${label(el)} ${r.height.toFixed(0)}<${lh.toFixed(0)} "${txt.slice(0, 30)}"` });
    }
  }

  if (window.innerWidth <= 390) {
    const tapSel = "button, a[href], [role='button'], input[type='checkbox'], input[type='radio']";
    for (const el of document.querySelectorAll(tapSel)) {
      if (!vis(el) || el.closest("[aria-hidden='true']")) continue;
      const r = el.getBoundingClientRect();
      const min = Math.min(r.width, r.height);
      if (min > 0 && min < 36) {
        const t = (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24);
        out.push({ kind: "small-tap", detail: `${label(el)} ${r.width.toFixed(0)}x${r.height.toFixed(0)} "${t}"` });
      }
    }
  }
  return out;
};

async function applyTheme(page, palette, mode) {
  await page.evaluate(
    ({ paletteName, themeMode, nameKey, modeKey }) => {
      const root = document.documentElement;
      root.classList.toggle("dark", themeMode === "dark");
      root.classList.toggle("light", themeMode === "light");
      root.setAttribute("data-theme", paletteName);
      try {
        localStorage.setItem(nameKey, paletteName);
        localStorage.setItem(modeKey, themeMode);
      } catch {}
    },
    {
      paletteName: palette,
      themeMode: mode,
      nameKey: THEME_NAME_STORAGE_KEY,
      modeKey: THEME_MODE_STORAGE_KEY,
    },
  );
}

async function gotoStep(page, step) {
  const btn = page.locator(`[data-onboarding-step="${step}"]`).first();
  if (await btn.isDisabled()) {
    await page.getByRole("button", { name: /^I have a server running$/i }).click();
  }
  await btn.click({ timeout: 2000 });
  await page.waitForTimeout(250);
}

async function run() {
  await mkdir(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const results = [];
  let total = 0;

  for (const palette of PALETTES) {
    for (const mode of MODES) {
      for (const width of WIDTHS) {
        const context = await browser.newContext({
          viewport: { width, height: HEIGHT },
          deviceScaleFactor: 1,
          colorScheme: mode,
        });
        const page = await context.newPage();
        const runtimeViolations = [];
        let runtimeViolationCursor = 0;
        page.on("console", (message) => {
          if (message.type() === "error" || message.type() === "warning") {
            runtimeViolations.push({
              kind: `console-${message.type()}`,
              detail: message.text().slice(0, 240),
            });
          }
        });
        page.on("pageerror", (error) => {
          runtimeViolations.push({
            kind: "page-error",
            detail: error.message.slice(0, 240),
          });
        });
        // Keep the developer installation intact while deterministically
        // exercising first run. Preserve the live inventory response and only
        // lower the canonical boot gate for this browser context.
        await page.route("**/alfred-api/api/setup/status", async (route) => {
          const response = await route.fetch();
          const status = await response.json();
          status.first_run = { ...status.first_run, ready: false };
          await route.fulfill({ response, json: status });
        });
        await page.addInitScript(
          ({ paletteName, themeMode, nameKey, modeKey }) => {
            try {
              localStorage.setItem(nameKey, paletteName);
              localStorage.setItem(modeKey, themeMode);
            } catch {}
          },
          {
            paletteName: palette,
            themeMode: mode,
            nameKey: THEME_NAME_STORAGE_KEY,
            modeKey: THEME_MODE_STORAGE_KEY,
          },
        );
        await page.goto(`${BASE}/?tab=settings`, {
          waitUntil: "domcontentloaded",
          timeout: 30000,
        });
        await applyTheme(page, palette, mode);
        await page.waitForTimeout(600);
        await page.waitForSelector(".alfred-onboarding-shell", { timeout: 5000 });

        for (const step of STEPS) {
          await gotoStep(page, step);
          const runtime = runtimeViolations.slice(runtimeViolationCursor);
          runtimeViolationCursor = runtimeViolations.length;
          const violations = [...(await page.evaluate(PROBE)), ...runtime];
          const shot = `${step}_${width}_${palette}_${mode}.png`;
          try {
            await page.screenshot({ path: join(OUT_DIR, shot), fullPage: false, timeout: 8000 });
          } catch {}
          total += violations.length;
          results.push({ step, width, palette, mode, violations });
        }
        await context.close();
      }
    }
  }
  await browser.close();

  const byKind = {};
  for (const r of results) {
    for (const v of r.violations) {
      (byKind[v.kind] = byKind[v.kind] || []).push(
        `[${r.step} ${r.width} ${r.palette} ${r.mode}] ${v.detail}`,
      );
    }
  }
  console.log(`\n=== ONBOARDING SWEEP: ${results.length} step renders, ${total} violations ===\n`);
  const kinds = Object.keys(byKind).sort();
  if (!kinds.length) {
    console.log("CLEAN: no violations across all steps / widths / palettes / modes.");
  } else {
    for (const k of kinds) {
      console.log(`## ${k} (${byKind[k].length})`);
      for (const line of byKind[k]) console.log("  - " + line);
    }
  }
  console.log(`\nScreenshots: ${OUT_DIR}`);
  process.exit(total > 0 ? 1 : 0);
}

run().catch((e) => {
  console.error("SWEEP ERROR:", e);
  process.exit(1);
});
