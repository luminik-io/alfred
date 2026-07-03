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
            role: "review",
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

  it("surfaces a save error inline instead of closing on a failed save", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        saveError="Could not save to Alfred. The roster is local-only until a save succeeds."
        onOpenChange={onOpenChange}
        onSave={vi.fn()}
      />,
    );

    // The error is visible in the dialog (not hidden behind a dismissed modal).
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not save to alfred/i);

    // Saving while an error is present must NOT close the dialog.
    await user.click(screen.getByRole("button", { name: /save roster/i }));
    await waitFor(
      () => {
        expect(onOpenChange).not.toHaveBeenCalledWith(false);
      },
      { timeout: 500 },
    );
  });

  it("closes after a clean save with no error", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <CustomThemeEditor
        open
        value={{ names: {}, roles: {} }}
        saveError={null}
        onOpenChange={onOpenChange}
        onSave={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save roster/i }));
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });
});
