import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RosterThemePicker } from "./RosterThemePicker";

describe("RosterThemePicker", () => {
  it("blocks roster writes and exposes retry while hydration failed", async () => {
    const onChange = vi.fn();
    const onEditCustom = vi.fn();
    const onRetry = vi.fn();
    render(
      <RosterThemePicker
        value="batman"
        onChange={onChange}
        onEditCustom={onEditCustom}
        disabled
        saveError="Could not load saved fleet names from Alfred."
        onRetry={onRetry}
      />,
    );
    const user = userEvent.setup();

    expect(screen.getByRole("combobox", { name: /roster theme/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /customize/i }));
    expect(onEditCustom).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /^retry$/i }));

    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });
});
