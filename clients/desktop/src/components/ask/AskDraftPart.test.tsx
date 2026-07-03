import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { AskDraftPart } from "./AskDraftPart";
import { AskSurfaceProvider, type AskSurface } from "./AskContext";
import type { DraftCardModel } from "./askModel";

vi.mock("../../lib/links", async () => {
  const actual = await vi.importActual<typeof import("../../lib/links")>("../../lib/links");
  return { ...actual, openExternal: vi.fn() };
});

function draft(overrides: Partial<DraftCardModel> = {}): DraftCardModel {
  return {
    draftId: "draft-a",
    title: "Add a docs smoke test",
    repos: ["acme/api"],
    ready: true,
    questions: [],
    ...overrides,
  };
}

// AskDraftPart only reads `args`; assistant-ui passes many other props we do not
// use here, so a narrow cast keeps the test focused on the file-button behavior.
function renderDraft(model: DraftCardModel, surface: Partial<AskSurface>) {
  const value: AskSurface = {
    fileBusyId: null,
    fileNotices: {},
    onFile: vi.fn(),
    onOpenWork: vi.fn(),
    ...surface,
  };
  render(
    <AskSurfaceProvider value={value}>
      <AskDraftPart {...({ args: { draft: model } } as unknown as ComponentProps<typeof AskDraftPart>)} />
    </AskSurfaceProvider>,
  );
  return value;
}

describe("AskDraftPart file button", () => {
  it("disables a sibling card's File button while a DIFFERENT card is filing", () => {
    // The file path is single-flight (one global guard in useAskThread), so a
    // sibling card must NOT look active-but-dead: it is disabled while draft-a
    // files, and does not show the filing spinner (that stays on draft-a).
    renderDraft(draft({ draftId: "draft-b" }), { fileBusyId: "draft-a" });
    const button = screen.getByRole("button", { name: /file issue/i });
    expect(button).toBeDisabled();
  });

  it("disables and shows a spinner only on the card that is filing", () => {
    renderDraft(draft({ draftId: "draft-a" }), { fileBusyId: "draft-a" });
    // Only the filing card shows the "Filing..." label.
    const button = screen.getByRole("button", { name: /filing/i });
    expect(button).toBeDisabled();
  });

  it("enables every File button when nothing is in flight", () => {
    renderDraft(draft({ draftId: "draft-a" }), { fileBusyId: null });
    const button = screen.getByRole("button", { name: /file issue/i });
    expect(button).toBeEnabled();
  });

  it("fires onFile with this card's draftId when clicked", async () => {
    const onFile = vi.fn();
    renderDraft(draft({ draftId: "draft-a" }), { onFile });
    const button = screen.getByRole("button", { name: /file issue/i });
    button.click();
    expect(onFile).toHaveBeenCalledWith("draft-a");
  });
});
