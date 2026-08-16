import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useTheme } from "./useTheme";

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
    delete document.documentElement.dataset.theme;
  });

  it("ignores removed palette identifiers and starts with Mineral", async () => {
    localStorage.setItem("alfred-theme-name", "alfred");

    const { result } = renderHook(() => useTheme());

    expect(result.current.themeName).toBe("mineral");
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("mineral");
    });
  });

  it("applies and persists Carbon independently from the mode", async () => {
    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.setThemeName("carbon");
      result.current.setMode("light");
    });

    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("carbon");
      expect(document.documentElement).toHaveClass("light");
    });
    expect(localStorage.getItem("alfred-theme-name")).toBe("carbon");
    expect(localStorage.getItem("alfred-theme")).toBe("light");
  });
});
