import { describe, expect, it } from "vitest";

import { assertEntryBudget } from "../../scripts/check-entry-budget.mjs";

describe("Desktop entry bundle budget", () => {
  it("accepts an entry bundle inside both byte limits", () => {
    expect(() =>
      assertEntryBudget({ rawBytes: 500_000, gzipBytes: 150_000 }),
    ).not.toThrow();
  });

  it("rejects a raw entry bundle regression", () => {
    expect(() =>
      assertEntryBudget({ rawBytes: 550_001, gzipBytes: 150_000 }),
    ).toThrow(/raw entry bundle.*550,001.*550,000/i);
  });

  it("rejects a compressed entry bundle regression", () => {
    expect(() =>
      assertEntryBudget({ rawBytes: 500_000, gzipBytes: 170_001 }),
    ).toThrow(/gzip entry bundle.*170,001.*170,000/i);
  });
});
