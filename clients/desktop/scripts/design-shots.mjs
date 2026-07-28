// Headless screenshot harness for the design overhaul.
// Captures every primary screen at desktop + mobile, dark + light.
// Usage: node scripts/design-shots.mjs <baseUrl> <outDir> <phase>
//   e.g. node scripts/design-shots.mjs http://localhost:5310 .design-review/overhaul before
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.argv[2] || "http://localhost:5310";
const OUT = process.argv[3] || ".design-review/overhaul";
const PHASE = process.argv[4] || "before";

// Canonical route search string -> friendly name.
const SCREENS = [
  { name: "inbox", search: "?tab=inbox" },
  { name: "ask", search: "?tab=ask" },
  { name: "work", search: "?tab=work" },
  { name: "agents-roster", search: "?tab=agents" },
  { name: "agents-activity", search: "?tab=agents&subtab=activity" },
  { name: "agents-learnings", search: "?tab=agents&subtab=lessons" },
  { name: "settings", search: "?tab=settings" },
];

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];
const MODES = ["dark", "light"];

mkdirSync(`${OUT}/${PHASE}`, { recursive: true });

const browser = await chromium.launch();
for (const vp of VIEWPORTS) {
  for (const mode of MODES) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 2,
    });
    const page = await context.newPage();
    // Seed storage before app boots so it connects to live serve + right theme.
    await page.addInitScript(
      ([m]) => {
        try {
          localStorage.setItem("alfred-desktop.base-url", "http://127.0.0.1:7010");
          localStorage.setItem("alfred-theme-name", "mineral");
          localStorage.setItem("alfred-theme", m);
        } catch {}
      },
      [mode]
    );
    for (const scr of SCREENS) {
      const url = `${BASE}/${scr.search}`;
      await page.goto(url, { waitUntil: "networkidle" }).catch(() => {});
      // give charts / react-flow / streams a beat to settle
      await page.waitForTimeout(1400);
      const file = `${OUT}/${PHASE}/${scr.name}--${vp.name}--${mode}.png`;
      await page.screenshot({ path: file }).catch((e) => console.error("shot fail", file, e.message));
      console.log("shot", file);
    }
    await context.close();
  }
}
await browser.close();
console.log("done", PHASE);
