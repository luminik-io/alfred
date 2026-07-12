import { CheckCircle2, CircleDashed, Package, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { errorDetail } from "../../api/client";
import { loadSetupBatteries, saveSetupBattery } from "../../api/setup";
import type { NativeCommandResult, SetupBattery, SetupBatteryManifest } from "../../types";
import type { NativeActionRequest } from "../../lib/uiTypes";
import { Badge, Button, Card, CardContent, Switch } from "../ui";
import { cn } from "@/lib/utils";
import type { OnboardingNotice } from "./types";

// A short, friendly badge for how a battery is obtained. Truthful, no hype: it
// tells the person exactly what turning it on will still require.
function requirementLabel(battery: SetupBattery): string {
  if (battery.requires_daemon) return `needs ${battery.service}`;
  if (battery.install_kind === "pip-extra" && battery.pip_extra) {
    return `pip extra: ${battery.pip_extra}`;
  }
  if (battery.install_kind === "pip-extra") return "extra package";
  if (battery.install_kind === "autofetch") return "auto-fetched";
  return "no setup";
}

function statusBadge(battery: SetupBattery): { label: string; variant: "secondary" | "outline" } {
  switch (battery.status) {
    case "included":
      return { label: "included", variant: "secondary" };
    case "enabled":
      return { label: "on", variant: "secondary" };
    case "available":
      return { label: "ready to turn on", variant: "outline" };
    default:
      return { label: "needs install", variant: "outline" };
  }
}

/**
 * Optional batteries step. Reads the shared manifest (GET /api/setup/batteries),
 * shows the always-on built-ins as "included, no setup", and lets the person
 * toggle each opt-in enhancement. Native onboarding runs the real battery CLI,
 * which installs local dependencies before enabling them; external daemons stay
 * explicitly marked until reachable. Browser-only setup can still persist the
 * configuration and shows the remaining requirement. Alfred works fully with
 * zero optional batteries, so this whole step is skippable.
 */
export function BatteryPickerStep({
  baseUrl,
  canMutate,
  canRun = false,
  connected = true,
  onRunLocalAction,
  onSaved,
  setNotice,
}: {
  baseUrl: string;
  canMutate: boolean;
  canRun?: boolean;
  connected?: boolean;
  onRunLocalAction?: (
    request: NativeActionRequest,
  ) => Promise<NativeCommandResult | null>;
  onSaved?: () => Promise<void>;
  setNotice: (notice: OnboardingNotice) => void;
}) {
  const [manifest, setManifest] = useState<SetupBatteryManifest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await loadSetupBatteries(baseUrl);
      setManifest(result);
      setError(result.error || null);
    } catch (err) {
      setError(errorDetail(err) || "Could not load the batteries.");
    } finally {
      setLoading(false);
    }
  }, [baseUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (battery: SetupBattery, next: boolean) => {
    setPending(battery.id);
    try {
      if (next && canRun && !connected) {
        throw new Error("Connect to the Alfred runtime before installing a battery.");
      }
      if (next && canRun) {
        const nativeResult = await onRunLocalAction?.({
          action: "battery_enable",
          target: battery.id,
          refreshAfter: true,
        });
        if (!nativeResult?.success) {
          throw new Error(nativeResult?.message || `Could not install ${battery.name}.`);
        }
      }
      // The native CLI performs the install and writes the durable env file.
      // This idempotent API write mirrors the result into the running server so
      // the manifest and subsequent agent calls see it without a restart.
      const result = await saveSetupBattery(baseUrl, battery.id, next);
      setManifest(result.manifest);
      const verb = next ? "on" : "off";
      const current = result.manifest.batteries.find((row) => row.id === battery.id);
      const tail =
        next && current?.status === "not_installed"
          ? ` Configuration saved; it still needs ${current.service || current.install_hint}.`
          : "";
      setNotice({ tone: "ok", message: `Turned ${battery.name} ${verb}.${tail}` });
      await onSaved?.();
    } catch (err) {
      setNotice({
        tone: "error",
        message: errorDetail(err) || `Could not change ${battery.name}.`,
      });
    } finally {
      setPending(null);
    }
  };

  const builtins = (manifest?.batteries ?? []).filter((b) => b.builtin);
  const optIns = (manifest?.batteries ?? []).filter((b) => !b.builtin);

  return (
    <div className="grid gap-3">
      <Card size="sm" className="rounded-lg border-border/70 bg-muted/25 shadow-none">
        <CardContent className="px-3 py-2 text-sm text-muted-foreground">
          Alfred works fully with none of these. The built-ins below are always on. The optional
          batteries add better recall, more token savings, or a live code graph. Turn on what you
          want; you can change this any time with <code>alfred batteries</code>.
        </CardContent>
      </Card>

      {error ? (
        <Card size="sm" className="rounded-lg border-border/70 bg-muted/35 shadow-none">
          <CardContent className="px-3 text-sm text-muted-foreground">{error}</CardContent>
        </Card>
      ) : null}

      {loading && !manifest ? (
        <Button variant="outline" className="w-fit" type="button" disabled>
          <RefreshCw size={14} aria-hidden="true" className="animate-spin" />
          <span>Loading batteries</span>
        </Button>
      ) : null}

      {builtins.length ? (
        <section aria-label="Included, always on" className="grid gap-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Included, no setup
          </h3>
          {builtins.map((battery) => (
            <div
              key={battery.id}
              className="grid grid-cols-[auto_1fr] gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-sm"
            >
              <CheckCircle2 size={15} className="mt-0.5 text-primary" aria-hidden="true" />
              <div className="min-w-0">
                <span className="flex flex-wrap items-center gap-1.5">
                  <strong className="font-medium text-foreground">{battery.name}</strong>
                  <Badge variant="outline" className="font-normal">
                    {battery.category}
                  </Badge>
                  <Badge variant="secondary" className="font-normal">
                    included
                  </Badge>
                </span>
                <span className="block text-xs text-muted-foreground">{battery.how_it_helps}</span>
              </div>
            </div>
          ))}
        </section>
      ) : null}

      {optIns.length ? (
        <section aria-label="Optional batteries" className="grid gap-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Optional, off by default
          </h3>
          {optIns.map((battery) => {
            const badge = statusBadge(battery);
            const busy = pending === battery.id;
            return (
              <div
                key={battery.id}
                className={cn(
                  "grid grid-cols-[1fr_auto] items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                  battery.enabled
                    ? "border-primary/25 bg-primary/5"
                    : "border-border/70 bg-background/55",
                )}
              >
                <div className="min-w-0">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <Package size={14} className="text-muted-foreground" aria-hidden="true" />
                    <strong className="font-medium text-foreground">{battery.name}</strong>
                    <Badge variant="outline" className="font-normal">
                      {battery.category}
                    </Badge>
                    <Badge variant={badge.variant} className="font-normal">
                      {badge.label}
                    </Badge>
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {battery.how_it_helps}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                    <CircleDashed size={12} aria-hidden="true" />
                    <span>{requirementLabel(battery)}</span>
                    {battery.status === "not_installed" ? (
                      <span className="text-muted-foreground/80">· {battery.install_hint}</span>
                    ) : null}
                  </span>
                </div>
                <Switch
                  checked={battery.enabled}
                  disabled={!canMutate || busy || (canRun && !connected)}
                  onCheckedChange={(next) => void toggle(battery, next)}
                  aria-label={`${battery.enabled ? "Disable" : "Enable"} ${battery.name}`}
                />
              </div>
            );
          })}
        </section>
      ) : null}

      {!canMutate ? (
        <p className="text-xs text-muted-foreground">
          This read-only preview cannot change batteries. Use the desktop app, or run{" "}
          <code>alfred batteries</code> from a terminal.
        </p>
      ) : null}
    </div>
  );
}
