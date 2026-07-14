import { listen } from "@tauri-apps/api/event";

import type { TabKey } from "./uiTypes";

export type AppMenuDestination = Extract<
  TabKey,
  "home" | "compose" | "pipeline" | "fleet" | "settings"
>;

export type AppMenuHandlers = {
  onNavigate: (destination: AppMenuDestination) => void;
  onCommandPalette: () => void;
  onRefresh: () => void;
};

type Unlisten = () => void;

const DESTINATIONS = new Set<AppMenuDestination>([
  "home",
  "compose",
  "pipeline",
  "fleet",
  "settings",
]);

export function listenAppMenuEvents(handlers: AppMenuHandlers): Promise<Unlisten> {
  if (!window.__TAURI_INTERNALS__) {
    return Promise.resolve(() => {});
  }

  const offs: Unlisten[] = [];
  const register = async () => {
    try {
      offs.push(
        await listen<string>("app-menu://navigate", ({ payload }) => {
          if (isAppMenuDestination(payload)) handlers.onNavigate(payload);
        }),
      );
      offs.push(
        await listen("app-menu://command-palette", () => handlers.onCommandPalette()),
      );
      offs.push(await listen("app-menu://refresh", () => handlers.onRefresh()));
    } catch (error) {
      for (const off of offs) off();
      throw error;
    }
  };

  return register().then(() => () => {
    for (const off of offs) off();
  });
}

function isAppMenuDestination(value: string): value is AppMenuDestination {
  return DESTINATIONS.has(value as AppMenuDestination);
}
