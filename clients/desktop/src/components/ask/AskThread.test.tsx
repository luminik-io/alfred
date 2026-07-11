import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ComposeView } from "../ComposeView";
import {
  ApiError,
  composeConverse,
  composeDraft,
  conversationControl,
  filePlanIssue,
  streamComposeConverse,
} from "../../api";
import type { ConverseResponse } from "../../types";

// These tests exercise the assistant-ui ExternalStore adapter wiring that the
// migration introduced: onNew streaming (tokens appended in place), message
// conversion (user vs assistant bubbles, the draft tool-call part), and the
// last-5 recent-threads switcher. The broader
// converse/draft/control/cancel behavior is covered in ComposeChat.test.tsx.

// `supportsConversation` gates whether Ask holds a live streamed conversation.
// It is overridable per test so we can prove the hosted-browser case (native
// actions unavailable, conversation still available) converses through the
// server-side engine rather than dropping to the offline draft fallback.
let conversationAvailable = true;

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    // Native actions stay Tauri-only; the hosted-browser test flips this false.
    supportsNativeActions: () => false,
    supportsConversation: () => conversationAvailable,
    composeConverse: vi.fn(),
    composeDraft: vi.fn(),
    conversationControl: vi.fn(),
    filePlanIssue: vi.fn(),
    streamComposeConverse: vi.fn(),
  };
});

const converseMock = vi.mocked(composeConverse);
const draftMock = vi.mocked(composeDraft);
const controlMock = vi.mocked(conversationControl);
const filePlanIssueMock = vi.mocked(filePlanIssue);
const streamMock = vi.mocked(streamComposeConverse);

function renderChat(selectedRepos = ["your-org/frontend"]) {
  return render(
    <ComposeView baseUrl="http://127.0.0.1:7010" selectedRepos={selectedRepos} onSwitch={vi.fn()} />,
  );
}

function chatInput() {
  return screen.getByLabelText(/your message to alfred/i);
}

async function send(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(chatInput(), text);
  await user.click(screen.getByRole("button", { name: /send message/i }));
}

function converseResponse(overrides: Partial<ConverseResponse> = {}): ConverseResponse {
  return {
    draft_id: "compose-20260603-120000-add-csv-export",
    saved_path: "/state/planning-drafts/compose-20260603-120000-add-csv-export.json",
    reply: "How should Alfred verify this worked?",
    readiness: { score: 62, ready: false, missing: ["a test plan"] },
    done: false,
    draft: {
      title: "Add CSV export to the attendees table",
      problem: "Sales reps need to export attendees.",
      user: "Sales rep",
      current_behavior: "",
      desired_behavior: "A download button exports the visible rows as CSV.",
      repos: ["your-org/frontend"],
      acceptance_criteria: [],
      test_plan: "",
      out_of_scope: "",
      rollout: "",
      open_questions: "",
    },
    ...overrides,
  };
}

beforeEach(() => {
  conversationAvailable = true;
  window.localStorage.clear();
  converseMock.mockReset();
  draftMock.mockReset();
  controlMock.mockReset();
  filePlanIssueMock.mockReset();
  streamMock.mockReset();
  controlMock.mockResolvedValue({
    handled: false,
    action: "not_a_command",
    text: "",
    detail: "no leading control verb",
    actor_user_id: "ULOCALCLIENT",
  });
  streamMock.mockRejectedValue(new ApiError("stream unavailable", "load failed"));
});

describe("Ask adapter: onNew streaming + message conversion", () => {
  it("streams assistant tokens in place through onNew, then reconciles", async () => {
    streamMock.mockImplementation(async (_baseUrl, _request, onToken) => {
      onToken("Which repository ");
      onToken("is the attendees table in?");
      return converseResponse({ reply: "Which repository is the attendees table in?" });
    });
    const user = userEvent.setup();
    renderChat();

    await send(user, "Add a CSV download button");

    await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(1));
    // The streamed text landed in a single assistant bubble (converted from our
    // external store, not a default assistant-ui bubble).
    expect(
      await screen.findByText(/which repository is the attendees table in\?/i),
    ).toBeInTheDocument();
    expect(converseMock).not.toHaveBeenCalled();
  });

  it("holds a live streamed conversation in the hosted browser (no native actions)", async () => {
    // Regression: the desktop Ask was gated on `supportsNativeActions` (Tauri
    // only), so in the browser served by `alfred serve` it never streamed and
    // dropped straight to the offline draft fallback. The engine runs
    // server-side, so with native actions OFF but conversation available, a
    // send must go through the streaming converse endpoint, not the draft path.
    conversationAvailable = true;
    streamMock.mockImplementation(async (_baseUrl, _request, onToken) => {
      onToken("Lucius is retrying ");
      onToken("a failed run right now.");
      return converseResponse({ reply: "Lucius is retrying a failed run right now." });
    });
    const user = userEvent.setup();
    renderChat();

    await send(user, "what's the fleet doing?");

    await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(1));
    expect(
      await screen.findByText(/lucius is retrying a failed run right now\./i),
    ).toBeInTheDocument();
    // It conversed through the server, it did NOT fall back to the draft form.
    expect(draftMock).not.toHaveBeenCalled();
  });

  it("falls back to the offline draft path when conversation is unavailable", async () => {
    // A bare non-served preview (no Tauri, no hosted server) cannot converse, so
    // Ask keeps its reliable offline draft fallback rather than a broken stream.
    conversationAvailable = false;
    draftMock.mockResolvedValue({
      draft_id: "compose-20260603-120000-add-csv-export",
      saved_path: "/state/planning-drafts/compose-20260603-120000-add-csv-export.json",
      title: "Add CSV export to the attendees table",
      readiness: { ok: false, score: 40 },
      questions: [],
      findings: [],
      summary: "",
      spec_body: "",
      revision_count: 0,
      draft: converseResponse().draft,
    });
    const user = userEvent.setup();
    renderChat();

    await send(user, "Add a CSV download button");

    await waitFor(() => expect(draftMock).toHaveBeenCalledTimes(1));
    expect(streamMock).not.toHaveBeenCalled();
  });

  it("retries the live engine after one unavailable turn", async () => {
    draftMock.mockResolvedValue({
      draft_id: "compose-20260603-120000-first-turn",
      saved_path: "/state/planning-drafts/compose-20260603-120000-first-turn.json",
      title: "First turn fallback",
      readiness: { ok: false, score: 0 },
      questions: [],
      findings: [],
      summary: "",
      spec_body: "",
      revision_count: 0,
      draft: converseResponse().draft,
    });
    streamMock
      .mockRejectedValueOnce(
        new ApiError(
          "No live engine is available.",
          'alfred serve returned 503: {"error":"live_session_unavailable"}',
        ),
      )
      .mockResolvedValueOnce(
        converseResponse({ reply: "Claude Code and Codex are ready now." }),
      );
    const user = userEvent.setup();
    renderChat();

    await send(user, "Which engines are installed?");
    await waitFor(() => expect(draftMock).toHaveBeenCalledTimes(1));

    await send(user, "Try the live engines again");

    await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByText(/claude code and codex are ready now/i),
    ).toBeInTheDocument();
    expect(draftMock).toHaveBeenCalledTimes(1);
  });

  it("renders the user turn and the assistant turn as distinct bubbles", async () => {
    streamMock.mockImplementation(async () => converseResponse());
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Add a CSV download button to the attendees table");

    expect(
      await screen.findByText(/how should alfred verify this worked\?/i),
    ).toBeInTheDocument();
    expect(container.querySelector(".ask-bubble--user")).toBeInTheDocument();
    expect(container.querySelector(".ask-bubble--assistant")).toBeInTheDocument();
    // The "You" / "Alfred" role labels come from the converted message parts.
    expect(screen.getByText(/^You$/)).toBeInTheDocument();
    expect(screen.getByText(/^Alfred$/)).toBeInTheDocument();
  });

  it("renders a substantive draft as the inline lifecycle card (tool-call part)", async () => {
    streamMock.mockImplementation(async () =>
      converseResponse({ readiness: { score: 92, ready: true, missing: [] } }),
    );
    const user = userEvent.setup();
    renderChat();

    await send(user, "Build it");

    // The custom alfred-draft tool-call part renders the lifecycle card.
    expect(await screen.findByLabelText(/plan alfred is shaping/i)).toBeInTheDocument();
    expect(screen.getByText(/^Ready to file$/)).toBeInTheDocument();
  });

  it("keeps the regenerate control on the reply when a draft card trails it", async () => {
    // A build turn emits the text reply AND a separate trailing draft message,
    // so a strict "last message" gate would hide regenerate. It must stay on
    // the reply.
    streamMock.mockImplementation(async () =>
      converseResponse({ readiness: { score: 92, ready: true, missing: [] } }),
    );
    const user = userEvent.setup();
    renderChat();

    await send(user, "Build it");

    expect(await screen.findByLabelText(/plan alfred is shaping/i)).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /regenerate this reply/i }),
    ).toBeInTheDocument();
  });

  it("retry after a first-hop failure replays the failed message, not an earlier turn", async () => {
    // First turn succeeds and lands in the transcript.
    streamMock.mockImplementation(async () => converseResponse({ reply: "First reply" }));
    const user = userEvent.setup();
    renderChat();
    await send(user, "first message");
    expect(await screen.findByText(/first reply/i)).toBeInTheDocument();

    // The second send fails on its FIRST hop (a control error). That path removes
    // the failed user turn from the transcript and restores the text to the
    // composer, so the transcript's last user turn is now the OLDER message.
    controlMock.mockRejectedValueOnce(new Error("control endpoint down"));
    await send(user, "second message");
    await waitFor(() => expect(chatInput()).toHaveValue("second message"));

    // Capture what the next replay actually sends.
    let replayed: string | undefined;
    streamMock.mockImplementation(async (_baseUrl, request) => {
      const msgs = (request as { messages: { role: string; content: string }[] }).messages;
      replayed = [...msgs].reverse().find((m) => m.role === "user")?.content;
      return converseResponse({ reply: "Second reply" });
    });

    // Regenerate must replay the failed "second message", not the earlier turn.
    await user.click(await screen.findByRole("button", { name: /regenerate this reply/i }));
    await waitFor(() => expect(replayed).toBe("second message"));
  });

  it("files a ready plan straight from the inline card", async () => {
    streamMock.mockImplementation(async () =>
      converseResponse({ readiness: { score: 92, ready: true, missing: [] } }),
    );
    filePlanIssueMock.mockResolvedValue({
      ok: true,
      status: "filed",
      draft_id: "compose-20260603-120000-add-csv-export",
      issue_url: "https://github.com/your-org/frontend/issues/42",
      repo: "your-org/frontend",
      label: "agent:implement",
    });
    const user = userEvent.setup();
    renderChat();

    await send(user, "Build it");
    await user.click(await screen.findByRole("button", { name: /file issue/i }));

    await waitFor(() => expect(filePlanIssueMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/filed with agent:implement/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /view issue/i })).toBeInTheDocument();
  });
});

describe("Ask recent-threads switcher (last-5 persistence)", () => {
  it("hides the Recent control until there is more than one stored thread", async () => {
    streamMock.mockImplementation(async () => converseResponse());
    const user = userEvent.setup();
    renderChat();

    await send(user, "Add a CSV export");
    await screen.findByText(/how should alfred verify this worked\?/i);

    // One active thread only: nothing to switch to yet.
    expect(screen.queryByRole("button", { name: /recent/i })).not.toBeInTheDocument();
  });

  it("resumes a prior conversation from the recent switcher", async () => {
    // Seed one settled conversation, then a new chat creates a second, so the
    // switcher has two entries.
    streamMock.mockImplementationOnce(async () =>
      converseResponse({ reply: "First conversation reply." }),
    );
    const user = userEvent.setup();
    renderChat();

    await send(user, "First conversation question");
    await screen.findByText(/first conversation reply\./i);

    // Start a fresh chat (the prior one is kept in the last-5 list).
    await user.click(screen.getByRole("button", { name: /new chat/i }));
    streamMock.mockImplementationOnce(async () =>
      converseResponse({ reply: "Second conversation reply." }),
    );
    await send(user, "Second conversation question");
    await screen.findByText(/second conversation reply\./i);

    // The second conversation is the active thread, so its reply is on screen
    // and the first conversation's reply is not.
    expect(screen.queryByText(/first conversation reply\./i)).not.toBeInTheDocument();

    // The Recent switcher now has two entries; open it and resume the first.
    await user.click(screen.getByRole("button", { name: /recent/i }));
    const menu = await screen.findByLabelText(/recent ask conversations/i);
    await user.click(within(menu).getByText(/first conversation question/i));

    // Resuming restores the first conversation's transcript and drops the
    // second's from view.
    expect(await screen.findByText(/first conversation reply\./i)).toBeInTheDocument();
    expect(screen.queryByText(/second conversation reply\./i)).not.toBeInTheDocument();
  });

  it("preserves the active conversation when switching threads mid-stream", async () => {
    const user = userEvent.setup();
    renderChat();

    // Two settled conversations so the Recent switcher is available.
    streamMock.mockImplementationOnce(async () => converseResponse({ reply: "B reply." }));
    await send(user, "Question B");
    await screen.findByText(/b reply\./i);
    await user.click(screen.getByRole("button", { name: /new chat/i }));
    streamMock.mockImplementationOnce(async () => converseResponse({ reply: "C reply." }));
    await send(user, "Question C");
    await screen.findByText(/c reply\./i);
    await user.click(screen.getByRole("button", { name: /new chat/i }));

    // Start conversation A with a stream that never settles, so it stays busy
    // and the settle effect does not persist it.
    streamMock.mockImplementationOnce(() => new Promise<ConverseResponse>(() => {}));
    await send(user, "Question A unfinished");
    expect(await screen.findByText(/question a unfinished/i)).toBeInTheDocument();

    // Switch to B mid-stream. Without the persist-before-switch fix, A's turn is
    // dropped because the swap replaces it while busy.
    await user.click(screen.getByRole("button", { name: /recent/i }));
    let menu = await screen.findByLabelText(/recent ask conversations/i);
    await user.click(within(menu).getByText(/question b/i));
    await screen.findByText(/b reply\./i);

    // A must still be recoverable from Recent: its message was persisted.
    await user.click(screen.getByRole("button", { name: /recent/i }));
    menu = await screen.findByLabelText(/recent ask conversations/i);
    await user.click(within(menu).getByText(/question a unfinished/i));
    expect(await screen.findByText(/question a unfinished/i)).toBeInTheDocument();
  });

  it("rehydrates the most recent conversation on mount (and survives across mounts)", async () => {
    streamMock.mockImplementation(async () =>
      converseResponse({ reply: "Persisted reply." }),
    );
    const user = userEvent.setup();
    const first = renderChat();

    await send(user, "A question to persist");
    await screen.findByText(/persisted reply\./i);

    first.unmount();

    // A fresh mount reads the persisted conversation back from localStorage.
    renderChat();
    expect(await screen.findByText(/persisted reply\./i)).toBeInTheDocument();
    expect(screen.getByText(/a question to persist/i)).toBeInTheDocument();
  });

});

describe("Ask streaming render is incremental (perf)", () => {
  // The in-flight reply must render as fast raw text, NOT re-parse markdown +
  // run syntax highlighting on every token. Only when the turn settles does the
  // full markdown pass run. This is the fix for the chat feeling slow: parsing
  // the whole growing reply per token was O(n^2) and dominated frame time.
  it("renders the streaming reply as plain text, then rich markdown on settle", async () => {
    // A stream that emits a fenced code block, then blocks until we release it,
    // so we can inspect the DOM mid-flight before the turn reconciles.
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const replyBody = "Here is code:\n\n```js\nconst a = 1;\n```";
    streamMock.mockImplementation(async (_baseUrl, _request, onToken) => {
      onToken(replyBody);
      await gate;
      return converseResponse({ reply: replyBody });
    });

    const user = userEvent.setup();
    const { container } = renderChat();
    await send(user, "show me code");

    // Mid-stream: the raw text is on screen via the fast streaming container,
    // and NO highlighted code block (.ask-code) has been parsed yet.
    await waitFor(() =>
      expect(container.querySelector(".ask-bubble__stream-text")).toBeInTheDocument(),
    );
    expect(container.querySelector(".ask-code")).not.toBeInTheDocument();

    // Settle: the same text now renders through the memoized markdown pass, so
    // the fenced block becomes a real highlighted code block and the fast
    // streaming container is gone.
    release();
    await waitFor(() =>
      expect(container.querySelector(".ask-code")).toBeInTheDocument(),
    );
    expect(container.querySelector(".ask-bubble__stream-text")).not.toBeInTheDocument();
  });

  it("coalesces a burst of tokens into the final reply text", async () => {
    // Many onToken calls in one microtask tick must all land (buffered and
    // flushed), producing the complete concatenated reply, not a dropped tail.
    streamMock.mockImplementation(async (_baseUrl, _request, onToken) => {
      for (const word of ["The ", "quick ", "brown ", "fox ", "jumps."]) {
        onToken(word);
      }
      return converseResponse({ reply: "The quick brown fox jumps." });
    });
    const user = userEvent.setup();
    renderChat();
    await send(user, "stream a sentence");
    expect(
      await screen.findByText(/the quick brown fox jumps\./i),
    ).toBeInTheDocument();
  });
});

describe("Ask hero and copy", () => {
  it("shows the ask-anything hero and no plain/technical toggle", () => {
    renderChat();
    expect(screen.getByRole("heading", { name: /ask alfred anything/i })).toBeInTheDocument();
    expect(screen.getByText(/ask a question, or describe a change/i)).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: /plain language/i })).not.toBeInTheDocument();
  });

  it("shows generic starter prompt cards on an empty thread", () => {
    renderChat();
    // Four repo-agnostic starters, matching the ChatGPT/Base44 empty-state
    // pattern. No real repo name appears in any of them.
    expect(screen.getByRole("button", { name: /add tests to a module/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /fix a failing ci check/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /tidy a readme/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /add logging to a code path/i }),
    ).toBeInTheDocument();
  });

  it("seeds the composer from a starter chip without sending", async () => {
    const user = userEvent.setup();
    renderChat();

    await user.click(screen.getByRole("button", { name: /add tests to a module/i }));
    // The starter text lands in the composer input for editing...
    expect((chatInput() as HTMLTextAreaElement).value).toMatch(/add tests to a module/i);
    // ...but nothing was sent: no converse/stream call fired.
    expect(streamMock).not.toHaveBeenCalled();
    expect(converseMock).not.toHaveBeenCalled();
  });

  it("hides the starter prompt cards once the thread has messages", async () => {
    streamMock.mockImplementation(async () => converseResponse());
    const user = userEvent.setup();
    renderChat();

    // Present on the empty thread.
    expect(screen.getByRole("button", { name: /add tests to a module/i })).toBeInTheDocument();

    await send(user, "Add a CSV download button");
    await screen.findByText(/how should alfred verify this worked\?/i);

    // Gone once the conversation has started.
    expect(
      screen.queryByRole("button", { name: /add tests to a module/i }),
    ).not.toBeInTheDocument();
  });
});

describe("Ask plan card enrichment", () => {
  it("renders the structured sections and consequence line when the plan carries them", async () => {
    streamMock.mockImplementation(async () =>
      converseResponse({
        readiness: { score: 92, ready: true, missing: [] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "Sales reps cannot export the attendees they filtered.",
          user: "Sales rep",
          current_behavior: "",
          desired_behavior: "A download button exports the visible rows as CSV.",
          repos: ["your-org/frontend"],
          acceptance_criteria: [
            "The button downloads only the filtered rows.",
            "The file opens cleanly in a spreadsheet.",
          ],
          test_plan: "A unit test asserts the exported rows match the filtered set.",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Build it");

    expect(await screen.findByLabelText(/plan alfred is shaping/i)).toBeInTheDocument();
    // Structured section headers appear.
    expect(screen.getByText(/^Intent$/)).toBeInTheDocument();
    expect(screen.getByText(/^Scope$/)).toBeInTheDocument();
    expect(screen.getByText(/^Done when$/)).toBeInTheDocument();
    expect(screen.getByText(/^Verified by$/)).toBeInTheDocument();
    // Their content is present.
    expect(screen.getByText(/sales reps cannot export/i)).toBeInTheDocument();
    expect(screen.getByText(/the button downloads only the filtered rows/i)).toBeInTheDocument();
    // The plain-words consequence line names the target repo.
    const consequence = container.querySelector(".ask-draft__consequence");
    expect(consequence).toBeInTheDocument();
    expect(consequence).toHaveTextContent(/files a real issue on frontend/i);
    expect(consequence).toHaveTextContent(/engineer-agent picks it up and opens a pull request/i);
  });

  it("omits every empty section (no bare headers) on a thin draft", async () => {
    streamMock.mockImplementation(async () =>
      converseResponse({
        readiness: { score: 40, ready: false, missing: ["a test plan"] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "",
          // `user` gives the draft enough substance to surface the card at all,
          // but it is NOT one of the card's rendered sections, so the enriched
          // detail block must still be absent.
          user: "Sales rep",
          current_behavior: "",
          desired_behavior: "",
          repos: [],
          acceptance_criteria: [],
          test_plan: "",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Add a CSV download button");

    expect(await screen.findByLabelText(/plan alfred is shaping/i)).toBeInTheDocument();
    // No structured detail block and no section headers when there is no data.
    expect(container.querySelector(".ask-draft__detail")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Intent$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Done when$/)).not.toBeInTheDocument();
    // With no repo, the card must NOT promise a real filing; it shows the neutral
    // "name the repo" hint instead.
    const consequence = container.querySelector(".ask-draft__consequence");
    expect(consequence).toBeInTheDocument();
    expect(consequence).not.toHaveTextContent(/files a real issue/i);
    expect(consequence).toHaveTextContent(/name the repo/i);
  });

  it("does not promise a real filing for a repo-less draft, but does once a repo is named", async () => {
    // Repo-less: the file path refuses it, so the "files a real issue" line must
    // not appear (it would overpromise). The neutral hint appears instead.
    streamMock.mockImplementationOnce(async () =>
      converseResponse({
        readiness: { score: 55, ready: false, missing: ["a repo"] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "Sales reps cannot export attendees.",
          user: "Sales rep",
          current_behavior: "",
          desired_behavior: "A download button exports the visible rows as CSV.",
          repos: [],
          acceptance_criteria: [],
          test_plan: "",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Add a CSV download button");
    await screen.findByLabelText(/plan alfred is shaping/i);
    let consequence = container.querySelector(".ask-draft__consequence");
    expect(consequence).toBeInTheDocument();
    expect(consequence).not.toHaveTextContent(/files a real issue/i);
    expect(consequence).toHaveClass("ask-draft__consequence--hint");
    expect(consequence).toHaveTextContent(/name the repo/i);

    // A follow-up turn names a repo: now the consequence line promises the filing.
    streamMock.mockImplementationOnce(async () =>
      converseResponse({
        readiness: { score: 90, ready: true, missing: [] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "Sales reps cannot export attendees.",
          user: "Sales rep",
          current_behavior: "",
          desired_behavior: "A download button exports the visible rows as CSV.",
          repos: ["your-org/frontend"],
          acceptance_criteria: [],
          test_plan: "",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    await send(user, "It is in the frontend");
    await screen.findByText(/^Ready to file$/);
    // The second turn appends a new draft card, so read the LAST consequence line
    // (the repo-named one), not the earlier repo-less hint still in the transcript.
    const all = container.querySelectorAll(".ask-draft__consequence");
    consequence = all[all.length - 1];
    expect(consequence).toBeInTheDocument();
    expect(consequence).not.toHaveClass("ask-draft__consequence--hint");
    expect(consequence).toHaveTextContent(/files a real issue on frontend/i);
  });

  it("keeps a title + repo-only draft as the simple card (no enriched block, no filing promise)", async () => {
    // A bare repo is not "detail": a draft carrying only a title and a repo must
    // stay the simple card, not expand the Intent/Scope/Done-when block, and must
    // not promise a filing until it is actually ready.
    streamMock.mockImplementation(async () =>
      converseResponse({
        readiness: { score: 45, ready: false, missing: ["a problem statement"] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "",
          user: "",
          current_behavior: "",
          desired_behavior: "",
          repos: ["your-org/frontend"],
          acceptance_criteria: [],
          test_plan: "",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Add a CSV download button in the frontend");
    await screen.findByLabelText(/plan alfred is shaping/i);

    // No enriched detail block and no section headers for a repo-only draft.
    expect(container.querySelector(".ask-draft__detail")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Intent$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Scope$/)).not.toBeInTheDocument();
    // The repo is present but the draft is not ready, so no filing promise.
    const consequence = container.querySelector(".ask-draft__consequence");
    expect(consequence).toBeInTheDocument();
    expect(consequence).toHaveClass("ask-draft__consequence--hint");
    expect(consequence).not.toHaveTextContent(/files a real issue/i);
    expect(consequence).toHaveTextContent(/add the missing detail/i);
  });

  it("shows the hint (not the filing promise) for a repo'd but unready draft with detail", async () => {
    // Has a repo AND real structured content, but the server judges it not ready
    // (a required field like the test plan is missing). The enriched block shows,
    // but the filing promise is gated on readiness, so the hint appears instead.
    streamMock.mockImplementation(async () =>
      converseResponse({
        readiness: { score: 70, ready: false, missing: ["a test plan"] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "Sales reps cannot export the attendees they filtered.",
          user: "Sales rep",
          current_behavior: "",
          desired_behavior: "A download button exports the visible rows as CSV.",
          repos: ["your-org/frontend"],
          acceptance_criteria: ["The button downloads only the filtered rows."],
          test_plan: "",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Add a CSV download button in the frontend");
    await screen.findByLabelText(/plan alfred is shaping/i);

    // The enriched block DOES render (there is real content).
    expect(container.querySelector(".ask-draft__detail")).toBeInTheDocument();
    expect(screen.getByText(/^Intent$/)).toBeInTheDocument();
    // But the filing promise is withheld because the draft is not ready.
    const consequence = container.querySelector(".ask-draft__consequence");
    expect(consequence).toBeInTheDocument();
    expect(consequence).toHaveClass("ask-draft__consequence--hint");
    expect(consequence).not.toHaveTextContent(/files a real issue/i);
    expect(consequence).toHaveTextContent(/add the missing detail/i);
  });

  it("names only the single target repo (repos[0]) for a ready multi-repo draft", async () => {
    // The filing path files the issue in the FIRST repo only, so a ready draft
    // listing several repos must promise a filing in just that one, not the whole
    // list, and note that the rest are context.
    streamMock.mockImplementation(async () =>
      converseResponse({
        readiness: { score: 92, ready: true, missing: [] },
        draft: {
          title: "Add CSV export to the attendees table",
          problem: "Sales reps cannot export the attendees they filtered.",
          user: "Sales rep",
          current_behavior: "",
          desired_behavior: "A download button exports the visible rows as CSV.",
          repos: ["your-org/frontend", "your-org/api"],
          acceptance_criteria: [],
          test_plan: "A unit test asserts the exported rows match the filtered set.",
          out_of_scope: "",
          rollout: "",
          open_questions: "",
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderChat();

    await send(user, "Build it");
    await screen.findByLabelText(/plan alfred is shaping/i);

    const consequence = container.querySelector(".ask-draft__consequence");
    expect(consequence).toBeInTheDocument();
    // Only the first repo is named as the filing target.
    expect(consequence).toHaveTextContent(/files a real issue on frontend\./i);
    // The second repo is NOT promised a filing.
    expect(consequence).not.toHaveTextContent(/on frontend, api/i);
    expect(consequence).not.toHaveTextContent(/issue on api/i);
    // The note clarifies the other repos are context, not extra issues.
    expect(consequence).toHaveTextContent(/other repos are context, not extra issues/i);
  });
});
