import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// The theme system (docs/THEME_SYSTEM.md) defines colors as CSS custom
// properties swapped by a data-theme attribute plus a .dark/.light mode class.
// Every theme + mode combination MUST define the complete token set, or a
// surface falls back to an undefined var() and renders unstyled. This guard
// reads index.css, treats the base :root block as the canonical token set, and
// fails CI if any theme block drops one of those tokens.
//
// The base :root block is Alfred Dark (the default), so it doubles as the
// reference set. Theme blocks may add tokens, but must never define fewer color
// tokens than the base.

// index.css is a thin manifest that @imports per-surface partials under
// src/styles/ (the theme token blocks live in styles/tokens.css). Read the
// whole desktop stylesheet so this guard finds the token blocks wherever a
// structural split places them. Partials are read in the manifest's own @import
// order, so first-match parsing inspects the exact cascade the app loads rather
// than filesystem iteration order.
const srcDir = resolve(__dirname, "..");

function readIndexCss(): string {
  const manifest = readFileSync(resolve(srcDir, "index.css"), "utf8");
  const parts = [manifest];
  for (const match of manifest.matchAll(/@import\s+"(\.\/[^"]+)"/g)) {
    parts.push(readFileSync(resolve(srcDir, match[1]), "utf8"));
  }
  return parts.join("\n");
}

// Extract the body of the first CSS block matching a selector head. Naive brace
// matching is enough here: token blocks contain no nested braces.
function blockBody(css: string, selectorHead: string): string {
  const start = css.indexOf(selectorHead);
  if (start === -1) {
    throw new Error(`could not find selector "${selectorHead}" in index.css`);
  }
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  if (open === -1 || close === -1) {
    throw new Error(`malformed block for "${selectorHead}" in index.css`);
  }
  return css.slice(open + 1, close);
}

// All --token names declared in a block body (left-hand sides only).
function declaredTokens(body: string): Set<string> {
  const names = new Set<string>();
  for (const match of body.matchAll(/(--[a-z0-9-]+)\s*:/gi)) {
    names.add(match[1]);
  }
  return names;
}

// Color tokens whose absence would visibly break a surface. Drawn from the base
// set; non-color structural tokens (radius, blur, saturate, ambient) are checked
// separately because a theme may legitimately inherit them from :root.
const COLOR_TOKEN_PREFIXES = [
  "--background",
  "--foreground",
  "--card",
  "--popover",
  "--primary",
  "--secondary",
  "--muted",
  "--accent",
  "--destructive",
  "--border",
  "--input",
  "--ring",
  "--surface",
  "--hairline",
  "--glass",
  "--ok",
  "--warn",
  "--error",
  "--sidebar",
];

const css = readIndexCss();
const baseTokens = declaredTokens(blockBody(css, ":root {"));

// The color tokens the base defines (the canonical required set).
const requiredColorTokens = [...baseTokens].filter((token) =>
  COLOR_TOKEN_PREFIXES.some((prefix) => token.startsWith(prefix)),
);

const THEME_BLOCKS: Array<{ name: string; selector: string }> = [
  { name: "Alfred Light", selector: ':root[data-theme="alfred"].light {' },
  { name: "Linear Crisp Dark", selector: ':root[data-theme="linear"],' },
  { name: "Linear Crisp Light", selector: ':root[data-theme="linear"].light {' },
];

describe("theme token completeness (do not revert)", () => {
  it("base :root defines a non-trivial color token set", () => {
    // Sanity: the reference set should be large; a tiny set means the base block
    // was gutted and every other assertion would pass vacuously.
    expect(requiredColorTokens.length).toBeGreaterThan(20);
  });

  for (const block of THEME_BLOCKS) {
    it(`${block.name} defines every base color token`, () => {
      const body = blockBody(css, block.selector);
      const tokens = declaredTokens(body);
      const missing = requiredColorTokens.filter((token) => !tokens.has(token));
      expect(missing).toEqual([]);
    });
  }

  it("each theme sets a glass-blur token so glass dials per theme", () => {
    // Alfred uses the base --glass-blur; Linear overrides it to near-flat. The
    // base and the Linear block must both declare it.
    expect(baseTokens.has("--glass-blur")).toBe(true);
    const linearBody = blockBody(css, ':root[data-theme="linear"],');
    expect(declaredTokens(linearBody).has("--glass-blur")).toBe(true);
  });

  it("defines the steel-violet (Giga-grade) signature accent on the Alfred default", () => {
    const body = blockBody(css, ":root {");
    // The operator-directed Alfred accent (2026-07 Giga-grade dark overhaul): a
    // saturated steel-violet (hue ~286) over a near-black cool-indigo floor, so
    // the accent emits light onto near-black. It lives on --accent / --ring
    // (active + focus glow); the filled --primary sits a touch deeper (L 0.58) so
    // white button text clears WCAG AA on the fill.
    expect(body).toMatch(/--accent:\s*oklch\(0\.68 0\.21 286\)/);
    expect(body).toMatch(/--ring:\s*oklch\(0\.68 0\.21 286\)/);
    // The accent-glow emission primitives (not a color swap) must be present.
    expect(body).toMatch(/--accent-glow:/);
    expect(body).toMatch(/--accent-glow-soft:/);
  });
});
