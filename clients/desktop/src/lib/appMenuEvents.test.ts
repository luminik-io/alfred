import { beforeEach, describe, expect, it, vi } from "vitest";

import { listenAppMenuEvents } from "./appMenuEvents";

const mocks = vi.hoisted(() => ({
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: mocks.listen,
}));

describe("listenAppMenuEvents", () => {
  beforeEach(() => {
    mocks.listen.mockReset();
    delete window.__TAURI_INTERNALS__;
  });

  it("does nothing in browser preview", async () => {
    const unlisten = await listenAppMenuEvents({
      onNavigate: vi.fn(),
      onCommandPalette: vi.fn(),
      onRefresh: vi.fn(),
    });

    expect(mocks.listen).not.toHaveBeenCalled();
    expect(() => unlisten()).not.toThrow();
  });

  it("routes valid native menu events and removes every listener", async () => {
    window.__TAURI_INTERNALS__ = {} as typeof window.__TAURI_INTERNALS__;
    const callbacks = new Map<string, (event: { payload: string }) => void>();
    const offs = [vi.fn(), vi.fn(), vi.fn()];
    mocks.listen.mockImplementation(
      async (event: string, callback: (event: { payload: string }) => void) => {
        callbacks.set(event, callback);
        return offs[callbacks.size - 1];
      },
    );
    const onNavigate = vi.fn();
    const onCommandPalette = vi.fn();
    const onRefresh = vi.fn();

    const unlisten = await listenAppMenuEvents({
      onNavigate,
      onCommandPalette,
      onRefresh,
    });
    callbacks.get("app-menu://navigate")?.({ payload: "settings" });
    callbacks.get("app-menu://navigate")?.({ payload: "not-a-route" });
    callbacks.get("app-menu://command-palette")?.({ payload: "" });
    callbacks.get("app-menu://refresh")?.({ payload: "" });

    expect(onNavigate).toHaveBeenCalledOnce();
    expect(onNavigate).toHaveBeenCalledWith("settings");
    expect(onCommandPalette).toHaveBeenCalledOnce();
    expect(onRefresh).toHaveBeenCalledOnce();

    unlisten();
    for (const off of offs) expect(off).toHaveBeenCalledOnce();
  });

  it("removes earlier listeners without starting registrations after a failure", async () => {
    window.__TAURI_INTERNALS__ = {} as typeof window.__TAURI_INTERNALS__;
    const firstOff = vi.fn();
    mocks.listen
      .mockResolvedValueOnce(firstOff)
      .mockRejectedValueOnce(new Error("native menu unavailable"));

    await expect(
      listenAppMenuEvents({
        onNavigate: vi.fn(),
        onCommandPalette: vi.fn(),
        onRefresh: vi.fn(),
      }),
    ).rejects.toThrow("native menu unavailable");
    expect(firstOff).toHaveBeenCalledOnce();
    expect(mocks.listen).toHaveBeenCalledTimes(2);
  });
});
