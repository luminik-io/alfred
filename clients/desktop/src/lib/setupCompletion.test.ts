import { describe, expect, it } from "vitest";

import type { SetupStatus } from "../types";
import { isSetupComplete } from "./setupCompletion";

function makeStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return {
    github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
    engines: [{ name: "claude", installed: true, path: "/opt/homebrew/bin/claude" }],
    engine_ready: true,
    repos: { selected: ["acme-org/api"], count: 1, keys: ["ALFRED_QUEUE_REPOS"] },
    demo: { present: false },
    ready: true,
    ...overrides,
  };
}

describe("isSetupComplete", () => {
  it("is false when there is no status at all (fresh machine)", () => {
    expect(isSetupComplete(null)).toBe(false);
    expect(isSetupComplete(undefined)).toBe(false);
  });

  it("is true when engine is ready, GitHub is connected, and a repo is selected", () => {
    expect(isSetupComplete(makeStatus())).toBe(true);
  });

  it("is false when no coding engine is ready", () => {
    expect(isSetupComplete(makeStatus({ engine_ready: false }))).toBe(false);
  });

  it("is false when GitHub is not connected", () => {
    expect(
      isSetupComplete(
        makeStatus({ github: { ok: false, account: null, detail: "Not signed in." } }),
      ),
    ).toBe(false);
  });

  it("is false when no repository is selected", () => {
    expect(
      isSetupComplete(makeStatus({ repos: { selected: [], count: 0, keys: [] } })),
    ).toBe(false);
  });

  it("is false when the server says the install was never initialised", () => {
    // A stale/partial install: a runtime directory hint says not initialised, so
    // setup is incomplete regardless of the other flags.
    const status = makeStatus({
      install: {
        initialized: false,
      } as SetupStatus["install"],
    });
    expect(isSetupComplete(status)).toBe(false);
  });

  it("stays true when install.initialized is true and all gates pass", () => {
    const status = makeStatus({
      install: {
        initialized: true,
      } as SetupStatus["install"],
    });
    expect(isSetupComplete(status)).toBe(true);
  });
});
