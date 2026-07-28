// Pixel-sweep audit harness. Loads every primary surface across named palettes,
// light/dark modes, and five widths, then asserts hard layout invariants.
//
//   node scripts/pixel-sweep.mjs
//   node scripts/pixel-sweep.mjs --quiet
//
// Dev-only QA tool. It is not part of the shipped client.
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, "..", ".pixel-sweep");
const BASE = process.env.SWEEP_BASE || "http://127.0.0.1:1420";
const QUIET = process.argv.includes("--quiet");

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

const WIDTHS = parseWidths(process.env.SWEEP_WIDTHS || "375,768,1024,1280,1680");
const PALETTES = parsePalettes(process.env.SWEEP_PALETTES || "mineral,carbon");
const MODES = ["dark", "light"];
const HEIGHT = 900;
const ROUTE_TIMEOUT_MS = parsePositiveInt(process.env.SWEEP_ROUTE_TIMEOUT_MS, 20000);
const NAVIGATION_ATTEMPTS = 2;
const SCREENSHOT_TIMEOUT_MS = 8000;
// The outer watchdog covers two possible navigations, the independent surface
// readiness wait, the screenshot, and short settle delays. It must never consume
// the same budget that an inner operation was promised.
const RENDER_TIMEOUT_MS =
  ROUTE_TIMEOUT_MS * (NAVIGATION_ATTEMPTS + 1) + SCREENSHOT_TIMEOUT_MS + 3000;
const THEME_NAME_STORAGE_KEY = "alfred-theme-name";
const THEME_MODE_STORAGE_KEY = "alfred-theme";

const ROUTES = [
  { id: "home", q: "tab=inbox", ready: ".command-center" },
  { id: "ask", q: "tab=ask", ready: ".ask" },
  { id: "pipeline", q: "tab=work", ready: ".board-page" },
  { id: "fleet-roster", q: "tab=agents", ready: "[aria-label='Agents']" },
  { id: "fleet-activity", q: "tab=agents&subtab=activity", ready: "[aria-label='Agents']" },
  { id: "lessons", q: "tab=agents&subtab=lessons", ready: "[aria-label='Lessons']" },
  { id: "settings", q: "tab=settings", ready: '.settings-view[data-ready="true"]' },
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
    return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${cls ? "." + cls : ""}${aria ? `[aria-label="${aria}"]` : ""}`;
  };

  const docOverflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  if (docOverflow > 1) {
    out.push({
      kind: "doc-hscroll",
      detail: `documentElement scrollWidth ${document.documentElement.scrollWidth} > clientWidth ${document.documentElement.clientWidth} (+${docOverflow}px)`,
    });
  }

  const bodyOverflow = document.body.scrollWidth - window.innerWidth;
  if (bodyOverflow > 1) {
    out.push({
      kind: "body-hscroll",
      detail: `body scrollWidth ${document.body.scrollWidth} > innerWidth ${window.innerWidth} (+${bodyOverflow}px)`,
    });
  }

  const chromeSel = [
    "header",
    "[role='tablist']",
    ".command-center__pane-head",
    ".command-center__top",
    ".alfred-pipeline__column-head",
    ".panel-header",
    ".ask__head",
    ".request-thread__head",
    ".alfred-card",
    ".attention-card",
    "[data-slot='card']",
  ];
  for (const sel of chromeSel) {
    for (const el of document.querySelectorAll(sel)) {
      if (!vis(el)) continue;
      const s = getComputedStyle(el);
      const scrolls = /(auto|scroll)/.test(s.overflowX);
      const over = el.scrollWidth - el.clientWidth;
      if (over > 1 && !scrolls) {
        out.push({
          kind: "chrome-overflow",
          detail: `${label(el)} scrollWidth ${el.scrollWidth} > clientWidth ${el.clientWidth} (+${over}px), overflow-x:${s.overflowX}`,
        });
      }
    }
  }

  const textSel = "h1,h2,h3,h4,strong,span,p,small,a,button,dd,dt,li,label,td,th";
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
      out.push({
        kind: "subline-clip",
        detail: `${label(el)} height ${r.height.toFixed(1)}px < line-height ${lh.toFixed(1)}px text="${txt.slice(0, 40)}"`,
      });
    }
  }

  if (window.innerWidth <= 375) {
    const tapSel =
      "button, a[href], [role='button'], [role='tab'], input[type='checkbox'], input[type='radio'], [role='switch']";
    for (const el of document.querySelectorAll(tapSel)) {
      if (!vis(el) || el.closest("[aria-hidden='true']")) continue;
      const r = el.getBoundingClientRect();
      let h = r.height;
      let w = r.width;
      for (const pseudo of ["::after", "::before"]) {
        const ps = getComputedStyle(el, pseudo);
        if (ps.content === "none" || ps.position !== "absolute") continue;
        const top = parseFloat(ps.top);
        const bottom = parseFloat(ps.bottom);
        const left = parseFloat(ps.left);
        const right = parseFloat(ps.right);
        if (top < 0 || bottom < 0) {
          h = Math.max(
            h,
            r.height -
              (Number.isNaN(top) ? 0 : Math.min(0, top)) -
              (Number.isNaN(bottom) ? 0 : Math.min(0, bottom)),
          );
        }
        if (left < 0 || right < 0) {
          w = Math.max(
            w,
            r.width -
              (Number.isNaN(left) ? 0 : Math.min(0, left)) -
              (Number.isNaN(right) ? 0 : Math.min(0, right)),
          );
        }
      }
      const min = Math.min(w, h);
      if (min > 0 && min < 36) {
        const text = (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24);
        out.push({
          kind: "small-tap",
          detail: `${label(el)} ${r.width.toFixed(0)}x${r.height.toFixed(0)} eff ${w.toFixed(0)}x${h.toFixed(0)} (min ${min.toFixed(0)} < 36) text="${text}"`,
        });
      }
    }
  }

  return out;
};

function parsePositiveInt(value, fallback) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`SWEEP_ROUTE_TIMEOUT_MS must be a positive integer, got ${JSON.stringify(value)}`);
  }
  return parsed;
}

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

async function withTimeout(task, ms, label) {
  let timeoutId;
  const guardedTask = Promise.resolve(task);
  guardedTask.catch(() => {});
  const timeout = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  try {
    return await Promise.race([guardedTask, timeout]);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function renderRoute(browser, { palette, mode, width, route }) {
  const context = await browser.newContext({
    viewport: { width, height: HEIGHT },
    deviceScaleFactor: 1,
    colorScheme: mode,
  });

  try {
    const page = await context.newPage();
    const runtimeViolations = [];
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

    const url = `${BASE}/?${route.q}`;
    let navOk = false;
    for (let attempt = 0; attempt < NAVIGATION_ATTEMPTS && !navOk; attempt++) {
      try {
        await page.goto(url, { waitUntil: "domcontentloaded", timeout: ROUTE_TIMEOUT_MS });
        navOk = true;
      } catch (err) {
        if (attempt === NAVIGATION_ATTEMPTS - 1) throw err;
        await page.waitForTimeout(500);
      }
    }
    await applyTheme(page, palette, mode);
    await page.waitForTimeout(700);
    await page.waitForSelector(route.ready, {
      timeout: ROUTE_TIMEOUT_MS,
      state: "attached",
    });
    await page.waitForTimeout(300);

    const violations = [...(await page.evaluate(PROBE)), ...runtimeViolations];
    const shot = `${route.id}_${width}_${palette}_${mode}.png`;
    try {
      await page.screenshot({
        path: join(OUT_DIR, shot),
        fullPage: false,
        timeout: SCREENSHOT_TIMEOUT_MS,
      });
    } catch {}
    return { route: route.id, width, palette, mode, violations, shot };
  } finally {
    await context.close().catch(() => {});
  }
}

async function run() {
  await mkdir(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ args: ["--disable-gpu"] });
  const results = [];
  let total = 0;

  try {
    for (const palette of PALETTES) {
      for (const mode of MODES) {
        for (const width of WIDTHS) {
          for (const route of ROUTES) {
            const result = await withTimeout(
              renderRoute(browser, { palette, mode, width, route }),
              RENDER_TIMEOUT_MS,
              `${route.id} ${width} ${palette} ${mode}`,
            );
            total += result.violations.length;
            results.push(result);
          }
        }
      }
    }
  } finally {
    await browser.close().catch(() => {});
  }

  await writeFile(join(OUT_DIR, "report.json"), JSON.stringify(results, null, 2));

  const byKind = {};
  for (const r of results) {
    for (const v of r.violations) {
      const key = v.kind;
      byKind[key] = byKind[key] || [];
      byKind[key].push(`[${r.route} ${r.width} ${r.palette} ${r.mode}] ${v.detail}`);
    }
  }
  if (!QUIET) {
    console.log(`\n=== PIXEL SWEEP: ${results.length} surface renders, ${total} violations ===\n`);
  }
  const kinds = Object.keys(byKind).sort();
  if (kinds.length === 0) {
    console.log("CLEAN: no violations across all routes / widths / palettes / modes.");
  } else {
    for (const k of kinds) {
      console.log(`\n## ${k} (${byKind[k].length})`);
      for (const line of byKind[k]) console.log("  - " + line);
    }
  }
  console.log(`\nReport: ${join(OUT_DIR, "report.json")}`);
  process.exit(total > 0 ? 1 : 0);
}

run().catch((e) => {
  console.error("SWEEP ERROR:", e);
  process.exit(1);
});
