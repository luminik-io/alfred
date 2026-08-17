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
// The base :root block is Prism Light (the default), so it doubles as the
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

function lastBlockBody(css: string, selectorHead: string): string {
  const start = css.lastIndexOf(selectorHead);
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

const THEME_PRIMITIVES = [
  "--theme-background",
  "--theme-foreground",
  "--theme-surface",
  "--theme-surface-2",
  "--theme-surface-3",
  "--theme-primary",
  "--theme-primary-foreground",
  "--theme-muted-foreground",
  "--theme-border",
  "--theme-border-strong",
  "--theme-glass",
  "--theme-glass-strong",
  "--theme-glass-highlight",
  "--theme-glass-shadow",
  "--theme-ok",
  "--theme-warn",
  "--theme-error",
  "--theme-sidebar",
  "--theme-sidebar-accent",
  "--signal-mint",
  "--signal-rose",
  "--signal-violet",
  "--fold-line",
  "--theme-glass-blur",
  "--theme-glass-saturate",
  "--theme-radius",
];

const css = readIndexCss();
const baseTokens = declaredTokens(blockBody(css, ":root {"));
const radixVariants = readFileSync(
  resolve(srcDir, "styles/radix-variants.css"),
  "utf8",
);
const atmosphereStyles = ["base.css", "onboarding.css", "shell.css"]
  .map((file) => readFileSync(resolve(srcDir, "styles", file), "utf8"))
  .join("\n");
const foregroundVariants = [
  readFileSync(resolve(srcDir, "components/ui/button.tsx"), "utf8"),
  readFileSync(resolve(srcDir, "components/ui/badge.tsx"), "utf8"),
].join("\n");
const visualSweepScripts = [
  "design-shots.mjs",
  "enterprise-shots.mjs",
  "onboarding-sweep.mjs",
  "pixel-sweep.mjs",
].map((file) => ({
  file,
  source: readFileSync(resolve(srcDir, "..", "scripts", file), "utf8"),
}));

// The color tokens the base defines (the canonical required set).
const requiredColorTokens = THEME_PRIMITIVES;

const THEME_BLOCKS: Array<{ name: string; selector: string }> = [
  {
    name: "Prism Dark",
    selector: ':root[data-theme="signal-edge"].dark {',
  },
  {
    name: "Graphite Light",
    selector: ':root[data-theme="category-standard"].light {',
  },
  {
    name: "Graphite Dark",
    selector: ':root[data-theme="category-standard"].dark {',
  },
  {
    name: "Ledger Light",
    selector: ':root[data-theme="linked-fold"].light {',
  },
  {
    name: "Ledger Dark",
    selector: ':root[data-theme="linked-fold"].dark {',
  },
];

describe("theme token completeness (do not revert)", () => {
  it("base :root defines a non-trivial color token set", () => {
    // Sanity: the reference set should be large; a tiny set means the base block
    // was gutted and every other assertion would pass vacuously.
    expect(requiredColorTokens.length).toBeGreaterThan(20);
    expect(
      requiredColorTokens.filter((token) => !baseTokens.has(token)),
    ).toEqual([]);
  });

  for (const block of THEME_BLOCKS) {
    it(`${block.name} defines every base color token`, () => {
      const body = blockBody(css, block.selector);
      const tokens = declaredTokens(body);
      const missing = requiredColorTokens.filter((token) => !tokens.has(token));
      expect(missing).toEqual([]);
    });
  }

  it("each theme and mode sets its own glass material", () => {
    expect(baseTokens.has("--theme-glass-blur")).toBe(true);
    for (const block of THEME_BLOCKS) {
      expect(
        declaredTokens(blockBody(css, block.selector)).has(
          "--theme-glass-blur",
        ),
      ).toBe(true);
    }
  });

  it("keeps Prism light as the warm-neutral default with spectral edges", () => {
    const body = blockBody(css, ":root {");
    expect(body).toMatch(/color-scheme:\s*light/);
    expect(body).toMatch(/--background:\s*oklch\(0\.975/);
    expect(body).toMatch(/--signal-mint:/);
    expect(body).toMatch(/--signal-rose:/);
    expect(body).toMatch(/--signal-violet:/);
  });

  it("removes the legacy Mineral and Carbon selectors", () => {
    expect(css).not.toContain('data-theme="mineral"');
    expect(css).not.toContain('data-theme="carbon"');
  });

  it("keeps Ledger dark on warm graphite instead of a brown field", () => {
    const body = lastBlockBody(css, ':root[data-theme="linked-fold"].dark {');

    expect(body).toMatch(/--theme-background:\s*oklch\(0\.155 0\.008 55\)/);
    expect(body).toMatch(/--theme-surface:\s*oklch\(0\.205 0\.010 55/);
    expect(body).toMatch(/--theme-surface-2:\s*oklch\(0\.235 0\.012 55/);
    expect(body).toMatch(/--theme-surface-3:\s*oklch\(0\.275 0\.014 55/);
  });

  it("sweeps every shipped theme and no retired theme", () => {
    for (const script of visualSweepScripts) {
      expect(script.source, script.file).toContain("signal-edge");
      expect(script.source, script.file).toContain("category-standard");
      expect(script.source, script.file).toContain("linked-fold");
      expect(script.source, script.file).not.toContain('"mineral"');
      expect(script.source, script.file).not.toContain('"carbon"');
    }
  });

  it("does not use decorative radial blooms in app chrome", () => {
    expect(atmosphereStyles).not.toContain("radial-gradient(");
  });

  it("keeps light accent colors out of white-text fills", () => {
    expect(foregroundVariants).not.toContain("var(--accent)");
    expect(foregroundVariants).toContain(
      "color-mix(in_oklch,var(--primary),black_18%)",
    );
  });
});

describe("Radix state variants", () => {
  it("matches both explicit and bare selected attributes", () => {
    expect(radixVariants).toContain('&:where([data-selected="true"]),');
    expect(radixVariants).toContain(
      '&:where([data-selected]:not([data-selected="false"]))',
    );
  });
});
