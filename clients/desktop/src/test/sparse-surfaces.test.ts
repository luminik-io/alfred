import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(__dirname, "..");
const rosterStyles = readFileSync(resolve(srcDir, "styles/roster.css"), "utf8");
const memoryStyles = readFileSync(resolve(srcDir, "styles/memory.css"), "utf8");

describe("Sparse Desktop surfaces", () => {
  it("wraps agent purpose copy instead of clipping it", () => {
    const purposeBlock = rosterStyles.match(
      /\.agents-deck__row-purpose \{([\s\S]*?)\n\}/,
    )?.[1];

    expect(purposeBlock).toContain("white-space: normal");
    expect(purposeBlock).toContain("overflow-wrap: anywhere");
    expect(purposeBlock).not.toContain("text-overflow: ellipsis");
  });

  it("keeps operational roster rows flat on hover", () => {
    const rowBlock = rosterStyles.match(
      /\.agents-deck__row \{([\s\S]*?)\n\}/,
    )?.[1];

    expect(rowBlock).not.toContain("transform 150ms ease");
    expect(rosterStyles).not.toMatch(
      /\.agents-deck__row:hover \{[\s\S]*?transform:/,
    );
  });

  it("renders lesson metadata as self-contained labels", () => {
    const detailsBlock = memoryStyles.match(
      /\.active-lesson__details > span \{([\s\S]*?)\n\}/,
    )?.[1];

    expect(detailsBlock).toContain("display: inline-flex");
    expect(detailsBlock).toContain("border: 1px solid");
    expect(memoryStyles).not.toContain(
      ".active-lesson__details > span + span::before",
    );
  });
});
