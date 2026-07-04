import { isKnownFleetCodename } from "./agentRoster";
import {
  type CustomRosterNames,
  DEFAULT_ROSTER_THEME,
  EMPTY_CUSTOM_NAMES,
  normalizeCodename,
  resolveThemedIdentity,
  type RosterThemeId,
} from "./agentThemes";
import type { FleetControlRow } from "./fleetControl";
import type { WorkflowNodeInput } from "./workflowGraph";
import type { ScheduledRun } from "../types";

// The resolved display profile for one agent under the active roster theme.
// `label` keeps the legacy "Name · Role" form for the aria title; `name` and
// `roleLabel` render separately so the role is always plain.
export type AgentProfile = {
  name: string;
  role: WorkflowNodeInput["role"];
  roleLabel: string;
  label: string;
  purpose: string;
  themeAccent: string;
};

// Resolve an agent's display profile under the active roster theme. The themed
// name + role label come from the theme mapping (keyed off the agent's derived
// role, never a literal name list); the runtime's own reported display name /
// role title still take precedence when present so a server that labels its
// agents is honored.
export function agentProfile(
  row: FleetControlRow,
  schedule?: ScheduledRun,
  themeId: RosterThemeId = DEFAULT_ROSTER_THEME,
  customNames: CustomRosterNames = EMPTY_CUSTOM_NAMES,
): AgentProfile {
  const identity = resolveThemedIdentity(
    {
      codename: row.codename,
      roleTitle: row.summary?.role_title || schedule?.role_title || schedule?.role,
      purpose: row.summary?.purpose || schedule?.purpose,
    },
    themeId,
    customNames,
  );
  // Name/label resolution has to honor two things at once:
  //   1. A non-Batman preset must actually re-skin the KNOWN fleet. The runtime
  //      reports a `display_name`/`role_title` for every default agent, and those
  //      defaults ARE the Batman roster (see lib/server/agent_profiles.py). If the
  //      runtime label always won, picking Transformers or Justice League would
  //      never change a single name, because the server keeps sending "Batman",
  //      "Lucius", etc. So for a known fleet codename under a non-default preset,
  //      the selected THEME is the source of truth for the display name.
  //   2. A server that genuinely renames an UNKNOWN agent (one the theme has no
  //      persona for) must still be honored, and the operator's per-agent custom
  //      overrides must still win under the `custom` theme. Neither of those is a
  //      re-skin of the shipped roster, so the runtime label is kept there.
  const short = normalizeCodename(row.codename);
  const known = isKnownFleetCodename(row.codename);
  const hasCustomName = themeId === "custom" && Boolean(customNames.names[short]?.trim());
  const hasCustomRole = themeId === "custom" && Boolean(customNames.roles[short]?.trim());
  // A preset (not custom) other than the shipped Batman roster owns the display
  // name/role label for any known fleet agent: the runtime default is just the
  // Batman name, so letting it win would defeat the theme switch entirely.
  const presetReskinsKnown =
    themeId !== "custom" && themeId !== DEFAULT_ROSTER_THEME && known;
  const themeOwnsName = hasCustomName || presetReskinsKnown;
  const themeOwnsRole = hasCustomRole || presetReskinsKnown;
  const name = themeOwnsName
    ? identity.name
    : row.summary?.display_name || schedule?.display_name || identity.name;
  const roleLabel = themeOwnsRole
    ? identity.roleLabel
    : row.summary?.role_title || schedule?.role_title || identity.roleLabel;
  const purpose = row.summary?.purpose || schedule?.purpose || "";
  return {
    name,
    role: identity.role,
    roleLabel,
    label: roleLabel ? `${name} · ${roleLabel}` : name,
    purpose,
    themeAccent: row.summary?.theme_accent || schedule?.theme_accent || "var(--primary)",
  };
}
