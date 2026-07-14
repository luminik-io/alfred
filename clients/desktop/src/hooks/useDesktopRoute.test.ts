import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { desktopRouteToSearch, parseDesktopRoute, useDesktopRoute } from "./useDesktopRoute";

function loc(search: string, hash = "") {
  return { search, hash } as Location;
}

describe("desktop route parsing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("maps product tab names to internal surfaces", () => {
    expect(parseDesktopRoute(loc("?tab=inbox")).tab).toBe("home");
    expect(parseDesktopRoute(loc("?tab=ask")).tab).toBe("compose");
    expect(parseDesktopRoute(loc("?tab=work")).tab).toBe("pipeline");
    expect(parseDesktopRoute(loc("?tab=settings")).tab).toBe("settings");
  });

  it("keeps lessons and activity as Agents subtabs", () => {
    expect(parseDesktopRoute(loc("?tab=agents&subtab=activity"))).toMatchObject({
      tab: "fleet",
      fleetTab: "logs",
    });
    expect(parseDesktopRoute(loc("?tab=agents&subtab=lessons"))).toMatchObject({
      tab: "fleet",
      fleetTab: "lessons",
    });
  });

  it("falls back to Inbox for removed routes", () => {
    expect(parseDesktopRoute(loc("?tab=setup"))).toMatchObject({ tab: "home" });
    expect(parseDesktopRoute(loc("?tab=pipeline"))).toMatchObject({ tab: "home" });
  });

  it("writes canonical product-facing search params", () => {
    expect(desktopRouteToSearch({ tab: "home", fleetTab: "fleet" })).toBe("?tab=inbox");
    expect(desktopRouteToSearch({ tab: "pipeline", fleetTab: "fleet" })).toBe("?tab=work");
    expect(desktopRouteToSearch({ tab: "fleet", fleetTab: "lessons" })).toBe(
      "?tab=agents&subtab=lessons",
    );
    expect(desktopRouteToSearch({ tab: "settings", fleetTab: "fleet" })).toBe(
      "?tab=settings",
    );
  });

  it("canonicalizes the default route without dropping unrelated query params", async () => {
    window.history.replaceState(null, "", "/?debug=1&tab=removed&token=abc");
    const replaceState = vi.spyOn(window.history, "replaceState");

    renderHook(() => useDesktopRoute());

    await waitFor(() => {
      const params = new URLSearchParams(window.location.search);
      expect(params.get("debug")).toBe("1");
      expect(params.get("token")).toBe("abc");
      expect(params.get("tab")).toBe("inbox");
      expect(params.get("subtab")).toBeNull();
    });
    expect(replaceState).toHaveBeenCalled();
  });

  it("pushes user-initiated tab changes and maps Lessons into Agents", async () => {
    window.history.replaceState(null, "", "/?tab=inbox");
    const pushState = vi.spyOn(window.history, "pushState");
    const replaceState = vi.spyOn(window.history, "replaceState");
    const { result } = renderHook(() => useDesktopRoute());

    act(() => {
      result.current.setTab("lessons");
    });

    await waitFor(() => {
      expect(result.current.tab).toBe("fleet");
      expect(result.current.fleetTab).toBe("lessons");
      expect(new URLSearchParams(window.location.search).get("tab")).toBe("agents");
      expect(new URLSearchParams(window.location.search).get("subtab")).toBe("lessons");
    });
    expect(pushState).toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalledWith(null, "", "/?tab=agents&subtab=lessons");
  });

  it("resyncs from popstate without writing a new history entry", async () => {
    window.history.replaceState(null, "", "/?tab=inbox");
    const pushState = vi.spyOn(window.history, "pushState");
    const replaceState = vi.spyOn(window.history, "replaceState");
    const { result } = renderHook(() => useDesktopRoute());

    window.history.replaceState(null, "", "/?tab=agents&subtab=activity");
    pushState.mockClear();
    replaceState.mockClear();
    act(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    await waitFor(() => {
      expect(result.current.tab).toBe("fleet");
      expect(result.current.fleetTab).toBe("logs");
      expect(new URLSearchParams(window.location.search).get("tab")).toBe("agents");
      expect(new URLSearchParams(window.location.search).get("subtab")).toBe("activity");
    });
    expect(pushState).not.toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalled();
  });
});
