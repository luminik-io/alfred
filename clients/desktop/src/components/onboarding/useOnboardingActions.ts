import { useCallback } from "react";

import { errorDetail } from "../../api/client";
import { loadSchedule, saveSetupBattery } from "../../api/setup";
import type { CustomRosterNames } from "../../lib/agentThemes";
import type { NativeActionRequest } from "../../lib/uiTypes";
import type { NativeCommandResult, OnboardingAction, SetupStatus } from "../../types";
import type { OnboardingActionResult } from "./OnboardingConversePanel";

/**
 * Coerce a loosely-typed action-arg value into a string->string record, keeping
 * only string values. The onboarding action args arrive as `Record<string,
 * unknown>` (validated + bounded server-side); this narrows the theme maps to
 * the `CustomRosterNames` shape without trusting the wire type. Returns null when
 * the value is not an object, so the caller can degrade gracefully.
 */
function asStringRecord(value: unknown): Record<string, string> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const out: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    if (typeof raw === "string") out[key] = raw;
  }
  return out;
}

/**
 * Map an onboarding cadence (off/hourly/daily/weekly, the vocabulary the server
 * validates) to the canonical schedule string the native `alfred schedule set`
 * primitive accepts (see bin/alfred-schedule.py canonical_schedule). `off` has
 * no per-agent "set" form; it is handled separately by unloading the scheduler
 * with the existing `pause` action. Returns null for an unknown cadence so the
 * caller can report a real failure instead of writing a bogus schedule.
 */
function scheduleForCadence(cadence: string): string | null {
  switch (cadence) {
    case "hourly":
      return "1h";
    case "daily":
      return "daily@09:00";
    case "weekly":
      return "weekly@mon:09:00";
    default:
      return null;
  }
}

type OnboardingActionDeps = {
  baseUrl: string;
  canMutate: boolean;
  canRun: boolean;
  connected: boolean;
  githubConnected: boolean;
  refreshStatus: () => Promise<SetupStatus | null>;
  startGithubAuthLogin: () => Promise<boolean>;
  onRunLocalAction: (request: NativeActionRequest) => Promise<NativeCommandResult | null>;
  onSaveCustomNames: (next: CustomRosterNames) => Promise<void>;
  onBatteriesDecision: () => void;
  onSlackDecision: () => void;
  onOpenSlackSetup: () => void;
  onOpenRepoSetup: () => void;
};

/**
 * The onboarding action executor shared by the conversational guide. This is the
 * single source of truth: every branch runs the SAME handler the stepped flow
 * already uses (the GitHub device flow, saveSetupRepos, onSaveCustomNames,
 * refreshStatus), never a duplicate config write. The conversational panel only
 * requests an action; this executor runs it under the same token gate and
 * returns a plain result note the panel threads back into the chat.
 */
export function useOnboardingActions({
  baseUrl,
  canMutate,
  canRun,
  connected,
  githubConnected,
  refreshStatus,
  startGithubAuthLogin,
  onRunLocalAction,
  onSaveCustomNames,
  onBatteriesDecision,
  onSlackDecision,
  onOpenSlackSetup,
  onOpenRepoSetup,
}: OnboardingActionDeps): (action: OnboardingAction) => Promise<OnboardingActionResult> {
  return useCallback(
    async (action: OnboardingAction): Promise<OnboardingActionResult> => {
      try {
        switch (action.tool) {
          case "check_engine": {
            // Read the FRESH status the refresh just fetched, not the closed-over
            // `status`/`engineReady` render values (those are only scheduled state
            // updates and would report stale "no engine" on a first run).
            const fresh = await refreshStatus();
            const engines = (fresh?.engines ?? [])
              .filter((engine) => engine.ready)
              .map((engine) => engine.display_name);
            if (Boolean(fresh?.engine_ready) || engines.length > 0) {
              const list = engines.length ? engines.join(" and ") : "a coding engine";
              return { ok: true, note: `Found ${list} on this Mac.` };
            }
            return {
              ok: false,
              note: "No compatible coding engine is ready. Install or sign in to Claude Code or Codex, then try again.",
            };
          }
          case "connect_github": {
            if (githubConnected) {
              return { ok: true, note: "GitHub is already connected." };
            }
            if (!canRun || !connected) {
              return {
                ok: false,
                note: "GitHub sign-in needs the local runtime. Open the desktop app, then retry.",
              };
            }
            // Use the FRESH verdict the device flow settled on, not the stale
            // pre-action `githubConnected` render value: a flow that just
            // succeeded must report success to the next model turn.
            const connectedAfter = await startGithubAuthLogin();
            await refreshStatus();
            return connectedAfter
              ? { ok: true, note: "GitHub is connected." }
              : {
                  ok: false,
                  note: "Started GitHub sign-in. Finish it in your browser, then tell me when it is done.",
                };
          }
          case "set_repos": {
            if (!canMutate) {
              return {
                ok: false,
                note: "I cannot save repos in this read-only preview. Use the step-by-step setup to pick repos.",
              };
            }
            const repos = Array.isArray(action.args.repos)
              ? action.args.repos.filter((repo): repo is string => typeof repo === "string")
              : [];
            if (!repos.length) {
              return {
                ok: false,
                note: "No valid repo names came through. Which repos should I watch?",
              };
            }
            onOpenRepoSetup();
            return {
              ok: false,
              note: `Choose the local checkout folder for ${repos.join(", ")} in the repository step. Alfred verifies each GitHub origin before saving or indexing.`,
            };
          }
          case "pick_agents": {
            // The fleet is fixed; picking agents is a display preference the
            // person can refine on the Team step. Acknowledge without a write.
            const roles = Array.isArray(action.args.roles)
              ? action.args.roles.filter((role): role is string => typeof role === "string")
              : [];
            const note = roles.length
              ? `Noted: ${roles.join(", ")}. You can fine-tune names on the Team step.`
              : "The full senior-engineering team is ready. You can rename it next.";
            return { ok: true, note };
          }
          case "propose_theme": {
            // A proposal is a preview, not a save. Surface it; the person confirms
            // by asking to save (the model then sends save_theme).
            return {
              ok: true,
              note: "Here is a proposed team. Say the word and I will save it, or ask for tweaks.",
            };
          }
          case "save_theme": {
            const names = asStringRecord(action.args.custom_names);
            const roles = asStringRecord(action.args.custom_roles);
            if (!names || Object.keys(names).length === 0) {
              return {
                ok: false,
                note: "That team was not complete. Let's name every core role first.",
              };
            }
            await onSaveCustomNames({ names, roles: roles ?? {} });
            return { ok: true, note: "Saved your team names." };
          }
          case "set_batteries": {
            // Configurable tools. Native setup prepares local dependencies,
            // then the live setup API performs the only configuration write.
            // External daemons remain explicit.
            // Unknown ids and built-ins are refused.
            if (!canMutate) {
              return {
                ok: false,
                note: "I cannot change tools in this read-only preview. Open Tools included to review them.",
              };
            }
            if (canRun && !connected) {
              return {
                ok: false,
                note: "Connect to the Alfred runtime before changing tools.",
              };
            }
            const enableIds = Array.isArray(action.args.enable)
              ? action.args.enable.filter((id): id is string => typeof id === "string")
              : [];
            const disableIds = Array.isArray(action.args.disable)
              ? action.args.disable.filter((id): id is string => typeof id === "string")
              : [];
            const changes = [
              ...enableIds.map((id) => ({ id, enabled: true })),
              ...disableIds.map((id) => ({ id, enabled: false })),
            ];
            if (!changes.length) {
              return {
                ok: false,
                note: "No tool changes came through. Open Tools included to review them.",
              };
            }
            const changed: Array<{ id: string; enabled: boolean }> = [];
            const failed: string[] = [];
            for (const change of changes) {
              try {
                if (change.enabled && canRun) {
                  const result = await onRunLocalAction({
                    action: "battery_install",
                    target: change.id,
                    refreshAfter: false,
                  });
                  if (!result?.success) throw new Error("battery install failed");
                }
                await saveSetupBattery(baseUrl, change.id, change.enabled);
                changed.push(change);
              } catch {
                failed.push(change.id);
              }
            }
            await refreshStatus();
            if (!changed.length) {
              return {
                ok: false,
                note: "I could not change those tools. Open Tools included to review them, or run `alfred batteries`.",
              };
            }
            onBatteriesDecision();
            const enabled = changed.filter((item) => item.enabled).map((item) => item.id);
            const disabled = changed.filter((item) => !item.enabled).map((item) => item.id);
            const parts = [
              enabled.length ? `Turned on ${enabled.join(", ")}.` : "",
              disabled.length ? `Turned off ${disabled.join(", ")}.` : "",
              failed.length ? `Could not change ${failed.join(", ")}.` : "",
            ].filter(Boolean);
            return {
              ok: true,
              note: parts.join(" "),
            };
          }
          case "skip_batteries":
            onBatteriesDecision();
            return { ok: true, note: "Keeping Alfred's included tools." };
          case "open_slack_setup":
            // Slack credentials never enter this action or the transcript. Move
            // to the existing token-gated local step, which owns Slack setup.
            onOpenSlackSetup();
            return { ok: true, note: "Opened the native Slack setup step." };
          case "skip_slack":
            onSlackDecision();
            return { ok: true, note: "Skipping Slack for now. You can add it later." };
          case "set_schedule": {
            // Persist the cadence through the SAME native primitive the Fleet view
            // uses (`alfred schedule set` / `pause`), never a fake acknowledgement.
            // `off` unloads the scheduler via the existing pause action; a cadence
            // is applied to every currently scheduled agent via `schedule set`.
            const cadence =
              typeof action.args.cadence === "string" ? action.args.cadence : "daily";
            if (!canRun || !connected) {
              return {
                ok: false,
                note: "Setting a schedule needs the local runtime. Open the desktop app, then retry.",
              };
            }
            if (cadence === "off") {
              const result = await onRunLocalAction({
                action: "pause",
                target: "all",
                refreshAfter: true,
              });
              if (!result || !result.success) {
                return {
                  ok: false,
                  note: "Could not pause the schedule. Try again, or set it on the Fleet page.",
                };
              }
              return { ok: true, note: "Paused the schedule. Alfred will only run when you ask." };
            }
            const mapped = scheduleForCadence(cadence);
            if (mapped === null) {
              return {
                ok: false,
                note: "I did not recognize that cadence. Try off, hourly, daily, or weekly.",
              };
            }
            // Re-cadence every scheduled agent through the native primitive. Read
            // the live schedule so we target the agents that actually exist.
            let runs: { codename: string }[] = [];
            try {
              runs = (await loadSchedule(baseUrl)).runs ?? [];
            } catch {
              runs = [];
            }
            const codenames = Array.from(
              new Set(runs.map((run) => run.codename).filter((name): name is string => Boolean(name))),
            );
            if (!codenames.length) {
              return {
                ok: false,
                note: "No scheduled agents are set up yet, so there is nothing to re-time. You can set schedules on the Fleet page after setup.",
              };
            }
            let applied = 0;
            for (const codename of codenames) {
              const result = await onRunLocalAction({
                action: "schedule",
                target: codename,
                cadence: mapped,
                refreshAfter: false,
              });
              if (result && result.success) applied += 1;
            }
            if (applied === 0) {
              return {
                ok: false,
                note: "Could not save the schedule. Try again, or set it on the Fleet page.",
              };
            }
            await refreshStatus();
            return { ok: true, note: `Alfred will sweep for work ${cadence}.` };
          }
          case "finish_setup": {
            const fresh = await refreshStatus();
            if (!fresh?.first_run?.ready) {
              return {
                ok: false,
                note:
                  fresh?.first_run?.headline ||
                  "Finish the required setup checks before completing setup.",
              };
            }
            return {
              ok: true,
              note: "Setup is ready. Choose Alfred's first job next.",
            };
          }
          default:
            return { ok: false, note: "I do not know how to do that step yet." };
        }
      } catch (err) {
        return {
          ok: false,
          note: errorDetail(err) || (err instanceof Error ? err.message : "That step failed."),
        };
      }
    },
    [
      baseUrl,
      canMutate,
      canRun,
      connected,
      githubConnected,
      onBatteriesDecision,
      onOpenRepoSetup,
      onOpenSlackSetup,
      onSlackDecision,
      onRunLocalAction,
      onSaveCustomNames,
      refreshStatus,
      startGithubAuthLogin,
    ],
  );
}
