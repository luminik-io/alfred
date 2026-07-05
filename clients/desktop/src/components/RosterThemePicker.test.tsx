import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RosterThemePicker } from "./RosterThemePicker";

describe("RosterThemePicker", () => {
  it("opens the theme builder chat from the Name your team button", async () => {
    const user = userEvent.setup();
    const onNameYourTeam = vi.fn();
    render(
      <RosterThemePicker
        value="batman"
        onChange={vi.fn()}
        onEditCustom={vi.fn()}
        onNameYourTeam={onNameYourTeam}
      />,
    );
    await user.click(screen.getByRole("button", { name: /name your team/i }));
    expect(onNameYourTeam).toHaveBeenCalledTimes(1);
  });

  it("omits the Name your team button when no handler is given", () => {
    render(<RosterThemePicker value="batman" onChange={vi.fn()} onEditCustom={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /name your team/i })).toBeNull();
  });
});
