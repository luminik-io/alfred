// Roster themes: where an agent's DISPLAY NAME and ROLE LABEL come from, kept
// entirely separate from its canonical WorkflowRole (see agentRoster.ts). A
// theme maps each canonical role to a plain role label, and maps each known
// fleet codename to a themed persona name. The default theme reproduces the
// shipped default roster exactly (no visible change unless the operator picks
// another theme); presets re-skin the SAME fleet with matched display names while the
// roles stay identical.
//
// The desktop picker, server persistence, custom-name editor, and Slack
// rendering all use this same theme contract. Future work should extend the
// roster by role/engine metadata, not fork the naming model.

import {
  deriveAgentRole,
  type RoleSource,
  type WorkflowRole,
} from "./agentRoster";
import { ROSTER_MANIFEST, type PresetRosterThemeId } from "./rosterManifest";

// The preset ids re-skin the shipped fleet. `custom` is the operator-authored
// theme whose names + role labels are persisted server-side (and mirrored to
// localStorage), so the choice is shared across the desktop and the Slack path.
export type RosterThemeId = PresetRosterThemeId | "custom";

export type RosterTheme = {
  id: RosterThemeId;
  label: string;
  blurb: string;
  // Plain role labels, one per canonical role. Same concept across themes (a
  // Reviewer is a Reviewer); a theme may phrase it in its own register but the
  // role itself never changes when the theme changes.
  roleLabels: Record<WorkflowRole, string>;
  // Themed display name per known fleet codename. Unknown agents (not in this
  // map) fall back to their own runtime name or a titleized codename, never to
  // another agent's persona, so two agents can never collide on one name.
  nameByCodename: Record<string, string>;
  // Ordered pool of themed names per canonical role. An agent whose codename is
  // NOT a known fleet slug (a custom agent) is
  // named by its derived ROLE from this pool, so theme application is correct for
  // ANY install and not just the canonical slugs. When several agents share a
  // role, the batch resolver (buildThemedRoster) walks the pool to give each a
  // DISTINCT name. Built from the manifest by grouping agents by role.
  namePoolByRole: Partial<Record<WorkflowRole, readonly string[]>>;
  // Every themed name in manifest order, used as the overflow pool when a role's
  // own pool is exhausted, so a duplicate agent still gets a real themed name
  // before falling back to a numeric suffix.
  namePool: readonly string[];
  // Per-codename role label override (only the `custom` theme uses this). The
  // operator authors a role label PER AGENT, so it must not bleed onto every
  // other agent that happens to share the same canonical role. Resolution
  // prefers this over the canonical `roleLabels[role]`, matching the Slack path
  // (RosterThemeState.role_label_for, which is also keyed by codename).
  roleLabelByCodename?: Record<string, string>;
};

// Canonical role per known fleet codename, used to map an operator's per-agent
// custom role label back onto the canonical role the theme keys on.
const ROLE_BY_CODENAME: Record<string, WorkflowRole> = Object.fromEntries(
  ROSTER_MANIFEST.agents.map((agent) => [agent.codename, agent.role]),
);

// The canonical role labels the presets share. Kept in the manifest so Python
// Slack rendering and the desktop resolve the same role text.
const ROLE_LABELS_DEFAULT: Record<WorkflowRole, string> = ROSTER_MANIFEST.role_labels;

function fallbackThemeLabel(themeId: string): string {
  return themeId
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function manifestThemeMeta(themeId: PresetRosterThemeId): { label: string; blurb: string } {
  const meta = ROSTER_MANIFEST.themes[themeId];
  const label = meta?.label?.trim() || fallbackThemeLabel(themeId);
  const blurb = meta?.blurb?.trim() || "Preset roster theme.";
  return { label, blurb };
}

function manifestNameByCodename(themeId: PresetRosterThemeId): Record<string, string> {
  return Object.fromEntries(
    ROSTER_MANIFEST.agents.map((agent) => [agent.codename, agent.names[themeId]]),
  );
}

// Group the theme's manifest names by canonical role, preserving manifest order,
// so a role with several agents (ops, ship) carries an ordered pool the batch
// resolver can hand out one distinct name at a time.
function manifestNamePoolByRole(
  themeId: PresetRosterThemeId,
): Partial<Record<WorkflowRole, string[]>> {
  const pools: Partial<Record<WorkflowRole, string[]>> = {};
  for (const agent of ROSTER_MANIFEST.agents) {
    (pools[agent.role] ??= []).push(agent.names[themeId]);
  }
  return pools;
}

function manifestNamePool(themeId: PresetRosterThemeId): string[] {
  return ROSTER_MANIFEST.agents.map((agent) => agent.names[themeId]);
}

function buildPresetTheme(themeId: PresetRosterThemeId): RosterTheme {
  const meta = manifestThemeMeta(themeId);
  return {
    id: themeId,
    label: meta.label,
    blurb: meta.blurb,
    roleLabels: ROLE_LABELS_DEFAULT,
    nameByCodename: manifestNameByCodename(themeId),
    namePoolByRole: manifestNamePoolByRole(themeId),
    namePool: manifestNamePool(themeId),
  };
}

const BASE_THEME: RosterTheme = buildPresetTheme("batman");

// The preset themes only (the `custom` theme is built at runtime from the
// operator's persisted names, so it has no static entry here).
export const PRESET_ROSTER_THEMES: Record<PresetRosterThemeId, RosterTheme> =
  Object.fromEntries(
    ROSTER_MANIFEST.preset_theme_ids.map((themeId) => [themeId, buildPresetTheme(themeId)]),
  ) as Record<PresetRosterThemeId, RosterTheme>;

export const PRESET_ROSTER_THEME_IDS: readonly PresetRosterThemeId[] =
  ROSTER_MANIFEST.preset_theme_ids;

// The full set the picker offers, custom last so the presets read first.
export const ROSTER_THEME_IDS: readonly RosterThemeId[] = [
  ...PRESET_ROSTER_THEME_IDS,
  "custom",
];

export const DEFAULT_ROSTER_THEME: RosterThemeId = ROSTER_MANIFEST.default_theme;

// The operator's authored maps for the `custom` theme: codename -> display
// name and codename -> role label. Anything the operator has not named falls
// back to the base theme, so a half-filled custom theme is never blank.
export type CustomRosterNames = {
  names: Record<string, string>;
  roles: Record<string, string>;
};

export const EMPTY_CUSTOM_NAMES: CustomRosterNames = { names: {}, roles: {} };

const CUSTOM_THEME_META = {
  label: "Custom",
  blurb: "Your own roster. Rename each agent; blanks keep default names.",
} as const;

// Build the `custom` theme by overlaying the operator's names/roles on the
// base theme so every agent has a name even when only a few are edited. Role
// labels are keyed by canonical role; an operator role label is applied to the
// role of any codename it names.
function buildCustomTheme(custom: CustomRosterNames): RosterTheme {
  const nameByCodename: Record<string, string> = { ...BASE_THEME.nameByCodename };
  for (const [codename, name] of Object.entries(custom.names)) {
    const clean = name.trim();
    if (clean) nameByCodename[normalizeCodename(codename)] = clean;
  }
  // A custom role label is authored PER AGENT, so it is stored against that one
  // codename and never folded into the role-wide labels. The canonical
  // roleLabels stay at the default theme; resolution overlays the per-codename
  // override on top so naming one architect "Lead detective" relabels only that
  // agent, not every other architect-role agent (which is exactly what Slack does).
  const roleLabelByCodename: Record<string, string> = {};
  for (const [codename, label] of Object.entries(custom.roles)) {
    const clean = label.trim();
    if (clean) roleLabelByCodename[normalizeCodename(codename)] = clean;
  }
  return {
    id: "custom",
    label: CUSTOM_THEME_META.label,
    blurb: CUSTOM_THEME_META.blurb,
    roleLabels: { ...ROLE_LABELS_DEFAULT },
    nameByCodename,
    // A custom theme names agents per-codename; an agent the operator has NOT
    // named falls back to its role pool. Inherit the Batman base pools so an
    // un-named agent still gets a real name (its base persona) rather than a bare
    // codename, matching how nameByCodename overlays the base names.
    namePoolByRole: BASE_THEME.namePoolByRole,
    namePool: BASE_THEME.namePool,
    roleLabelByCodename,
  };
}

// Resolve the active theme by id, building the custom theme from the operator's
// authored names when needed. A preset ignores the custom maps entirely.
export function rosterThemeFor(
  themeId: RosterThemeId,
  custom: CustomRosterNames = EMPTY_CUSTOM_NAMES,
): RosterTheme {
  if (themeId === "custom") return buildCustomTheme(custom);
  return PRESET_ROSTER_THEMES[themeId] ?? BASE_THEME;
}

export function isRosterThemeId(value: string | null): value is RosterThemeId {
  return ROSTER_THEME_IDS.includes(value as RosterThemeId);
}

// Picker-facing label + blurb for any theme id (presets read from their static
// entry; custom from its meta), so the picker never has to special-case custom.
export function rosterThemeLabel(themeId: RosterThemeId): string {
  if (themeId === "custom") return CUSTOM_THEME_META.label;
  return PRESET_ROSTER_THEMES[themeId]?.label ?? BASE_THEME.label;
}

export function rosterThemeBlurb(themeId: RosterThemeId): string {
  if (themeId === "custom") return CUSTOM_THEME_META.blurb;
  return PRESET_ROSTER_THEMES[themeId]?.blurb ?? BASE_THEME.blurb;
}

export function normalizeCodename(codename: string): string {
  return (codename.split(".").pop() || codename).trim().toLowerCase();
}

/** The plain, theme-independent fallback name when nothing else fits. */
function titleizeCodename(codename: string): string {
  const short = (codename.split(".").pop() || codename).trim();
  return short
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export type ThemedIdentity = {
  // The canonical role the agent occupies.
  role: WorkflowRole;
  // The themed display name to show.
  name: string;
  // The plain role label to show alongside the name, always present so every
  // card/node can render the role independent of the themed name.
  roleLabel: string;
};

/**
 * Resolve an agent's themed identity:
 *   - role comes from its metadata (deriveAgentRole), never a name list;
 *   - name comes from the theme's per-codename map when present, else a
 *     titleized codename so an unknown agent is never blank and never borrows
 *     another agent's persona;
 *   - roleLabel comes from the theme's role labels.
 * Pure and deterministic. Callers layer the runtime's own reported display name
 * / role title on top when the server already labels an agent (see
 * FleetControlView.agentProfile).
 */
export function resolveThemedIdentity(
  source: RoleSource,
  themeId: RosterThemeId,
  custom: CustomRosterNames = EMPTY_CUSTOM_NAMES,
): ThemedIdentity {
  const theme = rosterThemeFor(themeId, custom);
  const role = deriveAgentRole(source);
  const short = normalizeCodename(source.codename);
  // Name resolution, in order:
  //   1. an exact per-codename name (the canonical fleet slug, or an operator's
  //      custom name), then
  //   2. the theme's name for a custom agent's reported role, then
  //   3. a titleized codename so an agent we cannot place is never blank.
  // The role pool's FIRST entry is the role's primary persona; the batch resolver
  // (buildThemedRoster) is what walks the pool to keep duplicate-role agents
  // distinct. A single lookup has no roster context, so it takes the primary.
  const name =
    theme.nameByCodename[short] ||
    theme.namePoolByRole[role]?.[0] ||
    titleizeCodename(source.codename);
  // A per-codename custom role label wins over the role-wide label, so an
  // operator's "architect = Lead detective" does not relabel every architect.
  const roleLabel = theme.roleLabelByCodename?.[short] ?? theme.roleLabels[role];
  return { role, name, roleLabel };
}

/**
 * Resolve themed identities for a WHOLE roster at once, guaranteeing every agent
 * a DISTINCT display name (the product rule: no repeated names in a roster).
 *
 * Two phases, both deterministic and stable across reloads:
 *   1. Agents with an exact per-codename name (a canonical fleet slug, or an
 *      operator-named codename under the custom theme) take that name. These are
 *      already distinct by construction and preserve today's exact rendering.
 *   2. Every remaining custom agent is
 *      grouped by its derived role and, within a role, sorted by codename so the
 *      allocation never depends on roster order. Each is handed the next UNUSED
 *      name from its role pool; when that pool is exhausted it draws from the
 *      theme's flat pool, and only then falls back to a "Name 2" numeric suffix.
 *
 * The returned map is keyed by the NORMALIZED (short, lowercased) codename.
 */
export function buildThemedRoster(
  sources: readonly RoleSource[],
  themeId: RosterThemeId,
  custom: CustomRosterNames = EMPTY_CUSTOM_NAMES,
): Map<string, ThemedIdentity> {
  const theme = rosterThemeFor(themeId, custom);

  // Dedupe by normalized codename (keep the first source for each), so the same
  // agent reported twice does not consume two pool slots.
  const byCodename = new Map<string, RoleSource>();
  for (const source of sources) {
    const short = normalizeCodename(source.codename);
    if (short && !byCodename.has(short)) {
      byCodename.set(short, source);
    }
  }

  const roleOf = new Map<string, WorkflowRole>();
  for (const [short, source] of byCodename) {
    roleOf.set(short, deriveAgentRole(source));
  }

  const result = new Map<string, ThemedIdentity>();
  const used = new Set<string>();
  const claim = (name: string) => used.add(name.toLowerCase());

  const roleLabelFor = (short: string, role: WorkflowRole): string =>
    theme.roleLabelByCodename?.[short] ?? theme.roleLabels[role];

  // Phase 1: exact per-codename names.
  for (const [short] of byCodename) {
    const exact = theme.nameByCodename[short];
    if (exact) {
      const role = roleOf.get(short)!;
      result.set(short, { role, name: exact, roleLabel: roleLabelFor(short, role) });
      claim(exact);
    }
  }

  // Phase 2: remaining agents, grouped by role, sorted by codename, allocated a
  // distinct pool name each.
  const remainingByRole = new Map<WorkflowRole, string[]>();
  for (const [short] of byCodename) {
    if (result.has(short)) continue;
    const role = roleOf.get(short)!;
    const bucket = remainingByRole.get(role);
    if (bucket) {
      bucket.push(short);
    } else {
      remainingByRole.set(role, [short]);
    }
  }

  for (const [role, shorts] of remainingByRole) {
    shorts.sort();
    for (const short of shorts) {
      const name = allocateRoleName(short, role, theme, used);
      result.set(short, { role, name, roleLabel: roleLabelFor(short, role) });
      claim(name);
    }
  }

  return result;
}

/** Pick the next unused themed name for a role, then overflow to a suffix. */
function allocateRoleName(
  short: string,
  role: WorkflowRole,
  theme: RosterTheme,
  used: Set<string>,
): string {
  const isFree = (name: string) => !used.has(name.toLowerCase());
  const pool = theme.namePoolByRole[role] ?? [];
  const fromRole = pool.find(isFree);
  if (fromRole) return fromRole;
  const fromFlat = theme.namePool.find(isFree);
  if (fromFlat) return fromFlat;
  // Every real name is taken: suffix the role's primary persona (or the titleized
  // codename when the theme has no pool for this role) until it is unique.
  const base = pool[0] ?? theme.namePool[0] ?? titleizeCodename(short);
  let n = 2;
  while (!isFree(`${base} ${n}`)) n += 1;
  return `${base} ${n}`;
}

// The known fleet codenames the custom-theme editor lets the operator rename,
// each with its canonical role and the base-theme name as the placeholder.
// Drawn from the base theme so the editor always covers the full default roster.
export type EditableAgent = {
  codename: string;
  role: WorkflowRole;
  defaultName: string;
  defaultRoleLabel: string;
};

export type EditableAgentSource = RoleSource & {
  displayName?: string | null;
  roleLabel?: string | null;
};

function baseEditableAgent(codename: string): EditableAgent {
  const role = ROLE_BY_CODENAME[codename] ?? "ops";
  return {
    codename,
    role,
    defaultName: BASE_THEME.nameByCodename[codename] ?? titleizeCodename(codename),
    defaultRoleLabel: ROLE_LABELS_DEFAULT[role],
  };
}

function cleanLabel(value: string | null | undefined): string | null {
  const text = value?.trim();
  return text ? text : null;
}

export function editableAgents(sources: readonly EditableAgentSource[] = []): EditableAgent[] {
  const agents = new Map<string, EditableAgent>();
  for (const codename of Object.keys(BASE_THEME.nameByCodename)) {
    agents.set(codename, baseEditableAgent(codename));
  }

  for (const source of sources) {
    const codename = normalizeCodename(source.codename);
    if (!codename) continue;
    const identity = resolveThemedIdentity(source, "batman");
    const existing = agents.get(codename);
    agents.set(codename, {
      codename,
      role: identity.role,
      defaultName: existing?.defaultName ?? cleanLabel(source.displayName) ?? identity.name,
      defaultRoleLabel:
        existing?.defaultRoleLabel ?? cleanLabel(source.roleLabel) ?? identity.roleLabel,
    });
  }

  return Array.from(agents.values());
}
