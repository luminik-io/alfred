import { describe, expect, it } from "vitest";

import {
  deriveAgentRole,
  isKnownFleetCodename,
  scheduleRoleLabelForEditor,
  type WorkflowRole,
} from "./agentRoster";
import {
  buildThemedRoster,
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

describe("stable runtime identities", () => {
  it("does not treat themed display names as built-in runtime ids", () => {
    expect(isKnownFleetCodename("lucius")).toBe(false);
    expect(isKnownFleetCodename("batman")).toBe(false);
    expect(isKnownFleetCodename("rasalghul")).toBe(false);
    expect(deriveAgentRole({ codename: "lucius" })).toBe("ops");
  });

  it("uses explicit metadata to place a custom runtime agent", () => {
    const identity = resolveThemedIdentity(
      { codename: "release-captain", roleTitle: "Release coordinator" },
      "justice-league",
    );
    expect(identity.role).toBe("ship");
  });
});

describe("buildThemedRoster", () => {
  it("gives every agent a distinct name when several share a role", () => {
    // Two custom triage agents + two custom planner agents: naive role naming
    // would collide (two "The Flash", two "Green Arrow"). The roster pass must
    // hand out distinct names.
    const roster = buildThemedRoster(
      [
        { codename: "triage-one", roleTitle: "Bug triage" },
        { codename: "gordon-triage", roleTitle: "Bug triage" },
        { codename: "drake", roleTitle: "Issue planner" },
        { codename: "planner-two", roleTitle: "Release planner" },
      ],
      "justice-league",
    );
    const names = [...roster.values()].map((identity) => identity.name);
    expect(new Set(names).size).toBe(names.length);
    // The first triage agent (sorted by codename) takes the role's primary name.
    expect(roster.get("gordon-triage")?.role).toBe("triage");
    expect(roster.get("triage-one")?.role).toBe("triage");
  });

  it("never repeats a display name across the full default fleet in any theme", () => {
    const sources = Object.keys(rosterThemeFor("batman").nameByCodename).map((codename) => ({
      codename,
    }));
    for (const themeId of ROSTER_THEME_IDS) {
      const roster = buildThemedRoster(sources, themeId);
      const names = [...roster.values()].map((identity) => identity.name);
      expect(names.length).toBeGreaterThan(0);
      expect(new Set(names).size).toBe(names.length);
    }
  });

  it("preserves the exact canonical name for known slug codenames", () => {
    const roster = buildThemedRoster(
      [{ codename: "architect" }, { codename: "senior-dev" }],
      "programmers",
    );
    expect(roster.get("architect")?.name).toBe("Turing");
    expect(roster.get("senior-dev")?.name).toBe("Torvalds");
  });

  it("falls back to a numeric suffix once every themed name is used", () => {
    // More triage agents than the theme has names: the tail overflows to a
    // suffix but every name stays unique.
    const sources = Array.from({ length: 30 }, (_unused, index) => ({
      codename: `triage-${String(index).padStart(2, "0")}`,
      roleTitle: "Bug triage",
    }));
    const roster = buildThemedRoster(sources, "batman");
    const names = [...roster.values()].map((identity) => identity.name);
    expect(names.length).toBe(30);
    expect(new Set(names).size).toBe(30);
  });
});

describe("built-in themes", () => {
  it("ships the four new global themes as selectable presets", () => {
    for (const themeId of [
      "programmers",
      "scientists",
      "mathematicians",
      "philosophers",
    ] as const) {
      expect(PRESET_ROSTER_THEME_IDS).toContain(themeId);
      expect(isRosterThemeId(themeId)).toBe(true);
      const theme = rosterThemeFor(themeId);
      const names = Object.values(theme.nameByCodename);
      // Every name is non-blank and distinct within the theme.
      expect(names.length).toBeGreaterThanOrEqual(12);
      expect(new Set(names.map((name) => name.toLowerCase())).size).toBe(names.length);
    }
  });

  it("includes globally diverse figures, Indian ones among them", () => {
    const scientists = Object.values(rosterThemeFor("scientists").nameByCodename);
    expect(scientists).toEqual(expect.arrayContaining(["Raman", "Kalam", "Bose"]));
    const maths = Object.values(rosterThemeFor("mathematicians").nameByCodename);
    expect(maths).toEqual(
      expect.arrayContaining(["Ramanujan", "Aryabhata", "Bhaskara", "Brahmagupta"]),
    );
    const philosophers = Object.values(rosterThemeFor("philosophers").nameByCodename);
    expect(philosophers).toEqual(
      expect.arrayContaining(["Chanakya", "Adi Shankara", "Vivekananda"]),
    );
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
