import { Bot, Plus } from "lucide-react";
import { useMemo } from "react";

import {
  type CustomRosterNames,
  editableAgents,
  resolveThemedIdentity,
  rosterThemeBlurb,
  rosterThemeLabel,
  type RosterThemeId,
} from "../../lib/agentThemes";
import { RosterThemePicker } from "../RosterThemePicker";
import { Button } from "../ui";

// The first four distinct roles from the editable roster, used for the Team
// step's live preview. Computed once at module load since the role set is fixed.
const ROSTER_PREVIEW_AGENTS = (() => {
  const seenRoles = new Set<string>();
  const agents: ReturnType<typeof editableAgents> = [];
  for (const agent of editableAgents()) {
    if (seenRoles.has(agent.role)) continue;
    seenRoles.add(agent.role);
    agents.push(agent);
    if (agents.length === 4) break;
  }
  return agents;
})();

// Step 5 (Team): pick a roster display theme / custom names while the underlying
// senior-engineering roles stay fixed. Purely cosmetic; offers a path to add a
// custom agent when the shipped roster is not enough.
export function RosterThemeStep({
  customNames,
  rosterTheme,
  saveError,
  onChange,
  onEditCustom,
  onOpenCustomAgents,
}: {
  customNames: CustomRosterNames;
  rosterTheme: RosterThemeId;
  saveError: string | null;
  onChange: (next: RosterThemeId) => void;
  onEditCustom: () => void;
  onOpenCustomAgents?: () => void;
}) {
  const preview = useMemo(
    () =>
      ROSTER_PREVIEW_AGENTS.map(({ codename }) => ({
        codename,
        identity: resolveThemedIdentity({ codename }, rosterTheme, customNames),
      })),
    [customNames, rosterTheme],
  );

  return (
    <div className="space-y-4">
      <RosterThemePicker
        value={rosterTheme}
        onChange={onChange}
        onEditCustom={onEditCustom}
        saveError={saveError}
      />
      <div className="grid gap-3 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="rounded-lg border border-border/70 bg-card/60 p-4">
          <p className="text-xs font-medium uppercase text-muted-foreground">Active roster</p>
          <h3 className="mt-1 text-lg font-medium text-foreground">
            {rosterThemeLabel(rosterTheme)}
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            {rosterThemeBlurb(rosterTheme)}
          </p>
        </div>
        <div className="rounded-lg border border-border/70 bg-card/60 p-4">
          <p className="text-xs font-medium uppercase text-muted-foreground">Preview</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {preview.map(({ codename, identity }) => (
              <div key={codename} className="rounded-md border border-border/60 bg-background/40 p-3">
                <p className="text-sm font-medium text-foreground">{identity.name}</p>
                <p className="text-xs text-muted-foreground">{identity.roleLabel}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Roles, permissions, schedules, labels, worktrees, and merge gates stay unchanged.
      </p>
      {onOpenCustomAgents ? (
        <div className="rounded-lg border border-border/70 bg-card/60 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 gap-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-md border border-primary/25 bg-primary/10 text-primary">
                <Bot size={17} aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <p className="text-sm font-medium text-foreground">Need another role?</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Add a custom agent with its own engine, prompt, schedule, and repo scope.
                </p>
              </span>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={onOpenCustomAgents}>
              <Plus size={15} aria-hidden="true" />
              <span>Add custom agent</span>
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
