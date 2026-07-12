import { ArrowRight, Check, Send, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isLiveSessionUnavailable } from "../../api/client";
import { onboardingConverse } from "../../api/setup";
import type { ConverseMessage, OnboardingAction, OnboardingActionTool } from "../../types";
import { Button } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { Textarea } from "../ui/textarea";

/**
 * "Set it up by chatting": a small chat where Alfred walks the person through
 * setup one step at a time. One turn per send via POST /api/onboarding/converse.
 * When a turn carries an `action`, the panel hands it up via `onRunAction`, which
 * runs the SAME setup handler the stepped OnboardingView already drives (single
 * source of truth: the conversational and stepped paths never diverge). The
 * result of that handler is fed back into the chat as an assistant note so the
 * person sees what happened, and the conversation continues.
 *
 * ACTION GATE (#415 request/execute split): the model only ever PROPOSES an
 * action; a SIDE-EFFECTFUL one (sign in to GitHub, write repos, save the team,
 * set a schedule, finish) never runs without an explicit user click on an
 * Approve affordance in that turn. Only READ-ONLY actions (a status re-read like
 * check_engine) auto-proceed, so the good UX where a check flows straight to the
 * model's next prompt is preserved. This stops one user send from silently
 * chaining side-effectful setup steps with no confirmation between them.
 *
 * The panel reuses the ThemeBuilderDialog chat primitives (bubbles, ScrollArea,
 * Textarea composer). It does NOT hand-roll config-writing: every side effect
 * goes through `onRunAction`.
 *
 * Optional + additive: if the converse engine is unavailable (503), the panel
 * shows a plain notice and offers the stepped flow via `onUseStepped`, so setup
 * always still works.
 */

type ChatBubble = { role: "user" | "assistant"; content: string };

// The onboarding actions that only READ state (no config write, no external
// side effect). These may run without an explicit confirmation so a status check
// can flow straight into the model's next prompt. Every other action is
// side-effectful and must wait for a user click. Kept as an explicit allowlist
// so a new tool is side-effectful (gated) by default, never auto-run by
// omission.
const READ_ONLY_ACTIONS: ReadonlySet<OnboardingActionTool> = new Set<OnboardingActionTool>([
  "check_engine",
]);

// A short, human label for the Approve button per action, so the person knows
// what they are confirming before it runs.
const ACTION_APPROVE_LABEL: Record<OnboardingActionTool, string> = {
  check_engine: "Check tools",
  connect_github: "Connect GitHub",
  set_repos: "Save repositories",
  pick_agents: "Use these agents",
  propose_theme: "Preview team",
  save_theme: "Save team names",
  set_batteries: "Turn on batteries",
  skip_batteries: "Skip batteries",
  open_slack_setup: "Open Slack setup",
  skip_slack: "Skip Slack",
  set_schedule: "Set schedule",
  finish_setup: "Finish setup",
};

const GREETING: ChatBubble = {
  role: "assistant",
  content:
    "Hi, I'm Alfred. I can set everything up right here. Ready to start? I'll check your coding tools first.",
};

// The outcome of running an action, fed back into the chat so the model knows
// what happened and the person sees a plain confirmation.
export type OnboardingActionResult = {
  // A short, human note appended to the chat as an assistant message.
  note: string;
  // True when the step succeeded, so the model can move on; false steers a retry.
  ok: boolean;
};

export function OnboardingConversePanel({
  baseUrl,
  batteriesDecisionHandled = false,
  slackConfigured = false,
  onRunAction,
  onDone,
  onUseStepped,
}: {
  baseUrl: string;
  batteriesDecisionHandled?: boolean;
  slackConfigured?: boolean;
  // Execute one requested action through the shared setup handlers and return a
  // plain result note. The panel never writes config itself.
  onRunAction: (action: OnboardingAction) => Promise<OnboardingActionResult>;
  // Called on the terminal finish_setup turn so the parent can route onward.
  onDone: () => void;
  // Fallback: drop to the stepped flow (used when the engine is unavailable, or
  // any time the person prefers clicking through).
  onUseStepped: () => void;
}) {
  const [bubbles, setBubbles] = useState<ChatBubble[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [engineDown, setEngineDown] = useState(false);
  // A side-effectful action the model proposed and is waiting on the user to
  // approve. While set, the composer is replaced by an Approve/Skip affordance;
  // nothing runs until the user clicks. Read-only actions never land here.
  const [pendingAction, setPendingAction] = useState<OnboardingAction | null>(null);
  // Refs make the deterministic decision gate synchronous across recursive
  // model turns. React state could leave the next turn reading the prior value.
  const decisionsRef = useRef({ batteries: batteriesDecisionHandled, slack: slackConfigured });
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // The running transcript sent to the server. This is the MODEL-facing history
  // and is a SUPERSET of the visible bubbles: internal `[setup]` outcome notes
  // are threaded here for the model but never rendered as a chat bubble. Kept in
  // a ref so an action's follow-up note can be threaded in without racing a
  // stale render.
  const transcriptRef = useRef<ChatBubble[]>([GREETING]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles, busy]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Append a VISIBLE chat bubble: it renders AND joins the model transcript. The
  // visible list is tracked separately (not derived from the transcript) so a
  // transcript-only `[setup]` note added via threadModelNote can never leak into
  // the rendered bubbles.
  const appendBubble = useCallback((bubble: ChatBubble) => {
    transcriptRef.current = [...transcriptRef.current, bubble];
    setBubbles((prev) => [...prev, bubble]);
  }, []);

  // Thread a MODEL-ONLY note into the transcript WITHOUT rendering it. Used for
  // the internal `[setup] ...` outcome the model reads as ground truth; the
  // person sees the plain assistant confirmation bubble instead, never the
  // bracketed machine note.
  const threadModelNote = useCallback((content: string) => {
    transcriptRef.current = [...transcriptRef.current, { role: "user", content }];
  }, []);

  // executeAction and runTurn are mutually recursive (an executed action loops
  // back into the next model turn). A ref breaks the callback cycle so neither
  // useCallback depends on the other and the deps stay honest.
  const runTurnRef = useRef<(controller: AbortController) => Promise<void>>(async () => {});

  // Run one proposed action through the shared setup handler, thread its outcome
  // back to the model, honor a terminal finish_setup, then continue the chat.
  // The caller is responsible for the approval gate: this always executes.
  const executeAction = useCallback(
    async (action: OnboardingAction, done: boolean, controller: AbortController): Promise<void> => {
      const decisions = decisionsRef.current;
      if (action.tool === "finish_setup" && (!decisions.batteries || !decisions.slack)) {
        const missing = [
          !decisions.batteries ? "Batteries still need a decision" : null,
          !decisions.slack ? "Slack still needs a decision" : null,
        ].filter(Boolean);
        const note = `${missing.join(". ")}. Choose an option before finishing setup.`;
        appendBubble({ role: "assistant", content: note });
        threadModelNote(`[setup] finish_setup did not complete: ${note}`);
        await runTurnRef.current(controller);
        return;
      }
      // The step runs through the shared handler, never in this panel.
      const result = await onRunAction(action);
      if (controller.signal.aborted) return;
      if (result.ok && (action.tool === "set_batteries" || action.tool === "skip_batteries")) {
        decisionsRef.current.batteries = true;
      }
      if (result.ok && (action.tool === "open_slack_setup" || action.tool === "skip_slack")) {
        decisionsRef.current.slack = true;
      }
      // The person sees a plain confirmation bubble.
      appendBubble({ role: "assistant", content: result.note });
      // The terminal turn carries the finish_setup action AND done. Because the
      // server only sets done on that action, honor done AFTER running it, then
      // route out rather than continuing the chat.
      if (done && result.ok) {
        onDone();
        return;
      }
      // Thread the machine outcome to the model (transcript-only) so its next
      // step reflects what actually happened, then take one more model turn so
      // Alfred acknowledges the result and proposes the next step.
      threadModelNote(
        result.ok
          ? `[setup] ${action.tool} completed: ${result.note}`
          : `[setup] ${action.tool} did not complete: ${result.note}`,
      );
      await runTurnRef.current(controller);
    },
    [appendBubble, onDone, onRunAction, threadModelNote],
  );

  // Send the current transcript for one model turn and process the result. A
  // READ-ONLY action runs immediately (the check flows into the next prompt); a
  // SIDE-EFFECTFUL action is parked as `pendingAction` and NOT executed until the
  // user approves it, so no config write or external step runs unconfirmed.
  const runTurn = useCallback(
    async (controller: AbortController): Promise<void> => {
      const wire: ConverseMessage[] = transcriptRef.current.map((b) => ({
        role: b.role,
        content: b.content,
      }));
      const turn = await onboardingConverse(baseUrl, { messages: wire }, controller.signal);
      if (controller.signal.aborted) return;
      if (turn.reply) {
        appendBubble({ role: "assistant", content: turn.reply });
      }
      if (turn.action) {
        const readOnly = READ_ONLY_ACTIONS.has(turn.action.tool);
        if (readOnly && !turn.done) {
          // A read-only check auto-proceeds so the good UX is preserved.
          await executeAction(turn.action, false, controller);
          return;
        }
        // A side-effectful action (or a done-bearing terminal action) waits for
        // an explicit user click before it runs. Park it; the Approve affordance
        // in the composer area drives executeAction.
        setPendingAction(turn.action);
        return;
      }
      // A plain turn (no action) can still carry done in principle; honor it.
      if (turn.done) {
        onDone();
      }
    },
    [appendBubble, baseUrl, executeAction, onDone],
  );

  // Keep the ref pointing at the latest runTurn so executeAction's recursion
  // always calls the current closure.
  useEffect(() => {
    runTurnRef.current = runTurn;
  }, [runTurn]);

  const drive = useCallback(
    async (work: (controller: AbortController) => Promise<void>) => {
      if (busy || engineDown) return;
      setBusy(true);
      setError(null);

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await work(controller);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (isLiveSessionUnavailable(err)) {
          setEngineDown(true);
          setError(null);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (abortRef.current === controller) {
          setBusy(false);
          abortRef.current = null;
        }
      }
    },
    [busy, engineDown],
  );

  const send = useCallback(() => {
    const text = input.trim();
    if (!text || busy || engineDown || pendingAction) return;
    appendBubble({ role: "user", content: text });
    setInput("");
    void drive((controller) => runTurn(controller));
  }, [appendBubble, busy, drive, engineDown, input, pendingAction, runTurn]);

  // The user approved the parked side-effectful action: run it now.
  const approvePending = useCallback(() => {
    const action = pendingAction;
    if (!action || busy) return;
    setPendingAction(null);
    // finish_setup is the terminal action; done is carried only by it.
    const done = action.tool === "finish_setup";
    void drive((controller) => executeAction(action, done, controller));
  }, [busy, drive, executeAction, pendingAction]);

  // The user declined the parked action: drop it and let them redirect the chat
  // by typing. Nothing is executed. A note tells the model the step was skipped
  // so its next turn does not silently re-propose the same step.
  const skipPending = useCallback(() => {
    const action = pendingAction;
    if (!action || busy) return;
    setPendingAction(null);
    threadModelNote(`[setup] ${action.tool} was skipped by the person.`);
    appendBubble({ role: "assistant", content: "Skipped. What would you like to do instead?" });
  }, [appendBubble, busy, pendingAction, threadModelNote]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  return (
    <div className="onboarding-converse grid gap-3">
      <ScrollArea className="onboarding-converse__scroll max-h-[42vh] pr-3">
        <div ref={scrollRef} className="onboarding-converse__log space-y-3">
          {bubbles.map((bubble, index) => (
            <div
              key={index}
              className={`onboarding-converse__bubble onboarding-converse__bubble--${bubble.role}`}
            >
              <span className="onboarding-converse__who text-xs font-medium text-muted-foreground">
                {bubble.role === "user" ? "You" : "Alfred"}
              </span>
              <p className="onboarding-converse__text text-sm text-foreground whitespace-pre-wrap">
                {bubble.content}
              </p>
            </div>
          ))}
          {busy ? (
            <div className="onboarding-converse__bubble onboarding-converse__bubble--assistant">
              <span className="onboarding-converse__who text-xs font-medium text-muted-foreground">
                Alfred
              </span>
              <span
                className="onboarding-converse__pending inline-flex items-center gap-1.5 text-sm text-muted-foreground"
                role="status"
              >
                <Sparkles aria-hidden="true" className="size-3.5 animate-pulse" />
                Working on it...
              </span>
            </div>
          ) : null}
        </div>
      </ScrollArea>

      {engineDown ? (
        <p className="onboarding-converse__notice text-sm text-muted-foreground" role="status">
          The chat needs a live Alfred engine, which is not configured. You can
          still set up step by step.
        </p>
      ) : null}
      {error ? (
        <p className="onboarding-converse__error text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      {engineDown ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" className="btn-primary-glow" onClick={onUseStepped}>
            <ArrowRight aria-hidden="true" className="size-4" />
            <span>Set up step by step</span>
          </Button>
        </div>
      ) : pendingAction ? (
        // The action gate: a side-effectful step is proposed and waits for the
        // person to approve it before anything runs. Nothing here executes until
        // the click.
        <div className="onboarding-converse__approval grid gap-2" role="group" aria-label="Approve setup step">
          <p className="onboarding-converse__approval-hint text-xs text-muted-foreground">
            Alfred wants to run a setup step. Nothing happens until you approve it.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              className="btn-primary-glow onboarding-converse__approve"
              disabled={busy}
              onClick={approvePending}
            >
              <Check aria-hidden="true" className="size-4" />
              <span>{ACTION_APPROVE_LABEL[pendingAction.tool]}</span>
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="onboarding-converse__skip"
              disabled={busy}
              onClick={skipPending}
            >
              <span>Not now</span>
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="onboarding-converse__composer flex items-end gap-2">
            <Textarea
              className="onboarding-converse__input min-h-11"
              value={input}
              placeholder="e.g. yes, let's start"
              rows={2}
              disabled={busy}
              aria-label="Message Alfred to set up"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <Button
              type="button"
              size="sm"
              className="onboarding-converse__send"
              disabled={busy || !input.trim()}
              onClick={send}
              aria-label="Send"
            >
              <Send aria-hidden="true" className="size-4" />
            </Button>
          </div>
          <button
            type="button"
            className="onboarding-converse__stepped self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
            onClick={onUseStepped}
          >
            Prefer clicking through? Set up step by step
          </button>
        </>
      )}
    </div>
  );
}
