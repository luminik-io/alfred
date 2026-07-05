import { Send, Sparkles, WandSparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isLiveSessionUnavailable, themeBuilderConverse } from "../api";
import type { CustomRosterNames } from "../lib/agentThemes";
import type { ConverseMessage, ThemeProposalAction } from "../types";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { ScrollArea } from "./ui/scroll-area";
import { Textarea } from "./ui/textarea";

// "Name your team": a small chat where the person describes a vibe and Alfred
// proposes a full roster of display names. Purpose-built and self-contained (it
// does not reuse the Ask assistant-ui runtime, which is coupled to the plan/draft
// machinery): one turn per send via POST /api/theme-builder/converse. When a turn
// carries a `propose_theme` action, the dialog hands the proposed maps up via
// `onPropose`, which pre-fills the EXISTING CustomThemeEditor for the person to
// tweak and confirm. Nothing is saved here.
//
// Optional + additive: if the converse engine is unavailable (503), the dialog
// shows a plain notice and offers the manual editor via `onManualEdit`, so the
// standalone CustomThemeEditor path always still works.

type ChatBubble = { role: "user" | "assistant"; content: string };

const GREETING: ChatBubble = {
  role: "assistant",
  content:
    "Let's name your team. What vibe do you want? A sci-fi crew, a band, Greek gods, a football squad, something else?",
};

// Turn a `propose_theme` action's args into the CustomRosterNames the editor
// takes. The maps are already role-slug -> label; the editor and the server both
// re-validate on write, so this is a straight projection.
function proposalToCustomNames(action: ThemeProposalAction): CustomRosterNames {
  return {
    names: { ...action.args.custom_names },
    roles: { ...action.args.custom_roles },
  };
}

export function ThemeBuilderDialog({
  open,
  baseUrl,
  onOpenChange,
  onPropose,
  onManualEdit,
}: {
  open: boolean;
  baseUrl: string;
  onOpenChange: (open: boolean) => void;
  // Called when a turn proposes a team. The parent opens the CustomThemeEditor
  // pre-filled with these names/roles so the person tweaks + confirms + saves.
  onPropose: (names: CustomRosterNames) => void;
  // Fallback when the converse engine is unavailable: open the manual editor.
  onManualEdit: () => void;
}) {
  const [bubbles, setBubbles] = useState<ChatBubble[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // True once the engine has reported it is unavailable, so we stop trying and
  // steer the person to the manual editor instead of retrying into a 503.
  const [engineDown, setEngineDown] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Reset the chat each time the dialog (re)opens so a prior session never leaks
  // into the next. Cancel any in-flight turn on close.
  useEffect(() => {
    if (open) {
      setBubbles([GREETING]);
      setInput("");
      setError(null);
      setEngineDown(false);
      setBusy(false);
    } else {
      abortRef.current?.abort();
      abortRef.current = null;
    }
  }, [open]);

  // Keep the newest bubble in view as the conversation grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles, busy]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy || engineDown) return;
    const nextBubbles: ChatBubble[] = [...bubbles, { role: "user", content: text }];
    setBubbles(nextBubbles);
    setInput("");
    setBusy(true);
    setError(null);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const wire: ConverseMessage[] = nextBubbles.map((b) => ({
      role: b.role,
      content: b.content,
    }));

    try {
      const turn = await themeBuilderConverse(baseUrl, { messages: wire }, controller.signal);
      if (controller.signal.aborted) return;
      if (turn.reply) {
        setBubbles((prev) => [...prev, { role: "assistant", content: turn.reply }]);
      }
      if (turn.action) {
        // A team was cast: hand it up to pre-fill the editor. The dialog closes
        // so the editor takes over for the tweak + confirm + save step.
        onPropose(proposalToCustomNames(turn.action));
        onOpenChange(false);
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      if (isLiveSessionUnavailable(err)) {
        // No live engine: stop retrying and steer to the manual editor. The
        // standalone CustomThemeEditor still works, so this is a soft fallback.
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
  }, [baseUrl, bubbles, busy, engineDown, input, onOpenChange, onPropose]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter inserts a newline, like the Ask composer.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="theme-builder max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <WandSparkles aria-hidden="true" className="size-4 text-primary" />
            Name your team
          </DialogTitle>
          <DialogDescription>
            Describe a vibe and Alfred proposes a name for every agent. You review
            and tweak the roster before anything is saved.
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="theme-builder__scroll max-h-[45vh] pr-3">
          <div ref={scrollRef} className="theme-builder__log space-y-3">
            {bubbles.map((bubble, index) => (
              <div
                key={index}
                className={`theme-builder__bubble theme-builder__bubble--${bubble.role}`}
              >
                <span className="theme-builder__who text-xs font-medium text-muted-foreground">
                  {bubble.role === "user" ? "You" : "Alfred"}
                </span>
                <p className="theme-builder__text text-sm text-foreground whitespace-pre-wrap">
                  {bubble.content}
                </p>
              </div>
            ))}
            {busy ? (
              <div className="theme-builder__bubble theme-builder__bubble--assistant">
                <span className="theme-builder__who text-xs font-medium text-muted-foreground">
                  Alfred
                </span>
                <span
                  className="theme-builder__pending inline-flex items-center gap-1.5 text-sm text-muted-foreground"
                  role="status"
                >
                  <Sparkles aria-hidden="true" className="size-3.5 animate-pulse" />
                  Casting your team...
                </span>
              </div>
            ) : null}
          </div>
        </ScrollArea>

        {engineDown ? (
          <p className="theme-builder__notice text-sm text-muted-foreground" role="status">
            The chat needs a live Alfred engine, which is not configured. You can
            still build your roster by hand.
          </p>
        ) : null}
        {error ? (
          <p className="theme-builder__error text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        {engineDown ? (
          <DialogFooter className="theme-builder__footer gap-2 sm:justify-end">
            <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
              Close
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                onOpenChange(false);
                onManualEdit();
              }}
            >
              Edit names by hand
            </Button>
          </DialogFooter>
        ) : (
          <div className="theme-builder__composer flex items-end gap-2">
            <Textarea
              className="theme-builder__input min-h-11"
              value={input}
              placeholder="e.g. make them a Lord of the Rings fellowship"
              rows={2}
              disabled={busy}
              aria-label="Describe a vibe for your team"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <Button
              type="button"
              size="sm"
              className="theme-builder__send"
              disabled={busy || !input.trim()}
              onClick={() => void send()}
              aria-label="Send"
            >
              <Send aria-hidden="true" className="size-4" />
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
