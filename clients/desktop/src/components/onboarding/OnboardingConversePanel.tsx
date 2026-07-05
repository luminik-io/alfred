import { ArrowRight, Send, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isLiveSessionUnavailable, onboardingConverse } from "../../api";
import type { ConverseMessage, OnboardingAction } from "../../types";
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
 * The panel reuses the ThemeBuilderDialog chat primitives (bubbles, ScrollArea,
 * Textarea composer). It does NOT hand-roll config-writing: every side effect
 * goes through `onRunAction`.
 *
 * Optional + additive: if the converse engine is unavailable (503), the panel
 * shows a plain notice and offers the stepped flow via `onUseStepped`, so setup
 * always still works.
 */

type ChatBubble = { role: "user" | "assistant"; content: string };

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
  onRunAction,
  onDone,
  onUseStepped,
}: {
  baseUrl: string;
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // The running transcript sent to the server. Kept in a ref so an action's
  // follow-up note can be threaded in without racing a stale render.
  const transcriptRef = useRef<ChatBubble[]>([GREETING]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles, busy]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const appendBubble = useCallback((bubble: ChatBubble) => {
    transcriptRef.current = [...transcriptRef.current, bubble];
    setBubbles(transcriptRef.current);
  }, []);

  // Send the current transcript for one model turn, run any requested action
  // through the shared handlers, and thread the outcome back into the chat. A
  // turn that requests an action loops once more so the model can acknowledge
  // the result and move to the next step.
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
        // A step was requested: run it through the shared setup handler, never
        // in this panel. Feed the outcome back as a user-role note so the model
        // treats it as ground truth for the next turn (the app said so, not the
        // person).
        const result = await onRunAction(turn.action);
        if (controller.signal.aborted) return;
        appendBubble({ role: "assistant", content: result.note });
        // The terminal turn carries the finish_setup action AND done. Because the
        // server only sets done on that action, the done check has to run even
        // when an action is present: run the action, then honor done and route
        // out, rather than recursing past it (which would never re-surface done).
        if (turn.done) {
          onDone();
          return;
        }
        // Thread the machine outcome to the model as a user turn so its next
        // step reflects what actually happened, then loop once so Alfred
        // acknowledges the result and moves to the next step.
        transcriptRef.current = [
          ...transcriptRef.current,
          {
            role: "user",
            content: result.ok
              ? `[setup] ${turn.action.tool} completed: ${result.note}`
              : `[setup] ${turn.action.tool} did not complete: ${result.note}`,
          },
        ];
        await runTurn(controller);
        return;
      }
      // A plain turn (no action) can still carry done in principle; honor it.
      if (turn.done) {
        onDone();
      }
    },
    [appendBubble, baseUrl, onDone, onRunAction],
  );

  const drive = useCallback(
    async (userText: string | null) => {
      if (busy || engineDown) return;
      if (userText) {
        appendBubble({ role: "user", content: userText });
      }
      setInput("");
      setBusy(true);
      setError(null);

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await runTurn(controller);
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
    [appendBubble, busy, engineDown, runTurn],
  );

  const send = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    void drive(text);
  }, [drive, input]);

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
