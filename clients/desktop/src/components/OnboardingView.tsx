import {
  ArrowLeft,
  ArrowRight,
  BatteryCharging,
  GitPullRequest,
  ListChecks,
  MessageCircle,
  Plug,
  Settings2,
  Sparkles,
  TerminalSquare,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { errorDetail, supportsMutations } from "../api/client";
import { loadSetupStatus } from "../api/setup";
import { pollGithubAuthStatus } from "../lib/githubAuth";
import { type CustomRosterNames, type RosterThemeId } from "../lib/agentThemes";
import type { NativeActionRequest, TabKey } from "../lib/uiTypes";
import type { NativeCommandResult, SetupStatus } from "../types";
import { BatteryPickerStep } from "./onboarding/BatteryPickerStep";
import { EngineStep } from "./onboarding/EngineStep";
import { OnboardingConversePanel } from "./onboarding/OnboardingConversePanel";
import { FirstRequestStep } from "./onboarding/FirstRequestStep";
import { GitHubStep } from "./onboarding/GitHubStep";
import { OnboardingRail } from "./onboarding/OnboardingRail";
import { ReposStep } from "./onboarding/ReposStep";
import { RosterThemeStep } from "./onboarding/RosterThemeStep";
import { SlackStep } from "./onboarding/SlackStep";
import { StepFrame } from "./onboarding/StepFrame";
import { Stepper, type StepperItem } from "./onboarding/Stepper";
import {
  ONBOARDING_STEP_ORDER,
  type GithubAuthFlow,
  type OnboardingNotice,
  type OnboardingStepKey,
  type StepProgress,
} from "./onboarding/types";
import { useOnboardingActions } from "./onboarding/useOnboardingActions";
import { WelcomeStep } from "./onboarding/WelcomeStep";
import { Button, Card, CardContent } from "./ui";
import { cn } from "@/lib/utils";

/**
 * The setup takeover (DESIGN_SPEC section 7), built as a clean stepper. It
 * handles both true first-run setup and returning installs that need a quick
 * review. A seven-step journey can be completed without a terminal, ending on a
 * populated Home via a real first request or a clearly-labelled demo:
 *
 *   0 Welcome        mental model + two doors (Get started / I have a server)
 *   1 Tools          detect Claude / Codex (no API keys)
 *   2 GitHub         reuse the gh sign-in (auto-advance when signed in)
 *   3 Repositories   pick by name + description (private badge)
 *   4 Team           pick roster theme / custom names, with a path to custom agents
 *   5 Slack          optional approvals, clearly skippable
 *   6 First request  a real Request, or a labelled sample
 *
 * The journey lives inside a single glass shell that floats over the ambient
 * base. A persistent, minimal numbered Stepper sits at the top (current / done /
 * upcoming), one decision lives in the centered column below it, and a Back /
 * Continue footer (with a first-class per-step Skip for the Dev persona) closes
 * the shell. Steel-violet accents only the single primary CTA per step;
 * everything data-shaped (repo list, engine probe) stays flat.
 *
 * Every step is skippable for the Dev persona, has honest empty/error states,
 * an Enter-key continue flow (suppressed inside text fields), and auto-advance
 * on a detected GitHub sign-in / fully-ready Tools step. The mutating steps
 * (repos, playbook, demo, Slack) need the per-launch token the native bridge
 * attaches; the browser preview cannot, so it degrades to a clear read-only note
 * with copy-paste fallback. The read steps work either way.
 *
 * "Advanced setup" (onOpenConnection) hands off to SetupView for the non-takeover
 * connection + diagnostics surface, which onboarding and Settings share.
 */

type StepMeta = {
  key: OnboardingStepKey;
  index: number;
  title: string;
  railTitle: string;
  blurb: string;
  icon: LucideIcon;
  optional: boolean;
};

const IDLE_GITHUB_AUTH_FLOW: GithubAuthFlow = {
  state: "idle",
  deviceUrl: null,
  deviceCode: null,
  message: null,
  detail: null,
};

const GITHUB_DEVICE_URL = "https://github.com/login/device";

const STEP_META: Record<OnboardingStepKey, Omit<StepMeta, "index">> = {
  welcome: {
    key: "welcome",
    title: "Welcome to Alfred",
    railTitle: "Welcome",
    blurb: "A local fleet that ships pull requests while you stay in control.",
    icon: Sparkles,
    optional: false,
  },
  engine: {
    key: "engine",
    title: "Let's find your coding tools.",
    railTitle: "Tools",
    blurb: "Alfred checks for Claude Code and Codex on this Mac. No keys, no config.",
    icon: TerminalSquare,
    optional: false,
  },
  github: {
    key: "github",
    title: "Connect GitHub.",
    railTitle: "GitHub",
    blurb: "Alfred reuses your existing GitHub sign-in. It only touches the repos you pick next.",
    icon: GitPullRequest,
    optional: false,
  },
  repos: {
    key: "repos",
    title: "Where should Alfred work?",
    railTitle: "Repositories",
    blurb: "Pick the projects Alfred may open pull requests in. You can change this anytime.",
    icon: Plug,
    optional: false,
  },
  batteries: {
    key: "batteries",
    title: "Add batteries?",
    railTitle: "Batteries",
    blurb: "Optional local enhancements: better memory, more token savings, a live code graph. Off by default.",
    icon: BatteryCharging,
    optional: true,
  },
  team: {
    key: "team",
    title: "Name your team.",
    railTitle: "Team",
    blurb: "Same senior-engineering roles, your names. Purely cosmetic.",
    icon: Users,
    optional: false,
  },
  slack: {
    key: "slack",
    title: "Want approvals in Slack?",
    railTitle: "Slack",
    blurb: "Optional. Get questions and approve work from Slack. Skip it and everything happens here.",
    icon: MessageCircle,
    optional: true,
  },
  request: {
    key: "request",
    title: "Give Alfred its first job.",
    railTitle: "First request",
    blurb: "Type a real task, or watch a sample first.",
    icon: ListChecks,
    optional: false,
  },
};

export function OnboardingView({
  baseUrl,
  loading,
  connected,
  canRun,
  nativeBusy,
  nativeResult,
  rosterTheme,
  customNames,
  rosterSaveError,
  onConnectServer,
  onInstallCore,
  onStartRuntime,
  onRunLocalAction,
  onRosterThemeChange,
  onEditCustomTheme,
  onSaveCustomNames,
  onOpenConnection,
  onSwitch,
  onRefreshBoard,
}: {
  baseUrl: string;
  loading: boolean;
  /** True once the client has a live snapshot (the runtime answered). */
  connected: boolean;
  canRun: boolean;
  nativeBusy: string | null;
  nativeResult: NativeCommandResult | null;
  rosterTheme: RosterThemeId;
  customNames: CustomRosterNames;
  rosterSaveError: string | null;
  onConnectServer: (url: string) => void;
  onInstallCore: () => void;
  onStartRuntime: () => void;
  onRunLocalAction: (request: NativeActionRequest) => Promise<NativeCommandResult | null>;
  onRosterThemeChange: (next: RosterThemeId) => void;
  onEditCustomTheme: () => void;
  /**
   * Persist custom roster names/roles. The SAME shared handler the custom theme
   * editor saves through; the conversational onboarding's save_theme action
   * reuses it so both paths write the roster identically.
   */
  onSaveCustomNames: (next: CustomRosterNames) => Promise<void>;
  /** Jump to the full connection + diagnostics surface (the advanced handoff). */
  onOpenConnection: () => void;
  /** Navigate to another primary surface (e.g. Inbox, Ask) after an action. */
  onSwitch?: (tab: TabKey) => void;
  onRefreshBoard?: (options?: { demo?: boolean }) => Promise<void> | void;
}) {
  // The mutating steps (repo save, Slack approver add) are token-gated HTTP
  // writes that work from the Tauri shell AND the browser shell served by
  // `alfred serve` (both carry the per-launch token). Only the token-less Vite
  // dev preview is read-only, so it shows the read-only note. Native-only steps
  // (install / start runtime / GitHub login) stay gated on `canRun`.
  const canMutate = supportsMutations();

  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [notice, setNotice] = useState<OnboardingNotice>(null);
  const [stepKey, setStepKey] = useState<OnboardingStepKey>("welcome");
  // The setup surface: the stepped click-through, or the conversational chat. The
  // chat is an alternative entry from Welcome; the person can drop back to
  // stepped at any point (and the engine-unavailable fallback does so too).
  const [mode, setMode] = useState<"stepped" | "chat">("stepped");
  // True once the first request / demo landed, so the rail shows the journey
  // complete even though the user has already been routed to Home / Ask.
  const [requestDone, setRequestDone] = useState(false);
  // Steps the user explicitly skipped (Dev persona). A skipped step is no longer
  // the blocker for "what's next" but is not marked done either.
  const [skipped, setSkipped] = useState<Set<OnboardingStepKey>>(new Set());
  const [batteriesTouched, setBatteriesTouched] = useState(false);
  // True once the user added a Slack approver, so the optional Slack step reads
  // as done in the rail (the server exposes no approver flag on SetupStatus).
  const [slackTouched, setSlackTouched] = useState(false);
  const [githubAuthFlow, setGithubAuthFlow] = useState<GithubAuthFlow>(IDLE_GITHUB_AUTH_FLOW);
  // The step the auto-advance effect last moved past, so a detected gh/engine
  // only auto-advances once and never fights a manual Back.
  const autoAdvancedFrom = useRef<Set<OnboardingStepKey>>(new Set());
  // Steps the user opened deliberately (rail click or Back). Auto-advance is
  // suppressed for these so revisiting a satisfied step to read it never yanks
  // the user forward; only the natural forward flow auto-advances on detection.
  const manualSteps = useRef<Set<OnboardingStepKey>>(new Set());
  const statusRequestSeq = useRef(0);
  const baseUrlRef = useRef(baseUrl);
  const connectedRef = useRef(connected);
  const connectionGenerationRef = useRef(0);
  const githubAuthRequestSeq = useRef(0);
  const githubAuthFlowRequestSeq = useRef<number | null>(null);

  const setInterruptedGithubAuthFlow = useCallback((message: string, requestId?: number) => {
    setStatusLoading(false);
    const activeFlowRequestId = githubAuthFlowRequestSeq.current;
    const ownsFlow =
      requestId === undefined || activeFlowRequestId === requestId || activeFlowRequestId === null;
    if (ownsFlow) {
      githubAuthFlowRequestSeq.current = null;
    }
    setGithubAuthFlow((current) => {
      const canInterrupt = current.state === "starting" || current.state === "waiting";
      if (!canInterrupt || !ownsFlow) {
        return current;
      }
      return {
        ...IDLE_GITHUB_AUTH_FLOW,
        state: "error",
        message,
      };
    });
  }, []);

  const resetStaleGithubAuthFlow = useCallback(
    (requestId: number, message: string) => {
      setInterruptedGithubAuthFlow(message, requestId);
    },
    [setInterruptedGithubAuthFlow],
  );
  const interruptStaleGithubAuthRequest = useCallback(
    (requestId: number) => {
      const activeFlowRequestId = githubAuthFlowRequestSeq.current;
      if (activeFlowRequestId !== requestId && activeFlowRequestId !== null) {
        return;
      }
      resetStaleGithubAuthFlow(
        requestId,
        "GitHub sign-in was interrupted. Start it again for this runtime.",
      );
    },
    [resetStaleGithubAuthFlow],
  );

  useEffect(() => {
    if (baseUrlRef.current !== baseUrl) {
      connectionGenerationRef.current += 1;
      statusRequestSeq.current += 1;
      githubAuthRequestSeq.current += 1;
      setStatus(null);
      setStatusError(null);
      setStatusLoading(false);
      setInterruptedGithubAuthFlow(
        "GitHub sign-in was interrupted. Start it again for this runtime.",
      );
    }
    baseUrlRef.current = baseUrl;
  }, [baseUrl, setInterruptedGithubAuthFlow]);

  useEffect(() => {
    const wasConnected = connectedRef.current;
    if (wasConnected !== connected) {
      connectionGenerationRef.current += 1;
      githubAuthRequestSeq.current += 1;
    }
    connectedRef.current = connected;
    if (!connected) {
      statusRequestSeq.current += 1;
      setStatus(null);
      setStatusError(null);
      setStatusLoading(false);
      setInterruptedGithubAuthFlow("GitHub sign-in was interrupted. Reconnect, then start it again.");
    } else if (wasConnected !== connected) {
      setInterruptedGithubAuthFlow("GitHub sign-in was interrupted. Start it again for this runtime.");
    }
  }, [connected, setInterruptedGithubAuthFlow]);

  // Re-read setup status and RETURN the fresh snapshot (or null when it could not
  // be read), so a caller that acts on the result reads the fresh value instead
  // of the closed-over `status`/`engineReady` render values, which are only
  // scheduled React state updates that have not landed yet.
  const refreshStatus = useCallback(async (): Promise<SetupStatus | null> => {
    if (!connected) {
      statusRequestSeq.current += 1;
      setStatus(null);
      setStatusLoading(false);
      return null;
    }
    const requestId = ++statusRequestSeq.current;
    const requestBaseUrl = baseUrl;
    const requestGeneration = connectionGenerationRef.current;
    setStatusLoading(true);
    try {
      const next = await loadSetupStatus(baseUrl);
      if (
        statusRequestSeq.current === requestId &&
        baseUrlRef.current === requestBaseUrl &&
        connectedRef.current &&
        connectionGenerationRef.current === requestGeneration
      ) {
        setStatus(next);
        setStatusError(null);
      }
      // Return the fresh snapshot regardless of whether this request is still the
      // current one: the caller wants the value it just fetched, not the render
      // state. A superseded request still read a valid status.
      return next;
    } catch (err) {
      if (
        statusRequestSeq.current === requestId &&
        baseUrlRef.current === requestBaseUrl &&
        connectedRef.current &&
        connectionGenerationRef.current === requestGeneration
      ) {
        setStatusError(errorDetail(err) || "Could not read setup status.");
      }
      return null;
    } finally {
      if (
        statusRequestSeq.current === requestId &&
        baseUrlRef.current === requestBaseUrl &&
        connectedRef.current &&
        connectionGenerationRef.current === requestGeneration
      ) {
        setStatusLoading(false);
      }
    }
  }, [baseUrl, connected]);

  // Returns the FRESH GitHub-connected verdict once the device flow settles, so a
  // caller (the conversational connect_github executor) can report the real
  // outcome instead of the stale pre-action render value. `false` also covers a
  // guard bail, an interrupted/stale request, a timeout, or an error.
  const startGithubAuthLogin = useCallback(async (): Promise<boolean> => {
    if (!canRun || !connected) {
      githubAuthFlowRequestSeq.current = null;
      setGithubAuthFlow({
        ...IDLE_GITHUB_AUTH_FLOW,
        state: "error",
        message: "Open Alfred in the desktop app and install or connect the local runtime first.",
      });
      return false;
    }

    const requestAuthId = ++githubAuthRequestSeq.current;
    githubAuthFlowRequestSeq.current = requestAuthId;
    setStatusLoading(true);
    setGithubAuthFlow({
      ...IDLE_GITHUB_AUTH_FLOW,
      state: "starting",
      message: "Starting GitHub sign-in.",
    });

    const requestBaseUrl = baseUrl;
    const requestGeneration = connectionGenerationRef.current;
    const isCurrentRequest = () =>
      connectedRef.current &&
      baseUrlRef.current === requestBaseUrl &&
      connectionGenerationRef.current === requestGeneration &&
      githubAuthRequestSeq.current === requestAuthId;

    // The fresh GitHub verdict from the poll, returned to the caller. Stays false
    // through any early bail so a stale/interrupted/failed flow never reports a
    // false success.
    let githubConnectedAfter = false;
    try {
      const result = await onRunLocalAction({ action: "github_auth_login" });
      const pollBelongsToCurrentRuntime = isCurrentRequest();
      if (!pollBelongsToCurrentRuntime) {
        interruptStaleGithubAuthRequest(requestAuthId);
        return false;
      }
      if (!result) {
        throw new Error("Could not start GitHub sign-in.");
      }
      if (!result.success) {
        throw new Error(result.message || result.stderr || "GitHub sign-in did not start.");
      }

      const details = result.github_auth;
      const deviceUrl = details?.device_url || GITHUB_DEVICE_URL;
      const deviceCode = details?.device_code || null;
      setGithubAuthFlow({
        state: "waiting",
        deviceUrl,
        deviceCode,
        message: result.message || "Finish GitHub sign-in in your browser.",
        detail: null,
      });

      const poll = await pollGithubAuthStatus(
        async () => {
          const next = await loadSetupStatus(requestBaseUrl);
          if (isCurrentRequest()) {
            setStatus(next);
          }
          return next;
        },
        {
          pollIntervalMs: details?.poll_interval_ms,
          timeoutMs: details?.timeout_ms,
        },
      );

      if (!isCurrentRequest()) {
        interruptStaleGithubAuthRequest(requestAuthId);
        return false;
      }
      githubAuthFlowRequestSeq.current = null;
      if (poll.status) {
        setStatus(poll.status);
      }
      // The verdict returned to the caller is the FRESH status the poll landed on,
      // not the stale pre-action render value: prefer the polled status's github
      // flag, and treat a success poll as connected even if the status snapshot is
      // momentarily absent.
      githubConnectedAfter = Boolean(poll.status?.github.ok) || poll.state === "success";
      if (poll.state === "success") {
        setGithubAuthFlow({
          state: "success",
          deviceUrl,
          deviceCode,
          message: poll.status?.github.detail || "GitHub is connected.",
          detail: null,
        });
      } else {
        setGithubAuthFlow({
          state: "timeout",
          deviceUrl,
          deviceCode,
          message: "Still waiting for GitHub. Finish sign-in, then press Recheck.",
          detail: poll.lastError,
        });
      }
    } catch (err) {
      if (!isCurrentRequest()) {
        interruptStaleGithubAuthRequest(requestAuthId);
        return false;
      }
      githubAuthFlowRequestSeq.current = null;
      setGithubAuthFlow({
        ...IDLE_GITHUB_AUTH_FLOW,
        state: "error",
        message: err instanceof Error ? err.message : String(err),
        detail: errorDetail(err),
      });
    } finally {
      if (isCurrentRequest()) {
        setStatusLoading(false);
      }
    }
    return githubConnectedAfter;
  }, [baseUrl, canRun, connected, interruptStaleGithubAuthRequest, onRunLocalAction]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const githubConnected = Boolean(status?.github.ok);
  const engineReady = Boolean(status?.engine_ready) || Boolean(nativeResult?.success);
  const capabilityActionableCount = status?.capability_plane?.summary.actionable ?? 0;
  const toolsReady = engineReady && capabilityActionableCount === 0;
  const reposSelected = (status?.repos.count ?? 0) > 0;
  const slackConfigured = Boolean(status?.install?.slack_configured);

  // Execute one onboarding action REQUESTED by the conversational guide. The
  // executor lives in useOnboardingActions so both paths share one source of
  // truth; every branch runs the SAME handler the stepped flow already uses.
  const runOnboardingAction = useOnboardingActions({
    baseUrl,
    canMutate,
    canRun,
    connected,
    githubConnected,
    refreshStatus,
    startGithubAuthLogin,
    onRunLocalAction,
    onSaveCustomNames,
    onBatteriesDecision: useCallback(() => setBatteriesTouched(true), []),
    onSlackDecision: useCallback(() => setSlackTouched(true), []),
    onOpenSlackSetup: useCallback(() => {
      setNotice(null);
      setStepKey("slack");
      setMode("stepped");
    }, []),
    onFinishSetup: useCallback(() => setRequestDone(true), []),
  });

  const currentIndex = ONBOARDING_STEP_ORDER.indexOf(stepKey);

  // The furthest step the user has actually reached. The rail's "done" state and
  // the "N of M done" count are anchored to this cursor, never to a background
  // signal that happens to be satisfied for a step the user has not seen yet. So
  // a fresh launch where Claude Code, gh, and repos are all already detected
  // still opens on Welcome with 0 done, instead of a rail that makes first-run
  // feel skipped. The mark only ever moves forward.
  const [reachedIndex, setReachedIndex] = useState(0);
  useEffect(() => {
    setReachedIndex((prev) => Math.max(prev, currentIndex));
  }, [currentIndex]);

  // An existing local runtime was detected on this Mac. When true, the setup
  // inventory already proves several steps are in place, so the rail must not
  // contradict it by reporting them as not-done just because the user has not
  // re-walked the wizard.
  const installInitialized = status !== null && Boolean(status.install?.initialized);

  // Whether a step's own readiness signal is satisfied, ignoring position.
  const stepSatisfied = useCallback(
    (key: OnboardingStepKey): boolean => {
      switch (key) {
        case "welcome":
          // Welcome is satisfied the moment the user steps off it (or finishes).
          return reachedIndex > 0 || requestDone;
        case "engine":
          return toolsReady;
        case "github":
          return githubConnected;
        case "repos":
          return reposSelected;
        case "batteries":
          // Batteries are optional; Alfred works with zero of them. The step
          // reads satisfied once the user moves past it or skips it. We never
          // require a battery to be enabled to continue.
          return (
            reachedIndex > ONBOARDING_STEP_ORDER.indexOf("batteries") ||
            skipped.has("batteries") ||
            installInitialized
          );
        case "team":
          // The shipped Batman roster is already valid. Keeping the default is a
          // complete state only after the operator continues past Team, OR when
          // an existing install proves a roster is already configured.
          return (
            reachedIndex > ONBOARDING_STEP_ORDER.indexOf("team") || installInitialized
          );
        case "slack":
          // Server configuration and local decisions are both authoritative.
          // The latter cover a skip or approver addition before a refreshed
          // setup snapshot is available.
          return slackConfigured || skipped.has("slack") || slackTouched;
        case "request":
          return requestDone;
        default:
          return false;
      }
    },
    [
      githubConnected,
      installInitialized,
      reachedIndex,
      reposSelected,
      requestDone,
      slackConfigured,
      skipped,
      slackTouched,
      toolsReady,
    ],
  );

  // Per-step completion for the rail. A step is "done" when its readiness signal
  // is satisfied AND either the user has reached it (its index is at or below the
  // furthest-reached cursor) OR an existing install was detected. On a fresh
  // first run the cursor keeps the count honest so a pre-detected engine / gh /
  // repo the user has not walked up to does not read as done. But when the
  // runtime already exists, a proven-complete step must show done so the rail
  // never contradicts the "ready to use" inventory (the "0 of 7" vs "ready"
  // contradiction). Steps with no inventory-backed signal (welcome) still rely
  // on the cursor, so they are never invented as done.
  const stepComplete = useCallback(
    (key: OnboardingStepKey): boolean => {
      const index = ONBOARDING_STEP_ORDER.indexOf(key);
      if (!installInitialized && index > reachedIndex) return false;
      return stepSatisfied(key);
    },
    [installInitialized, reachedIndex, stepSatisfied],
  );

  const steps = useMemo<StepMeta[]>(
    () =>
      ONBOARDING_STEP_ORDER.map((key, index) => ({
        ...STEP_META[key],
        index,
      })),
    [],
  );

  const progressFor = useCallback(
    (key: OnboardingStepKey): StepProgress => {
      if (stepComplete(key)) return "done";
      if (key === stepKey) return "active";
      return "todo";
    },
    [stepComplete, stepKey],
  );

  const stepperItems = useMemo<StepperItem[]>(
    () =>
      steps.map((step) => ({
        key: step.key,
        label: step.railTitle,
        state: progressFor(step.key),
        optional: step.optional,
      })),
    [steps, progressFor],
  );

  const previousKey = ONBOARDING_STEP_ORDER[currentIndex - 1] ?? null;
  const nextKey = ONBOARDING_STEP_ORDER[currentIndex + 1] ?? null;

  const goToStep = useCallback((key: OnboardingStepKey, options?: { manual?: boolean }) => {
    if (options?.manual) {
      manualSteps.current.add(key);
    }
    setNotice(null);
    setStepKey(key);
  }, []);

  const advance = useCallback(() => {
    if (stepKey === "batteries") setBatteriesTouched(true);
    if (nextKey) goToStep(nextKey);
  }, [goToStep, nextKey, stepKey]);

  const skipStep = useCallback(
    (key: OnboardingStepKey) => {
      if (key === "batteries") setBatteriesTouched(true);
      setSkipped((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      const idx = ONBOARDING_STEP_ORDER.indexOf(key);
      const following = ONBOARDING_STEP_ORDER[idx + 1] ?? null;
      if (following) goToStep(following);
    },
    [goToStep],
  );

  // Auto-advance once when a step's detection lands while the user is sitting on
  // it (DESIGN_SPEC: auto-advance on detected gh / engine). Never fights a Back.
  useEffect(() => {
    if (manualSteps.current.has(stepKey)) return;
    if (stepKey === "engine" && toolsReady && !autoAdvancedFrom.current.has("engine")) {
      autoAdvancedFrom.current.add("engine");
      goToStep("github");
    } else if (stepKey === "github" && githubConnected && !autoAdvancedFrom.current.has("github")) {
      autoAdvancedFrom.current.add("github");
      goToStep("repos");
    }
  }, [stepKey, toolsReady, githubConnected, goToStep]);

  // Enter advances when the focus is not in a text field (so typing a server URL
  // or Slack id never triggers a jump). The step bodies own their own submits.
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (event.key !== "Enter" || event.defaultPrevented) return;
      const target = event.target as HTMLElement;
      const tag = target.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "BUTTON" ||
        tag === "A" ||
        tag === "SUMMARY" ||
        target.isContentEditable
      ) {
        return;
      }
      if (nextKey) {
        event.preventDefault();
        advance();
      }
    },
    [advance, nextKey],
  );

  const meta = STEP_META[stepKey];
  const canReadSetupStatus = connected || loading || statusLoading;
  let shellCopy = {
    eyebrow: "First run",
    title: "Set up Alfred",
    lede: "A few short steps, about two minutes. No terminal, no API keys.",
  };
  if (status === null && !statusError && canReadSetupStatus) {
    shellCopy = {
      eyebrow: "Checking setup",
      title: "Checking this Mac",
      lede: "Reading the local runtime to pick the right setup path for you.",
    };
  } else if (installInitialized) {
    shellCopy = {
      eyebrow: "Existing setup",
      title: "Review your setup",
      lede: "Alfred is already installed on this Mac. Recheck tools, repos, team names, and Slack before you ship more work.",
    };
  }

  const completedCount = stepperItems.filter((s) => s.state === "done").length;

  return (
    <section className="alfred-onboarding" aria-label="Set up Alfred" onKeyDown={onKeyDown}>
      {/* Left rail: brand + the value promise + trust + a spend reassurance. It
          fills the left of the frame so the takeover reads as one composed
          product intro, not a card floating in a void. Collapses above the main
          column at narrow widths. */}
      <OnboardingRail
        completedCount={completedCount}
        totalSteps={ONBOARDING_STEP_ORDER.length}
      />

      <div className="alfred-onboarding-shell alfred-glass">
        <header className="alfred-onboarding-shell__head">
          <div className="min-w-0">
            <p className="alfred-onboarding-shell__eyebrow">{shellCopy.eyebrow}</p>
            <h1 className="alfred-onboarding-shell__title">{shellCopy.title}</h1>
            <p className="alfred-onboarding-shell__lede">{shellCopy.lede}</p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            type="button"
            onClick={onOpenConnection}
            className="alfred-onboarding-shell__advanced"
          >
            <Settings2 size={15} aria-hidden="true" />
            <span>Advanced setup</span>
          </Button>
        </header>

        {mode === "chat" ? (
          // The conversational entry: Alfred drives setup one step at a time via
          // /api/onboarding/converse, executing each requested step through the
          // SAME handlers the stepped flow uses (runOnboardingAction). The person
          // can drop back to the stepped flow at any point.
          <div className="alfred-onboarding-shell__panel motion-fade">
            <OnboardingConversePanel
              baseUrl={baseUrl}
              batteriesDecisionHandled={batteriesTouched}
              slackDecisionHandled={stepSatisfied("slack")}
              onRunAction={runOnboardingAction}
              onDone={() => {
                setRequestDone(true);
                onSwitch?.("home");
              }}
              onUseStepped={() => setMode("stepped")}
            />
          </div>
        ) : (
          <>
        <Stepper
          steps={stepperItems}
          activeKey={stepKey}
          onSelect={(key) => goToStep(key, { manual: true })}
        />

        {statusError ? (
          <Card className="rounded-lg border-destructive/30 bg-destructive/10 text-destructive shadow-none">
            <CardContent className="px-4 text-sm">
              {statusError} The steps below still show their manual fallback.
            </CardContent>
          </Card>
        ) : null}
        {notice ? (
          <Card
            className={cn(
              "rounded-lg shadow-none",
              notice.tone === "ok"
                ? "border-primary/25 bg-primary/10 text-primary"
                : "border-destructive/25 bg-destructive/10 text-destructive",
            )}
          >
            <CardContent className="px-4 text-sm">{notice.message}</CardContent>
          </Card>
        ) : null}

        <div className="alfred-onboarding-shell__panel motion-fade" key={stepKey}>
          {stepKey === "welcome" ? (
            // Welcome is the hero screen, not a labelled step: it skips the
            // StepFrame icon/title/blurb so the value line is said once here, not
            // echoed by a step header above it.
            <WelcomeStep
              install={status?.install ?? null}
              queue={status?.queue ?? null}
              connected={connected}
              canRun={canRun}
              nativeBusy={nativeBusy}
              onInstallCore={onInstallCore}
              onGetStarted={() => goToStep("engine")}
              onChatSetup={() => setMode("chat")}
              onDevShortcut={() => goToStep("github")}
            />
          ) : null}

          {stepKey === "engine" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb}>
              <EngineStep
                status={status}
                engineReady={engineReady}
                canRun={canRun}
                nativeBusy={nativeBusy}
                statusLoading={statusLoading}
                onRunLocalAction={onRunLocalAction}
                onRecheck={() => void refreshStatus()}
              />
            </StepFrame>
          ) : null}

          {stepKey === "github" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb}>
              <GitHubStep
                baseUrl={baseUrl}
                loading={loading}
                connected={connected}
                github={status?.github ?? null}
                canRun={canRun}
                nativeBusy={nativeBusy}
                authFlow={githubAuthFlow}
                statusLoading={statusLoading}
                onConnectServer={onConnectServer}
                onStartRuntime={onStartRuntime}
                onStartGithubAuth={startGithubAuthLogin}
                onRecheck={() => void refreshStatus()}
              />
            </StepFrame>
          ) : null}

          {stepKey === "repos" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb}>
              <ReposStep
                baseUrl={baseUrl}
                canMutate={canMutate}
                githubConnected={githubConnected}
                selectedCount={status?.repos.count ?? 0}
                onSaved={async () => {
                  await refreshStatus();
                }}
                setNotice={setNotice}
              />
            </StepFrame>
          ) : null}

          {stepKey === "batteries" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb} accentLabel="Optional">
              <BatteryPickerStep
                baseUrl={baseUrl}
                canMutate={canMutate}
                onSaved={async () => {
                  setBatteriesTouched(true);
                  await refreshStatus();
                }}
                setNotice={setNotice}
              />
            </StepFrame>
          ) : null}

          {stepKey === "team" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb}>
              <RosterThemeStep
                customNames={customNames}
                rosterTheme={rosterTheme}
                saveError={rosterSaveError}
                onChange={onRosterThemeChange}
                onEditCustom={onEditCustomTheme}
                onOpenCustomAgents={onSwitch ? () => onSwitch("fleet") : undefined}
              />
            </StepFrame>
          ) : null}

          {stepKey === "slack" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb} accentLabel="Optional">
              <SlackStep
                baseUrl={baseUrl}
                connected={connected}
                canMutate={canMutate}
                onSkip={() => skipStep("slack")}
                onApproverAdded={() => setSlackTouched(true)}
                setNotice={setNotice}
              />
            </StepFrame>
          ) : null}

          {stepKey === "request" ? (
            <StepFrame icon={meta.icon} title={meta.title} blurb={meta.blurb} accentLabel="The payoff">
              <FirstRequestStep
                baseUrl={baseUrl}
                canMutate={canMutate}
                reposReady={reposSelected}
                demoPresent={Boolean(status?.demo.present)}
                setNotice={setNotice}
                onSwitch={onSwitch}
                onComplete={() => setRequestDone(true)}
                onSeedDemo={async () => {
                  await onRefreshBoard?.({ demo: true });
                  await refreshStatus();
                }}
                onClearDemo={async () => {
                  await onRefreshBoard?.({ demo: false });
                  await refreshStatus();
                }}
              />
            </StepFrame>
          ) : null}
        </div>

        <footer className="alfred-onboarding-shell__footer" aria-label="Onboarding navigation">
          <Button
            variant="outline"
            size="sm"
            type="button"
            disabled={!previousKey}
            onClick={() => {
              if (previousKey) goToStep(previousKey, { manual: true });
            }}
          >
            <ArrowLeft size={15} aria-hidden="true" />
            <span>Back</span>
          </Button>
          <span className="alfred-onboarding-shell__progress">
            Step {currentIndex + 1} of {ONBOARDING_STEP_ORDER.length}
          </span>
          <div className="flex items-center gap-2">
            {meta.optional && nextKey ? (
              <Button variant="ghost" size="sm" type="button" onClick={() => skipStep(stepKey)}>
                <span>Skip</span>
              </Button>
            ) : null}
            {nextKey ? (
              <Button type="button" size="sm" className="btn-primary-glow" onClick={advance}>
                <span>Continue</span>
                <ArrowRight size={15} aria-hidden="true" />
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                className="btn-primary-glow"
                onClick={() => onSwitch?.("home")}
              >
                <span>Go to Inbox</span>
                <ArrowRight size={15} aria-hidden="true" />
              </Button>
            )}
          </div>
        </footer>
          </>
        )}
      </div>
    </section>
  );
}
