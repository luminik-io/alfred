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
    first_run: {
      version: 1,
      ready: true,
      status: "ready",
      headline: "Ready for the first real run.",
      summary: {
        required_ready: 7,
        required_total: 7,
        recommended_ready: 0,
        recommended_total: 0,
        optional_ready: 0,
        optional_total: 0,
        blockers: [],
      },
      checks: [],
    },
    ready: true,
    ...overrides,
  };
}

describe("isSetupComplete", () => {
  it("is false when there is no status at all (fresh machine)", () => {
    expect(isSetupComplete(null)).toBe(false);
    expect(isSetupComplete(undefined)).toBe(false);
  });

  it("is true when canonical first-run readiness is true", () => {
    expect(isSetupComplete(makeStatus())).toBe(true);
  });

  it("is false when canonical first-run readiness has a required blocker", () => {
    const status = makeStatus({
      first_run: {
        ...makeStatus().first_run,
        ready: false,
        status: "needs_action",
        headline: "1 required setup item needs action.",
        summary: {
          ...makeStatus().first_run.summary,
          required_ready: 6,
          blockers: ["repo_local_paths"],
        },
      },
    });
    expect(isSetupComplete(status)).toBe(false);
  });
});
