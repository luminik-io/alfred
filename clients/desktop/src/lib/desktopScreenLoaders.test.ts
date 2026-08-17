import { beforeEach, describe, expect, it, vi } from "vitest";

const loaded = vi.hoisted(() => ({
  ask: vi.fn(),
  code: vi.fn(),
  settings: vi.fn(),
  work: vi.fn(),
}));

vi.mock("../components/ComposeView", () => {
  loaded.ask();
  return { ComposeView: () => null };
});
vi.mock("../components/CodeIntelligenceView", () => {
  loaded.code();
  return { CodeIntelligenceView: () => null };
});
vi.mock("../components/SettingsView", () => {
  loaded.settings();
  return { SettingsView: () => null };
});
vi.mock("../components/PipelineView", () => {
  loaded.work();
  return { PipelineView: () => null };
});

import { preloadDesktopTab } from "./desktopScreenLoaders";

describe("desktop screen preloaders", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads only the screen requested by navigation intent", async () => {
    await preloadDesktopTab("code");

    expect(loaded.code).toHaveBeenCalledOnce();
    expect(loaded.ask).not.toHaveBeenCalled();
    expect(loaded.settings).not.toHaveBeenCalled();
    expect(loaded.work).not.toHaveBeenCalled();
  });

  it("keeps the initial Inbox route in the application chunk", async () => {
    await preloadDesktopTab("home");

    expect(loaded.ask).not.toHaveBeenCalled();
    expect(loaded.code).not.toHaveBeenCalled();
    expect(loaded.settings).not.toHaveBeenCalled();
    expect(loaded.work).not.toHaveBeenCalled();
  });
});
