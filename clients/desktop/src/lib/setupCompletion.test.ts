import { describe, expect, it } from "vitest";

import type { SetupStatus } from "../types";
import { isSetupComplete } from "./setupCompletion";

function makeInstall(
  overrides: Partial<SetupStatus["install"]> = {},
): SetupStatus["install"] {
  return {
    agents_conf_present: true,
    scheduled_runs: 3,
    initialized: true,
    ...overrides,
  } as SetupStatus["install"];
}

function makeStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return {
    github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
    engines: [{ name: "claude", installed: true, path: "/opt/homebrew/bin/claude" }],
    engine_ready: true,
    repos: { selected: ["acme-org/api"], count: 1, keys: ["ALFRED_QUEUE_REPOS"] },
    demo: { present: false },
    install: makeInstall(),
    ready: true,
    ...overrides,
  };
}

describe("isSetupComplete", () => {
  it("is false when there is no status at all (fresh machine)", () => {
    expect(isSetupComplete(null)).toBe(false);
    expect(isSetupComplete(undefined)).toBe(false);
  });

  it("is true when engine is ready, GitHub is connected, a repo is selected, and the fleet is scheduled", () => {
    expect(isSetupComplete(makeStatus())).toBe(true);
  });

  it("is false when repo scope is present but the fleet was never deployed (no agents.conf)", () => {
    // The Inbox-misroute bug: engine + GitHub + a selected repo (often inherited
    // from the shell environment) with NO agents.conf and zero scheduled agents
    // is NOT a set-up install. It must land on onboarding, not the Inbox.
    const status = makeStatus({
      install: makeInstall({ agents_conf_present: false, scheduled_runs: 0 }),
    });
    expect(isSetupComplete(status)).toBe(false);
  });

  it("is false when agents.conf exists but no agents are scheduled yet", () => {
    const status = makeStatus({
      install: makeInstall({ agents_conf_present: true, scheduled_runs: 0 }),
    });
    expect(isSetupComplete(status)).toBe(false);
  });

  it("falls back to the core gates when the server reports no install state", () => {
    // An older runtime that omits install state must not trap a returning user
    // whose engine, GitHub, and repo scope are all present.
    const status = makeStatus({ install: undefined });
    expect(isSetupComplete(status)).toBe(true);
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
      install: makeInstall({ initialized: true }),
    });
    expect(isSetupComplete(status)).toBe(true);
  });

  it("falls back to the core gates when an older install omits the fleet inventory fields", () => {
    // An older server reports an install object (with initialized) but predates
    // the agents_conf_present / scheduled_runs fields. Treating the missing
    // fields as false would over-gate a working returning user back into
    // onboarding; the fleet gate is skipped instead.
    const status = makeStatus({
      install: { initialized: true } as SetupStatus["install"],
    });
    expect(isSetupComplete(status)).toBe(true);
  });

  it("treats a custom-agent-only fleet as deployed", () => {
    // A supported deployment: enabled CustomAgentStore rows carry their own
    // schedules and run with no base agents.conf. Such an install must boot to
    // the Inbox, not be forced back into onboarding on every launch.
    const status = makeStatus({
      install: makeInstall({
        agents_conf_present: false,
        scheduled_runs: 0,
        custom_agents: {
          path: null,
          count: 2,
          enabled_count: 2,
          disabled_count: 0,
          agents: [],
        },
      }),
    });
    expect(isSetupComplete(status)).toBe(true);
  });

  it("does not count disabled-only custom agents as a deployed fleet", () => {
    const status = makeStatus({
      install: makeInstall({
        agents_conf_present: false,
        scheduled_runs: 0,
        custom_agents: {
          path: null,
          count: 1,
          enabled_count: 0,
          disabled_count: 1,
          agents: [],
        },
      }),
    });
    expect(isSetupComplete(status)).toBe(false);
  });
});
