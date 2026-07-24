import { CheckCircle2, MemoryStick, RefreshCw, Wrench, XCircle } from "lucide-react";

import type { NativeActionRequest } from "../../lib/uiTypes";
import type { SetupStatus } from "../../types";
import { Badge, Button, Card, CardContent } from "../ui";
import { cn } from "@/lib/utils";

const engineStateLabel: Record<string, string> = {
  ready: "ready",
  auth_required: "sign in",
  probe_failed: "check failed",
  needs_validation: "needs validation",
  incompatible: "incompatible",
  missing: "not installed",
};

/** Show the supported golden path first and keep raw harness probes one disclosure away. */
export function EngineStep({
  status,
  engineReady,
  canRun,
  nativeBusy,
  statusLoading,
  onRunLocalAction,
  onRecheck,
}: {
  status: SetupStatus | null;
  engineReady: boolean;
  canRun: boolean;
  nativeBusy: string | null;
  statusLoading: boolean;
  onRunLocalAction: (request: NativeActionRequest) => void;
  onRecheck: () => void;
}) {
  const engines = status?.engines ?? [];
  const readyEngine = engines.find((engine) => engine.ready);
  const detectedEngine = engines.find((engine) => engine.installed);
  const codeMemory = status?.code_memory;
  const capabilityPlane = status?.capability_plane;
  const capabilityBadgeLabel = capabilityPlane
    ? capabilityPlane.summary.actionable > 0
      ? "needs attention"
      : capabilityPlane.summary.ready === capabilityPlane.summary.total
        ? "ready"
        : capabilityPlane.summary.disabled === capabilityPlane.summary.total
          ? "optional"
          : "partly ready"
    : null;
  const capabilityBadgeVariant =
    capabilityBadgeLabel === "ready" ? ("secondary" as const) : ("outline" as const);
  const codeMemoryReady = Boolean(
    codeMemory?.enabled && codeMemory.binary.resolved && codeMemory.index_present,
  );
  const codeMemoryRepos = codeMemory?.repos;
  const scopedCodeRepos = codeMemoryRepos?.selected ?? codeMemoryRepos?.configured ?? [];
  const codeRepoScopeLabel =
    codeMemoryRepos?.source === "configured" ||
    (!codeMemoryRepos?.source && (codeMemoryRepos?.configured?.length ?? 0) > 0)
      ? "Configured repos"
      : "Auto-discovered repos";
  const codeMemoryTone = !codeMemory?.enabled
    ? "off"
    : codeMemoryReady
      ? "ready"
      : codeMemory?.binary.resolved
        ? "index pending"
        : codeMemory?.autofetch
          ? "will fetch"
          : "not found";

  return (
    <div className="grid gap-4">
      {engineReady ? (
        <Card
          size="sm"
          className="rounded-lg border-primary/25 bg-primary/10 text-primary shadow-none"
        >
          <CardContent className="flex items-center gap-2 px-3 text-sm">
            <CheckCircle2 size={15} aria-hidden="true" />
            <span>
              {readyEngine?.display_name ?? "A coding engine"} is ready. Alfred can run work on this
              Mac.
            </span>
          </CardContent>
        </Card>
      ) : (
        <Card size="sm" className="rounded-lg border-border/70 bg-muted/35 shadow-none">
          <CardContent className="grid gap-2 px-3 text-sm text-muted-foreground">
            <span>
              <strong className="block text-foreground">
                {detectedEngine ? "No compatible engine is ready." : "No coding engine is installed."}
              </strong>
              {detectedEngine
                ? "Sign in or update the detected CLI, then check again."
                : "Install and sign in to Claude Code or Codex, then check again."}
            </span>
            <a
              className="inline-flex w-fit min-h-9 items-center gap-1 rounded-md border border-border/70 bg-background/55 px-2.5 py-1.5 text-sm font-medium text-foreground underline-offset-2 hover:bg-muted/45 hover:underline"
              href="https://docs.anthropic.com/en/docs/claude-code/overview"
              target="_blank"
              rel="noreferrer"
            >
              Install Claude Code
            </a>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          disabled={!canRun || nativeBusy === "auth_status:fleet"}
          onClick={() => onRunLocalAction({ action: "auth_status", refreshAfter: true })}
        >
          <CheckCircle2 size={15} aria-hidden="true" />
          <span>{nativeBusy === "auth_status:fleet" ? "Checking" : "Check my tools"}</span>
        </Button>
        <Button variant="outline" type="button" onClick={onRecheck} disabled={statusLoading}>
          <RefreshCw
            size={14}
            aria-hidden="true"
            className={statusLoading ? "animate-spin" : undefined}
          />
          <span>Recheck</span>
        </Button>
      </div>

      <p className="text-sm text-muted-foreground">No API keys needed.</p>

      {capabilityPlane ? (
        <Card size="sm" className="rounded-lg border-border/70 bg-background/55 shadow-none">
          <CardContent className="grid gap-3 px-3 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <strong className="block text-foreground">Local capabilities</strong>
                <span className="text-muted-foreground">
                  {capabilityPlane.summary.ready} of {capabilityPlane.summary.total} ready
                  {capabilityPlane.summary.actionable
                    ? `, ${capabilityPlane.summary.actionable} to finish`
                    : ""}
                  .
                </span>
              </div>
              <Badge variant={capabilityBadgeVariant}>{capabilityBadgeLabel}</Badge>
            </div>
            <ul className="grid gap-2" aria-label="Local Alfred capabilities">
              {capabilityPlane.capabilities.map((capability) => {
                const ready = capability.state === "ready";
                const disabled = capability.state === "disabled";
                const showHint = (!ready && !disabled) || (disabled && Boolean(capability.install_hint));
                return (
                  <li
                    key={capability.key}
                    className="grid gap-1 rounded-md border border-border/60 bg-card/50 px-2.5 py-2"
                  >
                    <span className="flex min-w-0 items-start gap-2">
                      {ready ? (
                        <CheckCircle2
                          size={15}
                          aria-hidden="true"
                          className="mt-0.5 shrink-0 text-primary"
                        />
                      ) : (
                        <Wrench
                          size={15}
                          aria-hidden="true"
                          className="mt-0.5 shrink-0 text-muted-foreground"
                        />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <strong className="font-medium text-foreground">
                            {capability.title}
                          </strong>
                          <Badge variant={ready ? "secondary" : "outline"}>
                            {capability.state.replace(/_/g, " ")}
                          </Badge>
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {capability.detail}
                        </span>
                        {showHint ? (
                          <code className="mt-1 block break-words text-[11px] text-muted-foreground">
                            {capability.install_hint}
                          </code>
                        ) : null}
                      </span>
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      Source:{" "}
                      {capability.source.url ? (
                        <a
                          className="underline-offset-2 hover:underline"
                          href={capability.source.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {capability.source.source}
                        </a>
                      ) : (
                        capability.source.source
                      )}
                      {capability.source.license ? ` (${capability.source.license})` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {codeMemory ? (
        <Card size="sm" className="rounded-lg border-border/70 bg-background/55 shadow-none">
          <CardContent className="grid gap-2 px-3 text-sm">
            <div className="flex items-start gap-2">
              {codeMemoryReady ? (
                <CheckCircle2 size={15} aria-hidden="true" className="mt-0.5 text-primary" />
              ) : (
                <MemoryStick size={15} aria-hidden="true" className="mt-0.5 text-muted-foreground" />
              )}
              <div className="min-w-0 flex-1">
                <strong className="block text-foreground">Code memory</strong>
                <span className="text-muted-foreground">{codeMemory.detail}</span>
              </div>
              <Badge variant={codeMemoryReady ? "secondary" : "outline"}>{codeMemoryTone}</Badge>
            </div>
            <details className="group">
              <summary className="cursor-pointer list-none text-xs font-medium text-muted-foreground">
                Advanced: code-memory probe
              </summary>
              <dl className="mt-3 grid gap-1 text-xs text-muted-foreground">
                <div className="grid gap-0.5">
                  <dt className="font-medium text-foreground">Binary</dt>
                  <dd>{codeMemory.binary.path || "not resolved"}</dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="font-medium text-foreground">Pinned release</dt>
                  <dd>
                    {codeMemory.repo}@{codeMemory.version_pin}
                  </dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="font-medium text-foreground">Index</dt>
                  <dd>{codeMemory.index_dir}</dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="font-medium text-foreground">{codeRepoScopeLabel}</dt>
                  <dd>
                    {scopedCodeRepos.length ? scopedCodeRepos.join(", ") : "none found yet"}
                  </dd>
                </div>
              </dl>
            </details>
          </CardContent>
        </Card>
      ) : null}

      {engines.length ? (
        <Card size="sm" className="rounded-lg border-border/70 bg-background/55 shadow-none">
          <CardContent className="px-3">
            <details className="group grid gap-2">
              <summary className="cursor-pointer list-none">
                <span className="grid gap-0.5">
                  <strong className="text-sm font-medium">Advanced: engine probe</strong>
                  <span className="text-xs text-muted-foreground">
                    What Alfred detected for each CLI.
                  </span>
                </span>
              </summary>
              <ul className="mt-3 grid gap-2" aria-label="Coding engine probes">
                {engines.map((engine) => {
                  const state = engine.state?.trim() || "unknown";
                  return (
                    <li
                      key={engine.name}
                      className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 rounded-md border border-border/60 bg-card/60 px-2.5 py-2 text-sm"
                    >
                      {engine.ready ? (
                        <CheckCircle2 size={15} aria-hidden="true" className="mt-0.5 text-primary" />
                      ) : engine.installed ? (
                        <Wrench size={15} aria-hidden="true" className="mt-0.5 text-muted-foreground" />
                      ) : (
                        <XCircle size={15} aria-hidden="true" className="mt-0.5 text-muted-foreground" />
                      )}
                      <span className="min-w-0">
                        <strong className="block font-medium text-foreground">
                          {engine.display_name || engine.name}
                        </strong>
                        <span className="block text-xs text-muted-foreground">
                          {engine.detail || "This engine returned an incomplete readiness result."}
                        </span>
                        {engine.version ? (
                          <code className="mt-1 block truncate font-mono text-[11px] text-muted-foreground">
                            {engine.version}
                          </code>
                        ) : null}
                      </span>
                      <Badge
                        variant={engine.ready ? "secondary" : "outline"}
                        className={cn("ml-auto")}
                      >
                        {engineStateLabel[state] ?? state.replace(/_/g, " ")}
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            </details>
          </CardContent>
        </Card>
      ) : null}

      {!canRun ? (
        <p className="rounded-lg border border-border/70 bg-muted/35 px-3 py-2 text-sm text-muted-foreground">
          The desktop app runs the deeper CLI check. In the browser preview, this step reads the
          server's engine probe only.
        </p>
      ) : null}
    </div>
  );
}
