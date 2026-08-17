import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const html = readFileSync(resolve(__dirname, "../../index.html"), "utf8");
const bootstrap = readFileSync(
  resolve(__dirname, "../../public/theme-bootstrap.js"),
  "utf8",
);

describe("theme bootstrap", () => {
  it("applies a saved appearance before the boot screen can paint", () => {
    const scriptStart = html.indexOf(
      '<script id="alfred-theme-bootstrap" src="/theme-bootstrap.js"></script>',
    );
    const styleStart = html.indexOf("<style>");

    expect(scriptStart).toBeGreaterThan(0);
    expect(scriptStart).toBeLessThan(styleStart);
    expect(bootstrap).toContain('localStorage.getItem("alfred-theme-name")');
    expect(bootstrap).toContain('localStorage.getItem("alfred-theme")');
    expect(bootstrap).toContain("root.dataset.theme = theme");
    expect(bootstrap).toContain(
      'root.classList.add(mode === "dark" ? "dark" : "light")',
    );
  });
});
