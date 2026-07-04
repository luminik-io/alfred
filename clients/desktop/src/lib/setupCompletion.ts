import type { SetupStatus } from "../types";

/**
 * Whether the local Alfred setup is complete enough to skip the onboarding
 * takeover on boot and land the user straight on the Inbox.
 *
 * The signal is deliberately based on REAL server-reported setup state, not the
 * mere presence of a runtime directory: a half-initialised install (a runtime
 * folder exists, but no coding engine is detected, GitHub is not connected, or
 * no repository is selected) must still land the user in onboarding so they can
 * finish. The three substantive gates that make Alfred actually usable are:
 *
 *   - a coding engine (Claude Code / Codex) is detected and ready,
 *   - GitHub is connected, and
 *   - at least one repository is selected for Alfred to work in.
 *
 * A returning user who has completed all three boots straight to the Inbox as
 * before; a fresh or partially-configured machine is routed to onboarding.
 *
 * `install.initialized` alone is treated as a NECESSARY-not-sufficient hint: if
 * the server explicitly says the install was never initialised, setup is not
 * complete regardless of the other flags (guards against a stale cache).
 */
export function isSetupComplete(status: SetupStatus | null | undefined): boolean {
  if (!status) return false;

  // If the server tells us the install was never initialised, it is not complete.
  if (status.install && status.install.initialized === false) return false;

  const engineReady = Boolean(status.engine_ready);
  const githubConnected = Boolean(status.github?.ok);
  const reposSelected = (status.repos?.count ?? 0) > 0;

  return engineReady && githubConnected && reposSelected;
}
