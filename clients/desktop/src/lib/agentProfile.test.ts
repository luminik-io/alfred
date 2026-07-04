import { describe, expect, it } from "vitest";

import { agentProfile } from "./agentProfile";
import type { FleetControlRow } from "./fleetControl";
import type { AgentSummary } from "../types";

function summary(codename: string, overrides: Partial<AgentSummary> = {}): AgentSummary {
  return {
    codename,
    last_firing_id: null,
    last_run_at: "2026-05-30T10:00:00Z",
    status: "live",
    last_summary: "ok",
    firings_today: 1,
    ...overrides,
  };
}

function row(codename: string, summaryOverrides: Partial<AgentSummary> = {}): FleetControlRow {
  return {
    codename,
    summary: summary(codename, summaryOverrides),
    paused: false,
    pausedSince: null,
    loaded: true,
    consecutiveFailures: 0,
    service: "running",
  };
}

describe("agentProfile under a custom theme", () => {
  // Thread: "Custom Theme Hides Runtime Labels". A custom theme that does not
  // override THIS agent must keep the runtime's display_name / role_title rather
  // than replace it with a Batman default or a titleized codename.
  it("keeps runtime labels for an agent with no custom override", () => {
    const profile = agentProfile(
      row("lucius", { display_name: "Q Branch", role_title: "Gadget lead" }),
      undefined,
      "custom",
      { names: { batman: "Sherlock" }, roles: { batman: "Lead detective" } },
    );
    // lucius was not named in the custom map, so the server labels still win.
    expect(profile.name).toBe("Q Branch");
    expect(profile.roleLabel).toBe("Gadget lead");
  });

  it("applies the custom override only to the named agent", () => {
    const custom = {
      names: { batman: "Sherlock" },
      roles: { batman: "Lead detective" },
    };
    const batman = agentProfile(
      row("batman", { display_name: "Server Batman", role_title: "Server role" }),
      undefined,
      "custom",
      custom,
    );
    // The named agent takes the operator's authored name and role label, which
    // override even a server-provided label.
    expect(batman.name).toBe("Sherlock");
    expect(batman.roleLabel).toBe("Lead detective");
  });

  it("keeps the runtime name when only the role is customized", () => {
    const profile = agentProfile(
      row("lucius", { display_name: "Q Branch", role_title: "Gadget lead" }),
      undefined,
      "custom",
      { names: {}, roles: { lucius: "Quartermaster" } },
    );
    // Only the role was overridden, so the runtime name is preserved.
    expect(profile.name).toBe("Q Branch");
    expect(profile.roleLabel).toBe("Quartermaster");
  });

});

describe("agentProfile under a preset roster theme", () => {
  // Thread: "Roster theme switch never re-skins the fleet". The runtime reports a
  // display_name/role_title for every default agent, and those defaults ARE the
  // Batman roster. A non-Batman preset must therefore OWN the name for a known
  // fleet agent, or picking Transformers/Justice League changes nothing.
  it("re-skins a known fleet agent with the selected preset name", () => {
    const profile = agentProfile(
      row("lucius", { display_name: "Lucius", role_title: "Senior Developer" }),
      undefined,
      "transformers",
    );
    // lucius -> Ironhide under Transformers (see lib/roster_manifest.json).
    expect(profile.name).toBe("Ironhide");
    // Preset role labels come from the manifest, not the runtime's Batman label.
    expect(profile.roleLabel).toBe("Senior developer");
  });

  it("re-skins a known fleet agent under Justice League", () => {
    const profile = agentProfile(
      row("batman", { display_name: "Batman", role_title: "Architect" }),
      undefined,
      "justice-league",
    );
    // batman stays "Batman" under Justice League, but a re-mapped agent changes.
    const flash = agentProfile(
      row("robin", { display_name: "Robin", role_title: "Bug Triage" }),
      undefined,
      "justice-league",
    );
    expect(profile.name).toBe("Batman");
    expect(flash.name).toBe("The Flash");
  });

  it("keeps the shipped names under the default Batman theme", () => {
    const profile = agentProfile(
      row("lucius", { display_name: "Lucius", role_title: "Senior Developer" }),
      undefined,
      "batman",
    );
    // The default theme is the shipped roster, so the runtime label still shows.
    expect(profile.name).toBe("Lucius");
    expect(profile.roleLabel).toBe("Senior Developer");
  });

  it("keeps a runtime label for an UNKNOWN agent even under a preset", () => {
    // An agent the theme has no persona for is not a re-skin of the shipped
    // roster, so a server that renames it is still honored.
    const profile = agentProfile(
      row("custom-scout", { display_name: "Scout", role_title: "Recon" }),
      undefined,
      "transformers",
    );
    expect(profile.name).toBe("Scout");
    expect(profile.roleLabel).toBe("Recon");
  });
});
