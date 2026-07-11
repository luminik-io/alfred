import type { ThemeBuilderRequest, ThemeBuilderResponse } from "../types";
import { readAlfredJson, writeAlfredJson } from "./client";

// The persisted roster theme shared across surfaces. The desktop reads this on
// connect so its picker reflects the choice the runtime already holds (which
// the Slack path also honors), and writes it back when the operator picks a
// theme or edits a custom name. `custom_names` / `custom_roles` carry the
// operator-authored `custom` theme; presets leave them empty. Mirrors
// `lib/roster_theme_store.py:RosterThemeState.to_dict`.
export type RosterThemeResponse = {
  theme: string;
  custom_names: Record<string, string>;
  custom_roles: Record<string, string>;
  updated_at: string | null;
};

export type RosterThemeWrite = {
  theme: string;
  custom_names?: Record<string, string>;
  custom_roles?: Record<string, string>;
};

// Read-only GET, no token: any surface may learn the active roster theme.
export async function loadRosterTheme(baseUrl: string): Promise<RosterThemeResponse> {
  return readAlfredJson<RosterThemeResponse>(baseUrl, "/api/roster-theme");
}

// Persist the chosen theme + custom name/role maps. Token-gated server-side via
// _authorized_mutation; the native bridge attaches the per-launch token.
export async function saveRosterTheme(
  baseUrl: string,
  body: RosterThemeWrite,
): Promise<RosterThemeResponse> {
  return writeAlfredJson<RosterThemeResponse>(baseUrl, "/api/roster-theme", body);
}

// One turn of the conversational roster theme builder. The server asks a short
// vibe question, then proposes a full role-slug -> display-name mapping as a
// `propose_theme` action. When no live engine is configured the server returns a
// 503 with `error: "live_session_unavailable"`; the caller catches that (via
// isLiveSessionUnavailable) and falls back to the manual custom theme editor.
// Nothing is saved here: the client pre-fills the editor with the proposal and
// saves it via saveRosterTheme only after the person confirms.
export async function themeBuilderConverse(
  baseUrl: string,
  request: ThemeBuilderRequest,
  signal?: AbortSignal,
): Promise<ThemeBuilderResponse> {
  return writeAlfredJson(baseUrl, "/api/theme-builder/converse", request, signal);
}
