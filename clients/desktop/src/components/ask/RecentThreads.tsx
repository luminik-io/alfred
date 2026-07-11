import { History, MessageSquare, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "../ui/sheet";
import { friendlyTime, plural } from "../../format";
import type { RecentThread } from "./useAskThread";

// The chat-history switcher: a right-side slide-in panel (Sheet) listing the
// last few local Ask conversations, so a person can resume any of them. The
// durable artifacts (issues/specs) remain the real output; this is convenience
// history only.
//
// Why a slide-in panel rather than the old anchored popover: the popover was
// clipped by its container, overlaid the conversation awkwardly, and left no
// room to distinguish threads. A Sheet is fixed to the viewport edge and full
// height, so it can never be clipped, and it gives each row room for a real
// title plus a second line (relative time + message count) and a hover delete.
// Radix Dialog under the hood handles Escape-to-close and returns focus to the
// trigger for free.
export function RecentThreads({
  threads,
  onResume,
  onDelete,
  onRetireFocus,
}: {
  threads: RecentThread[];
  onResume: (id: string) => void;
  onDelete?: (id: string) => void;
  onRetireFocus?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [retiring, setRetiring] = useState(false);

  // The trigger exists to SWITCH chats, so its lifecycle keys on whether any
  // non-active thread remains, not on the raw list length: deleting the ACTIVE
  // chat resets the surface to a fresh unsaved conversation, so a single
  // surviving stored thread is not active and must stay reachable. A pure
  // length guard would hide the trigger and strand that conversation until a
  // new chat is saved.
  const switchable = threads.some((thread) => !thread.active);

  // A delete can shrink the list to only the active chat while the Sheet is
  // open. Keep it mounted for that close transition so Radix can restore focus
  // and release its dialog state before the trigger disappears.
  useEffect(() => {
    if (!switchable && open) {
      setRetiring(true);
      setOpen(false);
    }
  }, [open, switchable]);

  // Nothing to switch to until a non-active thread exists.
  if (!switchable && !open && !retiring) return null;

  const resume = (id: string) => {
    onResume(id);
    // Resuming can leave no switchable thread (the resumed row becomes active,
    // and the outgoing surface may not persist), which unmounts the trigger
    // right after Radix restores focus to it and strands keyboard focus on the
    // body. Route this close through the retire path so focus lands on the
    // composer deterministically, which is also where a person continues after
    // resuming a conversation. Escape/outside-click closes keep the default
    // focus-return to the trigger.
    if (onRetireFocus) setRetiring(true);
    setOpen(false);
  };

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <button type="button" className="ghost-button ask__recent-trigger">
          <History size={14} aria-hidden="true" />
          <span>Recent</span>
        </button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="ask__history-panel"
        onCloseAutoFocus={(event) => {
          if (!retiring) return;
          event.preventDefault();
          onRetireFocus?.();
          setRetiring(false);
        }}
      >
        <SheetHeader className="ask__history-head">
          <SheetTitle>Recent chats</SheetTitle>
          <SheetDescription>
            Resume any of your last {threads.length} conversations.
          </SheetDescription>
        </SheetHeader>
        <ul className="ask__history-list">
          {threads.map((thread) => (
            <li
              key={thread.id}
              className={`ask__history-row${thread.active ? " ask__history-row--active" : ""}`}
            >
              <button
                type="button"
                className="ask__history-item"
                onClick={() => resume(thread.id)}
                aria-current={thread.active ? "true" : undefined}
              >
                <span className="ask__history-item-title">{thread.title}</span>
                <span className="ask__history-item-meta">
                  <MessageSquare size={12} aria-hidden="true" />
                  <span>
                    {thread.active
                      ? "Current chat"
                      : friendlyTime(new Date(thread.updatedAt).toISOString())}
                    {" · "}
                    {plural(thread.messageCount, "message")}
                  </span>
                </span>
              </button>
              {onDelete ? (
                <button
                  type="button"
                  className="ask__history-delete"
                  aria-label={`Delete chat: ${thread.title}`}
                  onClick={() => onDelete(thread.id)}
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      </SheetContent>
    </Sheet>
  );
}
