import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CustomThemeEditor } from "./CustomThemeEditor";

describe("CustomThemeEditor", () => {
  it("renders live custom agents beyond the shipped preset roster", () => {
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        agents={[
          {
            codename: "security-scout",
            role: "reviewer",
            defaultName: "Sentinel",
            defaultRoleLabel: "Security reviewer",
          },
          {
            codename: "ops-sentinel",
            role: "ops",
            defaultName: "Ops Sentinel",
            defaultRoleLabel: "Ops & health",
          },
        ]}
        onOpenChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const nameInput = screen.getByLabelText("Sentinel name");
    expect(nameInput).toHaveAttribute("placeholder", "Sentinel");
    const row = nameInput.closest(".custom-theme-editor__row");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByLabelText("Role label")).toHaveAttribute(
      "placeholder",
      "Security reviewer",
    );
  });

  it("surfaces a parent saveError inline without dismissing the dialog", () => {
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        saveError="Could not save to Alfred. The roster is local-only until a save succeeds."
        onOpenChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not save to alfred/i);
  });

  it("stays open and shows the error when the save REJECTS", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    // onSave rejects (a failed POST). The dialog must not close, and must show
    // the error surfaced from the rejection.
    const onSave = vi.fn().mockRejectedValue(new Error("alfred serve returned 403"));
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        onOpenChange={onOpenChange}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save roster/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/403/);
    });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("does NOT close before a SLOW rejection resolves (no fixed-timer race)", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    // The exact Greptile race: the failure arrives well after any short timer
    // would have fired. Closure is tied to the awaited outcome, so the dialog
    // must still be open until the rejection lands, then stay open with the error.
    let rejectSave: (err: unknown) => void = () => {};
    const onSave = vi.fn(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectSave = reject;
        }),
    );
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        onOpenChange={onOpenChange}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save roster/i }));
    // Give any stray timer a chance to fire; the dialog must NOT have closed.
    await new Promise((r) => setTimeout(r, 300));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    // Now the slow failure lands: still open, error shown.
    rejectSave(new Error("network failure after 300ms"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/network failure/i);
    });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("closes only after the save RESOLVES", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    let resolveSave: () => void = () => {};
    const onSave = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSave = resolve;
        }),
    );
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        onOpenChange={onOpenChange}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save roster/i }));
    // Not closed while the save is still in flight.
    await new Promise((r) => setTimeout(r, 200));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    // The save lands: now it closes.
    resolveSave();
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });
});
