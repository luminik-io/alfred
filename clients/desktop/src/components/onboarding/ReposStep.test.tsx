import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import * as apiSetup from "../../api/setup";
import { ReposStep } from "./ReposStep";

describe("repository owner selection", () => {
  it("prevents a fresh selection from spanning multiple owners", async () => {
    vi.spyOn(apiSetup, "loadSetupRepos").mockResolvedValue({
      repos: [
        {
          name_with_owner: "acme/api",
          description: null,
          is_private: false,
          is_fork: false,
          updated_at: null,
          selected: false,
          selectable: true,
        },
        {
          name_with_owner: "personal/site",
          description: null,
          is_private: false,
          is_fork: false,
          updated_at: null,
          selected: false,
          selectable: true,
        },
      ],
      selected: [],
      repo_checkouts: [],
    });
    const user = userEvent.setup();

    render(
      <ReposStep
        baseUrl="http://127.0.0.1:7010"
        canMutate
        canRun
        githubConnected
        selectedCount={0}
        onSaved={vi.fn(async () => ({ indexed: false }))}
        setNotice={vi.fn()}
      />,
    );

    const acme = await screen.findByRole("checkbox", { name: /api/i });
    const personal = screen.getByRole("checkbox", { name: /site/i });
    await user.click(acme);

    expect(personal).toBeDisabled();
    expect(screen.getByText("different owner")).toBeInTheDocument();

    await user.click(acme);
    expect(personal).toBeEnabled();
  });
});
