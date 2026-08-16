import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { THEME_META, THEME_NAMES, useTheme } from "./useTheme";

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
    delete document.documentElement.dataset.theme;
  });

  it("rejects legacy palette identifiers and starts with Signal Edge light", async () => {
    localStorage.setItem("alfred-theme-name", "mineral");

    const { result } = renderHook(() => useTheme());

    expect(result.current.themeName).toBe("signal-edge");
    expect(result.current.mode).toBe("light");
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("signal-edge");
      expect(document.documentElement).toHaveClass("light");
    });
  });

  it("exposes only the three approved themes in ranked order", () => {
    expect(THEME_NAMES).toEqual([
      "signal-edge",
      "category-standard",
      "linked-fold",
    ]);
    expect(Object.keys(THEME_META)).toEqual(THEME_NAMES);
  });

  it("applies and persists Linked Fold independently from the mode", async () => {
    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.setThemeName("linked-fold");
      result.current.setMode("dark");
    });

    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("linked-fold");
      expect(document.documentElement).toHaveClass("dark");
    });
    expect(localStorage.getItem("alfred-theme-name")).toBe("linked-fold");
    expect(localStorage.getItem("alfred-theme")).toBe("dark");
  });
});
