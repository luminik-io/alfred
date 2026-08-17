import {
  CheckCircle2,
  Download,
  MemoryStick,
  Play,
  Radio,
  RefreshCw,
  Server,
  TerminalSquare,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { errorDetail, supportsNativeActions } from "../api/client";
import { loadSetupStatus } from "../api/setup";
import type { ThemeMode, ThemeName } from "../lib/useTheme";
import type { ActionNotice, NativeActionRequest } from "../lib/uiTypes";
import type { SetupStatus, TrustedSlackUsersResponse } from "../types";
import { AppearancePicker } from "./AppearancePicker";
import { EmptyState } from "./atoms";
import { FirstRunReadinessPanel } from "./onboarding/FirstRunReadinessPanel";
import { InstallInventoryPanel } from "./onboarding/InstallInventoryPanel";
import { Tabs, type TabItem } from "./Tabs";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";

type SettingsSection =
  "appearance" | "runtime" | "collaborators" | "diagnostics";

export function SettingsView({
  baseUrl,
  loading,
  connected,
  actionNotice,
  trustedSlack,
  busyTrustedUser,
  nativeBusy,
  themeName,
  mode,
  onSelectTheme,
  onSelectMode,
  onAddTrustedUser,
  onRemoveTrustedUser,
  onRunLocalAction,
  onInstallCore,
  onStartRuntime,
  onConnectServer,
}: {
  baseUrl: string;
  loading: boolean;
  connected: boolean;
  actionNotice: ActionNotice;
  trustedSlack: TrustedSlackUsersResponse | null;
  busyTrustedUser: string | null;
  nativeBusy: string | null;
  themeName: ThemeName;
  mode: ThemeMode;
  onSelectTheme: (theme: ThemeName) => void;
  onSelectMode: (mode: ThemeMode) => void;
  onAddTrustedUser: (userId: string) => void;
  onRemoveTrustedUser: (userId: string) => void;
  onRunLocalAction: (request: NativeActionRequest) => void | Promise<unknown>;
  onInstallCore: () => void;
  onStartRuntime: () => void;
  onConnectServer: (url: string) => void;
}) {
  const canRun = supportsNativeActions();
  const [consoleAgent, setConsoleAgent] = useState("senior-dev");
  const [serverUrl, setServerUrl] = useState(baseUrl);
  const [trustedUserId, setTrustedUserId] = useState("");
  const [pendingTrustedRemoval, setPendingTrustedRemoval] = useState<
    string | null
  >(null);
  const [section, setSection] = useState<SettingsSection>("runtime");
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [setupLoading, setSetupLoading] = useState(false);
  const setupRequestSeq = useRef(0);
  const removeTrustedAffirmRef = useRef<HTMLButtonElement>(null);
  const baseUrlRef = useRef(baseUrl);
  const connectedRef = useRef(connected);
  const connectionGenerationRef = useRef(0);
  const trustedUsers = trustedSlack?.users || [];
  const canAddTrusted = Boolean(trustedUserId.trim()) && !busyTrustedUser;
  const cleanConsoleAgent = consoleAgent.trim();

  useEffect(() => {
    if (baseUrlRef.current !== baseUrl) {
      connectionGenerationRef.current += 1;
      setupRequestSeq.current += 1;
      setSetupStatus(null);
      setSetupError(null);
      setSetupLoading(false);
    }
    baseUrlRef.current = baseUrl;
    setServerUrl(baseUrl);
  }, [baseUrl]);

  useEffect(() => {
    const wasConnected = connectedRef.current;
    if (wasConnected !== connected) {
      connectionGenerationRef.current += 1;
    }
    connectedRef.current = connected;
    if (!connected) {
      setupRequestSeq.current += 1;
      setSetupStatus(null);
      setSetupError(null);
      setSetupLoading(false);
    }
  }, [connected]);

  const refreshSetupStatus = useCallback(() => {
    if (!connected) {
      setupRequestSeq.current += 1;
      setSetupStatus(null);
      setSetupLoading(false);
      return;
    }
    const requestId = setupRequestSeq.current + 1;
    setupRequestSeq.current = requestId;
    const requestBaseUrl = baseUrl;
    const requestGeneration = connectionGenerationRef.current;
    const requestIsCurrent = () =>
      setupRequestSeq.current === requestId &&
      baseUrlRef.current === requestBaseUrl &&
      connectedRef.current &&
      connectionGenerationRef.current === requestGeneration;
    setSetupLoading(true);
    setSetupError(null);
    void loadSetupStatus(baseUrl)
      .then((next) => {
        if (requestIsCurrent()) {
          setSetupStatus(next);
        }
      })
      .catch((err) => {
        if (requestIsCurrent()) {
          setSetupStatus(null);
          setSetupError(errorDetail(err) || "Could not read setup status.");
        }
      })
      .finally(() => {
        if (requestIsCurrent()) {
          setSetupLoading(false);
        }
      });
  }, [baseUrl, connected]);

  useEffect(() => {
    refreshSetupStatus();
    return () => {
      setupRequestSeq.current += 1;
    };
  }, [refreshSetupStatus]);

  const runReadinessRepair = useCallback(
    (request: NativeActionRequest) => {
      const result = onRunLocalAction({ ...request, refreshAfter: true });
      if (result) {
        void Promise.resolve(result).finally(refreshSetupStatus);
        return;
      }
      refreshSetupStatus();
    },
    [onRunLocalAction, refreshSetupStatus],
  );

  const tabs: TabItem<SettingsSection>[] = [
    { key: "runtime", label: "Runtime" },
    { key: "appearance", label: "Appearance" },
    {
      key: "collaborators",
      label: "Collaborators",
      badge: trustedUsers.length || null,
    },
    { key: "diagnostics", label: "Diagnostics" },
  ];

  return (
    <section
      className="panel animate-rise settings-view"
      aria-label="Settings"
      data-ready={
        !loading && !setupLoading && setupStatus !== null ? "true" : "false"
      }
    >
      <header
        className="alfred-page-hero px-4 py-4"
        aria-label="Settings summary"
      >
        <div className="space-y-1">
          <h1 className="font-heading text-2xl font-medium tracking-normal text-foreground">
            Settings
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Configure the runtime, appearance, collaborators, and diagnostics.
          </p>
        </div>
      </header>
      <Tabs
        tabs={tabs}
        active={section}
        onChange={setSection}
        idBase="settings"
        ariaLabel="Settings sections"
      />
      <div id="settings-panel" role="tabpanel" className="subtab-panel">
        {section === "appearance" ? (
          <div className="settings-section">
            <p className="panel-intro">
              Choose the visual theme and light or dark mode for this Mac.
            </p>
            <AppearancePicker
              themeName={themeName}
              mode={mode}
              onSelectTheme={onSelectTheme}
              onSelectMode={onSelectMode}
            />
          </div>
        ) : null}

        {section === "runtime" ? (
          <div className="settings-section">
            <p className="panel-intro">
              Install or repair Alfred on this Mac, then connect to the local
              server it starts. Slack stays the collaboration UI. The CLI
              remains available for headless use and direct inspection.
            </p>
            <div className="settings-runtime-layout">
              <div className="settings-runtime-status">
                <FirstRunReadinessPanel
                  readiness={setupStatus?.first_run}
                  compact
                  canRunActions={canRun}
                  nativeBusy={nativeBusy}
                  onRunRepair={runReadinessRepair}
                />
                <InstallInventoryPanel
                  inventory={setupStatus?.install ?? null}
                  queue={setupStatus?.queue ?? null}
                  compact
                />
                {setupError ? (
                  <p className="console-note">
                    Runtime inventory unavailable: {setupError}
                  </p>
                ) : null}
              </div>
              <section
                className="settings-runtime-connect"
                aria-labelledby="runtime-connection-title"
              >
                <div>
                  <h2 id="runtime-connection-title">Runtime connection</h2>
                  <p>Choose the local server, then run setup checks.</p>
                </div>
                <form
                  className="server-connect-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const nextUrl = serverUrl.trim();
                    if (nextUrl) onConnectServer(nextUrl);
                  }}
                >
                  <label htmlFor="server-url">Local server URL</label>
                  <div className="server-row">
                    <input
                      id="server-url"
                      value={serverUrl}
                      onChange={(event) =>
                        setServerUrl(event.currentTarget.value)
                      }
                      placeholder="http://127.0.0.1:7010"
                      spellCheck={false}
                    />
                    <button
                      className="secondary-button"
                      type="submit"
                      disabled={loading || !serverUrl.trim()}
                    >
                      <span>{loading ? "Checking" : "Use URL"}</span>
                    </button>
                  </div>
                </form>
                <div className="console-panel__actions">
                  <button
                    className="icon-button"
                    type="button"
                    disabled={
                      !canRun ||
                      nativeBusy === "core:install" ||
                      nativeBusy === "runtime:start"
                    }
                    onClick={onInstallCore}
                  >
                    <Download size={16} aria-hidden="true" />
                    <span>
                      {nativeBusy === "core:install"
                        ? "Installing"
                        : nativeBusy === "runtime:start"
                          ? "Starting"
                          : "Install or repair"}
                    </span>
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={
                      !canRun ||
                      nativeBusy === "runtime:start" ||
                      nativeBusy === "core:install"
                    }
                    onClick={onStartRuntime}
                  >
                    <Play size={16} aria-hidden="true" />
                    <span>
                      {nativeBusy === "runtime:start"
                        ? "Starting"
                        : "Start runtime"}
                    </span>
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={!canRun || nativeBusy === "auth_status:fleet"}
                    onClick={() =>
                      onRunLocalAction({
                        action: "auth_status",
                        refreshAfter: true,
                      })
                    }
                  >
                    <CheckCircle2 size={16} aria-hidden="true" />
                    <span>Auth check</span>
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={setupLoading}
                    onClick={refreshSetupStatus}
                  >
                    <RefreshCw
                      size={16}
                      aria-hidden="true"
                      className={setupLoading ? "animate-spin" : undefined}
                    />
                    <span>{setupLoading ? "Checking" : "Recheck setup"}</span>
                  </button>
                </div>
                {!canRun ? (
                  <p className="console-note">
                    Native actions appear in the desktop app. Browser preview
                    stays read-only.
                  </p>
                ) : null}
              </section>
            </div>
          </div>
        ) : null}

        {section === "collaborators" ? (
          <div className="settings-section">
            <p className="panel-intro">
              Add people who can discuss plans and request drafts in Slack. The
              final approval gate stays with the designated operator.
            </p>
            {actionNotice ? (
              <p
                className={`inline-notice inline-notice--${actionNotice.tone}`}
              >
                {actionNotice.message}
              </p>
            ) : null}
            <form
              className="trusted-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (!canAddTrusted) return;
                onAddTrustedUser(trustedUserId.trim());
                setTrustedUserId("");
              }}
            >
              <label htmlFor="trusted-user-id">Slack user ID</label>
              <div className="trusted-form__row">
                <input
                  id="trusted-user-id"
                  value={trustedUserId}
                  onChange={(event) =>
                    setTrustedUserId(event.currentTarget.value)
                  }
                  placeholder="U0123ABCDEF"
                  spellCheck={false}
                />
                <button
                  className="icon-button"
                  type="submit"
                  disabled={!canAddTrusted}
                >
                  <UserPlus size={16} aria-hidden="true" />
                  <span>
                    {busyTrustedUser?.startsWith("add:") ? "Adding" : "Trust"}
                  </span>
                </button>
              </div>
            </form>
            <div
              className="trusted-list"
              aria-label="Trusted Slack collaborators"
            >
              {trustedUsers.length ? (
                trustedUsers.map((user) => (
                  <div className="trusted-user" key={user.user_id}>
                    <Users size={16} aria-hidden="true" />
                    <div>
                      <strong>{user.user_id}</strong>
                      <span>{user.sources.join(", ")}</span>
                    </div>
                    {user.can_remove ? (
                      <button
                        className="ghost-icon"
                        type="button"
                        aria-label={`Remove ${user.user_id}`}
                        disabled={busyTrustedUser === `remove:${user.user_id}`}
                        onClick={() => setPendingTrustedRemoval(user.user_id)}
                      >
                        <X size={15} aria-hidden="true" />
                      </button>
                    ) : null}
                  </div>
                ))
              ) : (
                <EmptyState
                  title="No collaborators yet."
                  body="Add a Slack user ID above so they can discuss plans with Alfred."
                  compact
                />
              )}
            </div>
          </div>
        ) : null}

        {section === "diagnostics" ? (
          <div className="settings-section">
            <p className="panel-intro">
              Raw runtime probes for power users. Output appears in the result
              panel at the top of the app. Per-agent controls live on Agents;
              memory checks live in Learnings.
            </p>
            <div className="console-panel__actions">
              <button
                className="secondary-button"
                type="button"
                disabled={!canRun || nativeBusy === "status:fleet"}
                onClick={() =>
                  onRunLocalAction({ action: "status", refreshAfter: true })
                }
              >
                <TerminalSquare size={16} aria-hidden="true" />
                <span>Agent status</span>
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!canRun || nativeBusy === "agents:fleet"}
                onClick={() =>
                  onRunLocalAction({ action: "agents", refreshAfter: true })
                }
              >
                <Server size={16} aria-hidden="true" />
                <span>Agents</span>
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!canRun || nativeBusy === "brain_doctor:fleet"}
                onClick={() => onRunLocalAction({ action: "brain_doctor" })}
              >
                <MemoryStick size={16} aria-hidden="true" />
                <span>Memory</span>
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!canRun || nativeBusy === "code_memory_status:fleet"}
                onClick={() =>
                  onRunLocalAction({ action: "code_memory_status" })
                }
              >
                <MemoryStick size={16} aria-hidden="true" />
                <span>Code memory</span>
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!canRun || nativeBusy === "redis_status:fleet"}
                onClick={() => onRunLocalAction({ action: "redis_status" })}
              >
                <Radio size={16} aria-hidden="true" />
                <span>Redis</span>
              </button>
            </div>
            <div className="console-agent-row">
              <label htmlFor="dry-run-agent">Dry-run agent</label>
              <input
                id="dry-run-agent"
                value={consoleAgent}
                onChange={(event) => setConsoleAgent(event.currentTarget.value)}
                spellCheck={false}
              />
              <button
                className="icon-button"
                type="button"
                disabled={
                  !canRun ||
                  !cleanConsoleAgent ||
                  nativeBusy === `dry_run:${cleanConsoleAgent}`
                }
                onClick={() => {
                  if (!cleanConsoleAgent) return;
                  onRunLocalAction({
                    action: "dry_run",
                    target: cleanConsoleAgent,
                    refreshAfter: true,
                  });
                }}
              >
                <Play size={16} aria-hidden="true" />
                <span>Run dry-run</span>
              </button>
            </div>
            <details className="cli-fallback">
              <summary>
                <strong>View exact commands</strong>
                <span>See the command behind each local action.</span>
              </summary>
              <p>
                Alfred does not expose an arbitrary shell here. Each button maps
                to a narrow local action, then the result panel shows the
                command, exit status, stdout, and stderr.
              </p>
              <div className="cli-chip-list">
                <code>alfred serve --port 7010</code>
                <code>alfred status --json</code>
                <code>alfred auth status</code>
                <code>alfred agents</code>
                <code>alfred brain doctor --json</code>
                <code>alfred code-memory doctor</code>
                <code>alfred code-memory index</code>
                <code>alfred skills install --starter</code>
                <code>alfred brain redis-status --json</code>
                <code>alfred dry-run &lt;codename&gt;</code>
                <code>alfred pause &lt;codename&gt;</code>
                <code>alfred resume &lt;codename&gt;</code>
                <code>alfred run &lt;codename&gt;</code>
              </div>
            </details>
            {!canRun ? (
              <p className="console-note">
                Native actions appear in the desktop app. Browser preview stays
                read-only.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
      <Dialog
        open={Boolean(pendingTrustedRemoval)}
        onOpenChange={(open) => !open && setPendingTrustedRemoval(null)}
      >
        <DialogContent
          role="alertdialog"
          showCloseButton={false}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            removeTrustedAffirmRef.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {pendingTrustedRemoval
                ? `Remove ${pendingTrustedRemoval}?`
                : "Remove collaborator?"}
            </DialogTitle>
            <DialogDescription>
              This Slack user will no longer be able to create drafts or amend
              planning threads.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setPendingTrustedRemoval(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              ref={removeTrustedAffirmRef}
              onClick={() => {
                if (!pendingTrustedRemoval) return;
                onRemoveTrustedUser(pendingTrustedRemoval);
                setPendingTrustedRemoval(null);
              }}
            >
              Remove collaborator
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
