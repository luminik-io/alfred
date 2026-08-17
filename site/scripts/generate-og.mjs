import { execFile as execFileCallback } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { promisify } from "node:util";
import sharp from "sharp";

const execFile = promisify(execFileCallback);

const root = resolve(import.meta.dirname, "..");
const logoPath = resolve(root, "src/assets/alfred-logo-transparent.png");
const outPath = resolve(root, "public/brand/alfred-og.png");
const positioning = "An autonomous engineering team that ships while you're away.";
const publicMediaMode = "light";

// Fonts are inlined as base64 woff2 so the Chrome headless render does not
// depend on network access.
const instrumentSans = resolve(
  root,
  "node_modules/@fontsource-variable/instrument-sans/files/instrument-sans-latin-wght-normal.woff2",
);
const quicksand = resolve(
  root,
  "node_modules/@fontsource-variable/quicksand/files/quicksand-latin-wght-normal.woff2",
);
const fragmentMono = resolve(
  root,
  "node_modules/@fontsource/fragment-mono/files/fragment-mono-latin-400-normal.woff2",
);

async function fontFace(family, weight, file, opts = {}) {
  const buf = await readFile(file);
  const variation = opts.variation ? `font-stretch:75% 125%;` : "";
  return `@font-face{font-family:${family};font-style:normal;font-weight:${weight};${variation}font-display:block;src:url(data:font/woff2;base64,${buf.toString(
    "base64",
  )}) format("woff2");}`;
}

async function imageData(path) {
  const image = await readFile(path);
  return `data:image/png;base64,${image.toString("base64")}`;
}

async function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);

  for (const candidate of candidates) {
    try {
      await execFile(candidate, ["--version"]);
      return candidate;
    } catch {
      // keep looking
    }
  }

  return null;
}

function html({ fontCss, logoData }) {
  return `<!doctype html>
<html data-theme="${publicMediaMode}">
<head>
  <meta charset="utf-8" />
  <style>
    ${fontCss}
    * { box-sizing: border-box; }
    body {
      width: 1200px;
      height: 630px;
      margin: 0;
      overflow: hidden;
      background: #F7F9FC;
      color: #44506B;
      font-family: "Instrument Sans Var", "Quicksand Var", Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }
    .canvas {
      position: relative;
      width: 1200px;
      height: 630px;
      padding: 64px 72px 56px;
      background:
        radial-gradient(ellipse 760px 460px at 8% 0%, rgba(40, 85, 200, 0.12), transparent 64%),
        radial-gradient(ellipse 700px 460px at 92% 100%, rgba(8, 122, 93, 0.08), transparent 64%),
        linear-gradient(160deg, #FFFFFF 0%, #F7F9FC 54%, #EEF3FA 100%);
    }
    .canvas::before {
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(116deg, transparent 0 68%, rgba(40, 85, 200, 0.06) 68% 69%, transparent 69%),
        linear-gradient(116deg, transparent 0 76%, rgba(8, 122, 93, 0.05) 76% 77%, transparent 77%);
    }
    .content {
      position: relative;
      z-index: 1;
      height: 100%;
      display: flex;
      flex-direction: column;
    }
    .top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .brand img {
      width: 42px;
      height: 42px;
      object-fit: contain;
    }
    .brand .wordmark {
      font-size: 30px;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #0D1322;
    }
    .meta {
      font-family: "Fragment Mono", ui-monospace, monospace;
      font-size: 14px;
      font-weight: 500;
      letter-spacing: 0.14em;
      color: #66718C;
      text-transform: uppercase;
    }
    .meta .accent { color: #087A5D; }
    .hero {
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
      margin-top: -18px;
      max-width: 940px;
    }
    .eyebrow {
      font-family: "Fragment Mono", ui-monospace, monospace;
      font-size: 13px;
      font-weight: 600;
      letter-spacing: 0.20em;
      color: #2855C8;
      text-transform: uppercase;
      margin-bottom: 16px;
    }
    h1 {
      margin: 0;
      font-size: 68px;
      line-height: 1.06;
      font-weight: 700;
      letter-spacing: -2px;
      color: #0D1322;
    }
    h1 .away {
      color: #2855C8;
    }
    .sub {
      margin: 26px 0 0;
      max-width: 840px;
      color: #44506B;
      font-family: "Quicksand Var", "Instrument Sans Var", Arial, sans-serif;
      font-size: 22px;
      line-height: 1.4;
      font-weight: 500;
      letter-spacing: -0.1px;
    }
    .stripe {
      display: flex;
      align-items: center;
      gap: 18px;
      padding-top: 22px;
      font-family: "Fragment Mono", ui-monospace, monospace;
      font-size: 13px;
      color: #66718C;
      letter-spacing: 0.04em;
    }
    .stripe .dot { color: #087A5D; }
    .stripe .dot.warn { color: #8F5600; }
    .stripe .sep { color: #D4DBE9; }
    .footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      color: #66718C;
      font-family: "Fragment Mono", ui-monospace, monospace;
      font-size: 14px;
      letter-spacing: 0.04em;
    }
    .footer .url { color: #1F2A44; }
  </style>
</head>
<body>
  <main class="canvas">
    <div class="content">
      <div class="top">
        <div class="brand">
          <img src="${logoData}" alt="" />
          <span class="wordmark">Alfred</span>
        </div>
        <div class="meta"><span class="accent">●</span> live · open source · mit</div>
      </div>
      <div class="hero">
        <div class="eyebrow">specs in, PRs out</div>
        <h1 aria-label="${positioning}">An autonomous engineering<br />team that ships<br /><span class="away">while you're away.</span></h1>
        <p class="sub">Alfred turns Claude Code and Codex into a spec-driven engineering team. Alfred keeps working without you at the keyboard. You approve risky actions and decide what merges.</p>
        <div class="stripe">
          <span><span class="dot">●</span> self-hosted</span>
          <span class="sep">·</span>
          <span><span class="dot">●</span> claude code + codex</span>
          <span class="sep">·</span>
          <span><span class="dot">●</span> github-native</span>
          <span class="sep">·</span>
          <span><span class="dot warn">●</span> human review at every PR</span>
        </div>
      </div>
      <div class="footer">
        <div class="url">alfred.luminik.io</div>
        <div>luminik-io/alfred</div>
      </div>
    </div>
  </main>
</body>
</html>`;
}

async function renderWithChrome(htmlText) {
  const chrome = await findChrome();
  if (!chrome) return false;

  const tempDir = await mkdtemp(resolve(tmpdir(), "alfred-og-"));
  const htmlPath = resolve(tempDir, "index.html");
  const screenshotPath = resolve(tempDir, "alfred-og.png");

  try {
    await writeFile(htmlPath, htmlText);
    await execFile(chrome, [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--no-first-run",
      "--no-default-browser-check",
      "--virtual-time-budget=1500",
      "--force-device-scale-factor=2",
      "--window-size=1200,630",
      `--screenshot=${screenshotPath}`,
      `file://${htmlPath}`,
    ]);

    await mkdir(dirname(outPath), { recursive: true });
    await sharp(screenshotPath).resize(1200, 630).png({ compressionLevel: 9 }).toFile(outPath);
    return true;
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

async function renderWithSharp(htmlText) {
  const escaped = htmlText
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  const fallbackSvg = `<?xml version="1.0" encoding="UTF-8"?>
<svg width="1200" height="630" viewBox="0 0 1200 630" xmlns="http://www.w3.org/2000/svg">
  <rect width="1200" height="630" fill="#F7F9FC"/>
  <foreignObject width="1200" height="630">
    ${escaped}
  </foreignObject>
</svg>`;

  await mkdir(dirname(outPath), { recursive: true });
  await sharp(Buffer.from(fallbackSvg)).png().toFile(outPath);
}

const fontCss = [
  await fontFace('"Instrument Sans Var"', 700, instrumentSans, { variation: true }),
  await fontFace('"Quicksand Var"', 500, quicksand, { variation: true }),
  await fontFace('"Fragment Mono"', "100 900", fragmentMono),
].join("");
const logoData = await imageData(logoPath);
const htmlText = html({ fontCss, logoData });
const rendered = await renderWithChrome(htmlText);

if (!rendered) {
  await renderWithSharp(htmlText);
}

console.log(`wrote ${outPath}`);
