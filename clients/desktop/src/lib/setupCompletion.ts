import type { SetupStatus } from "../types";

/** Canonical boot gate shared with the guided and conversational setup flows. */
export function isSetupComplete(status: SetupStatus | null | undefined): boolean {
  return status?.first_run?.ready === true;
}
