import { ROSTER_MANIFEST } from "./rosterManifest";

// The delivery roster as a set of named ROLES, not a fixed list of codenames.
// Each agent the runtime reports is mapped to one of these roles from its own
// role metadata (with a name-keyed hint table for the default fleet), so the
// canvas and roster render the WHOLE fleet, not a hardcoded subset. An agent
// whose role we cannot place still lands in a sensible fallback lane and is
// never dropped.
//
// A "lane" and a "role" are the same axis here: the canonical engineering
// stages, left to right. The themed display name + role label come from a
// separate theme mapping (see agentThemes.ts); this module is identity-free.

// Canonical role slugs, matching the shared roster manifest (lib/roster_manifest.json).
// Each is both an agent identity and a pipeline lane. The Batman-cast codenames
// (Lucius, Drake, ...) are display names layered on top by the theme mapping;
// this axis is identity-free.
export type WorkflowRole =
  | "triage"
  | "spec-planner"
  | "planner"
  | "architect"
  | "senior-dev"
  | "test-engineer"
  | "fixer"
  | "reviewer"
  | "e2e-runner"
  | "ops-watch"
  | "ship"
  | "ops";

// Ordered roles (left to right). This is the canonical pipeline order the
// canvas lays out and the order the list view groups by.
export const WORKFLOW_ROLES: readonly WorkflowRole[] = [
  "triage",
  "spec-planner",
  "planner",
  "architect",
  "senior-dev",
  "test-engineer",
  "fixer",
  "reviewer",
  "e2e-runner",
  "ship",
  "ops-watch",
  "ops",
] as const;

// The lane any agent we cannot place falls into, so an unknown agent the fleet
// reports still appears on the canvas rather than vanishing.
export const FALLBACK_ROLE: WorkflowRole = "ops";

// Plain stage headings used as the lane labels on the canvas. These are stage
// names, not agent names, so they are identical across every roster theme.
export const ROLE_LANE_LABEL: Record<WorkflowRole, string> = {
  triage: "Triage & plan",
  "spec-planner": "Spec planning",
  planner: "Planning",
  architect: "Architect",
  "senior-dev": "Implement",
  "test-engineer": "Tests",
  fixer: "Review fixes",
  reviewer: "Review",
  "e2e-runner": "End-to-end",
  "ops-watch": "Ops watch",
  ship: "Ship",
  ops: "Ops & health",
};

// Canonical handoffs between ROLES (source role -> target role). A real run does
// not traverse every edge, but this is the shape work takes through the fleet.
// Edges are drawn between the agents that occupy each role, so adding an agent
// to a role automatically wires it into the flow without touching a name list.
export const ROLE_EDGES: readonly [WorkflowRole, WorkflowRole][] = [
  ["triage", "architect"],
  ["triage", "senior-dev"],
  ["architect", "senior-dev"],
  ["senior-dev", "reviewer"],
  ["reviewer", "ship"],
  ["ship", "ops"],
];

// Short verbs for each canonical handoff, so a newcomer can read the pipeline
// (plan -> approve -> build -> review -> merge) straight off the edges instead
// of inferring it from lane order alone. Keyed by "source->target" role pair.
export const ROLE_EDGE_LABEL: Record<string, string> = {
  "triage->architect": "scope",
  "triage->senior-dev": "approved plan",
  "architect->senior-dev": "design",
  "senior-dev->reviewer": "pull request",
  "reviewer->ship": "approved",
  "ship->ops": "merged",
};

// The single handoff that carries the human approval gate (the Drake gate): a
// plan only becomes implementation work once the operator approves it. Rendered
// distinctly so the most important interaction in Alfred reads at a glance.
export const APPROVAL_GATE_ROLE_EDGE = "triage->senior-dev";

// Name-keyed role hints for the default fleet. The runtime does not always send
// a machine-readable role for every agent, so we seed the canonical codenames
// here. This is a HINT, not a gate: an agent absent from this table is still
// placed by its reported role metadata or, failing that, the fallback lane.
export const CODENAME_ROLE_HINTS: Record<string, WorkflowRole> = Object.fromEntries(
  ROSTER_MANIFEST.agents.map((agent) => [agent.codename, agent.role]),
);

/** Slugify a themed display name into the short codename form we hint on. */
function slugifyName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .trim();
}

// Legacy-install role hints. Before the role-slug rename, agents.conf codenames
// WERE the Batman-cast display names (``lucius``, ``robin``, ``rasalghul``, ...),
// so an install that predates the rename never matches the slug hint table above.
// We derive a second hint table straight from each manifest agent's Batman name
// (slugified: "Ra's al Ghul" -> "rasalghul", "Auto-merge" -> "automerge") so a
// legacy codename still resolves to its canonical role and gets re-themed like any
// other agent. Kept in lockstep with the manifest, so it never drifts.
export const LEGACY_NAME_ROLE_HINTS: Record<string, WorkflowRole> = Object.fromEntries(
  ROSTER_MANIFEST.agents.map((agent) => [slugifyName(agent.names.batman), agent.role]),
);

// Exact role descriptors generated by alfred-init into agents.conf. These are
// intentionally checked before fuzzy keyword buckets so strings like "code map
// refresh" stay in ops instead of matching the generic "code" implement bucket.
const ROLE_TITLE_HINTS: Record<string, WorkflowRole> = {
  "feature dev": "senior-dev",
  "issue planner": "planner",
  "test coverage": "test-engineer",
  "pr review": "reviewer",
  "ci repair": "fixer",
  "bug triage": "triage",
  "cross repo architect": "architect",
  "staging smoke runner": "e2e-runner",
  "ops morning": "ops-watch",
  "pr automerge": "ship",
  "agent cleanup": "ops",
  "memory harvest": "ops",
  "memory auto promote": "ops",
  "code map refresh": "ops",
  "morning brief": "ops",
  "fleet doctor": "ops",
  "fleet recap morning": "ops",
  "fleet recap evening": "ops",
  "shipped summary daily": "ship",
  "shipped summary weekly": "ship",
};

// Keyword buckets used to infer a role from a free-text role title or purpose
// when the runtime reports one but the codename is unknown to us. Ordered by
// specificity so "reviewer" wins over a generic "engineer" mention.
const ROLE_KEYWORDS: ReadonlyArray<[WorkflowRole, readonly string[]]> = [
  ["reviewer", ["review", "reviewer", "qa", "quality", "approve", "gatekeep"]],
  ["ship", ["ship", "merge", "release", "deploy", "publish"]],
  ["architect", ["architect", "plan", "design", "spec", "lead"]],
  ["triage", ["triage", "intake", "groom", "scope", "manager", "product"]],
  ["senior-dev", ["implement", "develop", "engineer", "build", "code", "fix"]],
  [
    "ops",
    [
      "ops",
      "health",
      "monitor",
      "doctor",
      "cleanup",
      "harvest",
      "memory",
      "brief",
      "recap",
      "summary",
      "telemetry",
      "infra",
      "uptime",
    ],
  ],
];

/** Normalize a codename to the short, lowercase form we hint on. */
function shortCodename(codename: string): string {
  return (codename.split(".").pop() || codename).trim().toLowerCase();
}

/**
 * The display fields a workflow node needs, joined from the live roster row by
 * the caller. `role` here is the canonical WorkflowRole; the human role *label*
 * and themed name are layered on separately.
 */
export type RoleSource = {
  codename: string;
  // Free-text role title the runtime reports, if any (e.g. "Senior Developer").
  roleTitle?: string | null;
  // Free-text purpose, used only as a weak secondary signal.
  purpose?: string | null;
};

/**
 * Derive an agent's canonical role from, in priority order:
 *   1. the codename hint table (covers the default fleet exactly), then
 *   2. keyword inference over the reported role title, then
 *   3. keyword inference over the reported purpose, then
 *   4. the fallback lane, so nothing is ever dropped.
 * Pure and deterministic.
 */
export function deriveAgentRole(source: RoleSource): WorkflowRole {
  const short = shortCodename(source.codename);
  const hinted = CODENAME_ROLE_HINTS[short];
  if (hinted) {
    return hinted;
  }
  // A legacy install's codename is a Batman-cast name; resolve it to its
  // canonical role so the theme layer can re-skin it (BUG: legacy codenames
  // never re-themed). Checked before the free-text keyword buckets because the
  // exact legacy name is a stronger signal than a fuzzy title match.
  const legacy = LEGACY_NAME_ROLE_HINTS[short];
  if (legacy) {
    return legacy;
  }
  const fromTitle = inferRoleFromText(source.roleTitle);
  if (fromTitle) {
    return fromTitle;
  }
  const fromPurpose = inferRoleFromText(source.purpose);
  if (fromPurpose) {
    return fromPurpose;
  }
  return FALLBACK_ROLE;
}

export function isKnownFleetCodename(codename: string): boolean {
  const short = shortCodename(codename);
  // A legacy install's Batman-cast codename (``lucius``) is just as much a known
  // fleet agent as the canonical slug (``senior-dev``); treat both as known so a
  // preset owns its themed display name instead of deferring to the raw
  // server/schedule label (which on a legacy install is the un-themed Batman name).
  return (
    CODENAME_ROLE_HINTS[short] !== undefined || LEGACY_NAME_ROLE_HINTS[short] !== undefined
  );
}

export function scheduleRoleLabelForEditor({
  codename,
  role,
  roleTitle,
}: {
  codename: string;
  role?: string | null;
  roleTitle?: string | null;
}): string | null {
  const title = roleTitle?.trim();
  if (title) {
    return title;
  }
  if (isKnownFleetCodename(codename)) {
    return null;
  }
  const configured = role?.trim();
  return configured || null;
}

function inferRoleFromText(text: string | null | undefined): WorkflowRole | null {
  if (!text) {
    return null;
  }
  const haystack = text.toLowerCase();
  const normalized = haystack.replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
  const hinted = ROLE_TITLE_HINTS[normalized];
  if (hinted) {
    return hinted;
  }
  for (const [role, keywords] of ROLE_KEYWORDS) {
    if (keywords.some((keyword) => haystack.includes(keyword))) {
      return role;
    }
  }
  return null;
}

export function roleOrder(role: WorkflowRole): number {
  const index = WORKFLOW_ROLES.indexOf(role);
  return index === -1 ? WORKFLOW_ROLES.length : index;
}
