import type { SetupStatus } from "../types";

/**
 * Whether the local Alfred setup is complete enough to skip the onboarding
 * takeover on boot and land the user straight on the Inbox.
 *
 * The signal is deliberately based on REAL server-reported setup state, not the
 * mere presence of a runtime directory: a half-initialised install (a runtime
 * folder exists, but no coding engine is detected, GitHub is not connected, or
 * no repository is selected) must still land the user in onboarding so they can
 * finish. The substantive gates that make Alfred actually usable are:
 *
 *   - a coding engine (Claude Code / Codex) is detected and ready,
 *   - GitHub is connected,
 *   - at least one repository is selected for Alfred to work in, and
 *   - a scheduled fleet is actually deployed (an `agents.conf` exists AND at
 *     least one scheduled run is configured).
 *
 * The fleet gate is what distinguishes a genuinely set-up machine from one that
 * merely has repo scope. Engine, GitHub, and repo scope can all be present
 * without any setup having run: repo scope in particular is often inherited from
 * the shell environment (`ALFRED_SHIPPED_REPOS` and friends), so a machine that
 * never ran setup/deploy reports a selected repo while having no `agents.conf`
 * and zero scheduled agents. Such an install is NOT set up and must land on the
 * onboarding takeover, not on an empty Inbox.
 *
 * A returning user whose fleet is deployed boots straight to the Inbox as
 * before; a fresh or partially-configured machine is routed to onboarding.
 *
 * `install.initialized` alone is treated as a NECESSARY-not-sufficient hint: if
 * the server explicitly says the install was never initialised, setup is not
 * complete regardless of the other flags (guards against a stale cache). When
 * the server does not report install state at all (an older runtime), the fleet
 * gate is skipped so a working returning user is never trapped in onboarding.
 */
export function isSetupComplete(status: SetupStatus | null | undefined): boolean {
  if (!status) return false;

  // If the server tells us the install was never initialised, it is not complete.
  if (status.install && status.install.initialized === false) return false;

  const engineReady = Boolean(status.engine_ready);
  const githubConnected = Boolean(status.github?.ok);
  const reposSelected = (status.repos?.count ?? 0) > 0;

  // A deployed, scheduled fleet is the signal that setup actually ran. When the
  // server reports the fleet inventory, require both an agents.conf and at least
  // one scheduled run. When it does not report install state at all, OR reports
  // an install object from an older runtime that predates the fleet-inventory
  // fields, fall back to the core gates rather than yanking a working returning
  // user into onboarding on a missing field.
  const install = status.install;
  const hasFleetInventory =
    typeof install?.agents_conf_present === "boolean" &&
    typeof install?.scheduled_runs === "number";
  const fleetDeployed =
    install && hasFleetInventory
      ? install.agents_conf_present && install.scheduled_runs > 0
      : true;

  return engineReady && githubConnected && reposSelected && fleetDeployed;
}
