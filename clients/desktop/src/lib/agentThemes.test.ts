import { describe, expect, it } from "vitest";

import {
  deriveAgentRole,
  scheduleRoleLabelForEditor,
  type WorkflowRole,
} from "./agentRoster";
import {
  editableAgents,
  isRosterThemeId,
  PRESET_ROSTER_THEME_IDS,
  resolveThemedIdentity,
  ROSTER_THEME_IDS,
  rosterThemeBlurb,
  rosterThemeFor,
  rosterThemeLabel,
} from "./agentThemes";

describe("deriveAgentRole", () => {
  it("places known default-fleet codenames by the hint table", () => {
    expect(deriveAgentRole({ codename: "architect" })).toBe("architect");
    expect(deriveAgentRole({ codename: "reviewer" })).toBe("reviewer");
    expect(deriveAgentRole({ codename: "automerge" })).toBe("ship");
    expect(deriveAgentRole({ codename: "fleet-doctor" })).toBe("ops");
  });

  it("tolerates fully-qualified codenames", () => {
    expect(deriveAgentRole({ codename: "fleet.local.senior-dev" })).toBe("senior-dev");
  });

  it("infers a role from the reported role title when the codename is unknown", () => {
    expect(deriveAgentRole({ codename: "newbot", roleTitle: "Code Reviewer" })).toBe(
      "reviewer",
    );
    expect(deriveAgentRole({ codename: "newbot", roleTitle: "Senior Developer" })).toBe(
      "senior-dev",
    );
  });

  it("maps generated alfred-init schedule role strings before fuzzy keywords", () => {
    expect(deriveAgentRole({ codename: "q-branch", roleTitle: "feature dev" })).toBe(
      "senior-dev",
    );
    expect(deriveAgentRole({ codename: "repo-cartographer", roleTitle: "code map refresh" })).toBe(
      "ops",
    );
    expect(deriveAgentRole({ codename: "merge-bot", roleTitle: "PR automerge" })).toBe(
      "ship",
    );
  });

  it("falls back to ops for a wholly unknown agent rather than dropping it", () => {
    expect(deriveAgentRole({ codename: "totally-unknown" })).toBe("ops");
  });

  it("places every default-fleet codename in its canonical lane via the hint table", () => {
    // Codenames are now the canonical role SLUGS (the Batman-cast names survive
    // only as theme display names). The data-driven derivation must place every
    // shipped codename in its canonical lane so the default roster renders as the
    // full pipeline, left to right.
    const CANONICAL_LANES: Record<string, WorkflowRole> = {
      triage: "triage",
      planner: "planner",
      "spec-planner": "spec-planner",
      architect: "architect",
      "senior-dev": "senior-dev",
      "test-engineer": "test-engineer",
      fixer: "fixer",
      reviewer: "reviewer",
      "e2e-runner": "e2e-runner",
      "ops-watch": "ops-watch",
      automerge: "ship",
      "fleet-doctor": "ops",
      "agent-cleanup": "ops",
      "memory-harvest": "ops",
      "memory-auto-promote": "ops",
      "code-map-refresh": "ops",
      "agent-morning-brief": "ops",
      "fleet-recap-morning": "ops",
      "fleet-recap-evening": "ops",
      "shipped-summary-daily": "ship",
      "shipped-summary-weekly": "ship",
      "proof-telemetry": "ops",
    };
    for (const [codename, lane] of Object.entries(CANONICAL_LANES)) {
      expect(deriveAgentRole({ codename })).toBe(lane);
    }
  });
});

describe("scheduleRoleLabelForEditor", () => {
  it("does not surface generated schedule roles as labels for known shipped agents", () => {
    expect(
      scheduleRoleLabelForEditor({
        codename: "senior-dev",
        role: "feature dev",
        roleTitle: null,
      }),
    ).toBeNull();
  });

  it("preserves agents.conf descriptors for schedule-only custom agents", () => {
    expect(
      scheduleRoleLabelForEditor({
        codename: "release-captain",
        role: "Release conductor",
        roleTitle: null,
      }),
    ).toBe("Release conductor");
  });

  it("prefers explicit role_title when the server has profile metadata", () => {
    expect(
      scheduleRoleLabelForEditor({
        codename: "release-captain",
        role: "Release conductor",
        roleTitle: "Launch lead",
      }),
    ).toBe("Launch lead");
  });
});

describe("resolveThemedIdentity", () => {
  it("keeps the shipped names under the default Batman theme", () => {
    const id = resolveThemedIdentity({ codename: "architect" }, "batman");
    expect(id.name).toBe("Batman");
    expect(id.role).toBe("architect");
    expect(id.roleLabel).toBe("Architect");
  });

  it("re-skins the architect lead under Transformers without changing the role", () => {
    const id = resolveThemedIdentity({ codename: "architect" }, "transformers");
    expect(id.name).toBe("Optimus Prime");
    expect(id.role).toBe("architect");
    // The plain role label is preserved across themes.
    expect(id.roleLabel).toBe("Architect");
  });

  it("re-skins under Justice League", () => {
    const id = resolveThemedIdentity({ codename: "reviewer" }, "justice-league");
    expect(id.name).toBe("Wonder Woman");
    expect(id.roleLabel).toBe("Reviewer");
  });

  it("never returns a blank name for an unknown agent in any theme", () => {
    for (const themeId of ROSTER_THEME_IDS) {
      const id = resolveThemedIdentity({ codename: "mystery-bot-7" }, themeId);
      expect(id.name.length).toBeGreaterThan(0);
      expect(id.roleLabel.length).toBeGreaterThan(0);
    }
  });
});

describe("isRosterThemeId", () => {
  it("accepts every id in ROSTER_THEME_IDS and rejects unknown or null", () => {
    // Derived from ROSTER_THEME_IDS so adding a theme can't desync the guard.
    for (const themeId of ROSTER_THEME_IDS) {
      expect(isRosterThemeId(themeId)).toBe(true);
    }
    expect(isRosterThemeId("nope")).toBe(false);
    expect(isRosterThemeId(null)).toBe(false);
  });
});

describe("shared roster manifest", () => {
  it("gives every preset the same codename coverage and nonblank names", () => {
    const base = Object.keys(rosterThemeFor("batman").nameByCodename).sort();
    expect(base.length).toBeGreaterThan(0);

    for (const themeId of PRESET_ROSTER_THEME_IDS) {
      const names = rosterThemeFor(themeId).nameByCodename;
      expect(rosterThemeLabel(themeId).trim().length).toBeGreaterThan(0);
      expect(rosterThemeBlurb(themeId).trim().length).toBeGreaterThan(0);
      expect(Object.keys(names).sort()).toEqual(base);
      for (const name of Object.values(names)) {
        expect(name.trim().length).toBeGreaterThan(0);
      }
    }
  });
});

describe("custom roster theme", () => {
  it("applies operator names and role labels over the Batman base", () => {
    const id = resolveThemedIdentity({ codename: "architect" }, "custom", {
      names: { architect: "Sherlock" },
      roles: { architect: "Lead detective" },
    });
    expect(id.name).toBe("Sherlock");
    expect(id.role).toBe("architect");
    expect(id.roleLabel).toBe("Lead detective");
  });

  it("keeps a custom role label scoped to the named codename only", () => {
    // A custom role label is authored PER AGENT. Renaming the senior-dev role
    // must NOT relabel the test-engineer (which would happen if the override
    // were folded into the role-wide labels), matching the Slack path where
    // role_label_for is keyed by codename.
    const custom = {
      names: {},
      roles: { "senior-dev": "Quartermaster" },
    };
    const seniorDev = resolveThemedIdentity({ codename: "senior-dev" }, "custom", custom);
    const testEngineer = resolveThemedIdentity({ codename: "test-engineer" }, "custom", custom);
    expect(seniorDev.roleLabel).toBe("Quartermaster");
    // test-engineer keeps its canonical label, not senior-dev's custom one.
    expect(testEngineer.roleLabel).toBe("Test engineer");
  });

  it("falls back to the Batman name when an agent is not customized", () => {
    const id = resolveThemedIdentity({ codename: "senior-dev" }, "custom", {
      names: { architect: "Sherlock" },
      roles: {},
    });
    // senior-dev was not renamed, so it keeps its shipped Batman-base name.
    expect(id.name).toBe("Lucius");
    expect(id.roleLabel).toBe("Senior developer");
  });

  it("ignores blank custom names rather than rendering an empty label", () => {
    const id = resolveThemedIdentity({ codename: "architect" }, "custom", {
      names: { architect: "   " },
      roles: {},
    });
    expect(id.name).toBe("Batman");
  });

  it("normalizes a dotted codename when building the custom theme", () => {
    const theme = rosterThemeFor("custom", {
      names: { "fleet.local.architect": "Sherlock" },
      roles: {},
    });
    expect(theme.nameByCodename.architect).toBe("Sherlock");
  });

  it("presets ignore custom maps entirely", () => {
    const id = resolveThemedIdentity({ codename: "architect" }, "transformers", {
      names: { architect: "Sherlock" },
      roles: {},
    });
    expect(id.name).toBe("Optimus Prime");
  });

  it("can name a future custom agent without adding it to the preset roster", () => {
    const id = resolveThemedIdentity(
      { codename: "security-scout", roleTitle: "Code Reviewer" },
      "custom",
      {
        names: { "security-scout": "Sentinel" },
        roles: { "security-scout": "Security reviewer" },
      },
    );
    expect(id.role).toBe("reviewer");
    expect(id.name).toBe("Sentinel");
    expect(id.roleLabel).toBe("Security reviewer");
  });
});

describe("editableAgents", () => {
  it("lists the full default roster with a role, name, and role label each", () => {
    const agents = editableAgents();
    expect(agents.length).toBeGreaterThan(0);
    for (const agent of agents) {
      expect(agent.codename.length).toBeGreaterThan(0);
      expect(agent.defaultName.length).toBeGreaterThan(0);
      expect(agent.defaultRoleLabel.length).toBeGreaterThan(0);
    }
    // The obsolete cleanup alias is gone; the canonical scheduled codename is
    // editable because it is what the installer deploys.
    expect(agents.some((a) => a.codename === "cleanup")).toBe(false);
    expect(agents.some((a) => a.codename === "agent-cleanup")).toBe(true);
    expect(agents.some((a) => a.codename === "architect")).toBe(true);
    expect(agents.some((a) => a.codename === "memory-auto-promote")).toBe(true);
    expect(agents.some((a) => a.codename === "shipped-summary-weekly")).toBe(true);
  });

  it("adds live custom agents to the editable roster", () => {
    const agents = editableAgents([
      {
        codename: "security-scout",
        displayName: "Sentinel",
        roleLabel: "Security reviewer",
        roleTitle: "Code Reviewer",
      },
    ]);
    expect(agents).toContainEqual({
      codename: "security-scout",
      role: "reviewer",
      defaultName: "Sentinel",
      defaultRoleLabel: "Security reviewer",
    });
  });

  it("keeps Batman-base placeholders for known agents even when runtime reports a themed name", () => {
    const agents = editableAgents([
      {
        codename: "senior-dev",
        displayName: "Ironhide",
        roleLabel: "Senior developer",
        roleTitle: "Senior Developer",
      },
    ]);
    expect(agents.find((agent) => agent.codename === "senior-dev")).toMatchObject({
      defaultName: "Lucius",
      defaultRoleLabel: "Senior developer",
    });
  });
});
