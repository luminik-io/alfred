import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiSetup from "../../api/setup";
import { ReposStep } from "./ReposStep";

describe("repository owner selection", () => {
  afterEach(() => vi.restoreAllMocks());

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

  it("unlocks other owners immediately after clearing an existing scope", async () => {
    vi.spyOn(apiSetup, "loadSetupRepos").mockResolvedValue({
      repos: [
        {
          name_with_owner: "acme/api",
          description: null,
          is_private: false,
          is_fork: false,
          updated_at: null,
          selected: true,
          selectable: true,
        },
        {
          name_with_owner: "personal/site",
          description: null,
          is_private: false,
          is_fork: false,
          updated_at: null,
          selected: false,
          selectable: false,
        },
      ],
      selected: ["acme/api"],
      repo_checkouts: [
        {
          repo: "acme/api",
          path: "/workspace/api",
          source: "map",
          exists: true,
          is_git_repo: true,
          github_remote_name: "origin",
          github_remote_repo: "acme/api",
          identity_matches: true,
          ready: true,
          reason: null,
        },
      ],
    });
    vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: [],
      repo_checkouts: [],
      env_path: "/runtime/.env",
      keys: [],
    });
    const user = userEvent.setup();

    render(
      <ReposStep
        baseUrl="http://127.0.0.1:7010"
        canMutate
        canRun
        githubConnected
        selectedCount={1}
        onSaved={vi.fn(async () => ({ indexed: false }))}
        setNotice={vi.fn()}
      />,
    );

    const acme = await screen.findByRole("checkbox", { name: /api/i });
    const personal = screen.getByRole("checkbox", { name: /site/i });
    expect(personal).toBeDisabled();

    await user.click(acme);
    await user.click(screen.getByRole("button", { name: "Clear repository scope" }));

    expect(personal).toBeEnabled();
  });

  it("prefills an auto-detected checkout when a repository is selected", async () => {
    const detected = {
      repo: "acme/api",
      path: "/workspace/nested/api",
      source: "discovery" as const,
      exists: true,
      is_git_repo: true,
      github_remote_name: "origin",
      github_remote_repo: "acme/api",
      identity_matches: true,
      ready: true,
      reason: null,
    };
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
      ],
      selected: [],
      repo_checkouts: [detected],
    });
    const save = vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["acme/api"],
      repo_checkouts: [detected],
      env_path: "/runtime/.env",
      keys: [],
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

    await user.click(await screen.findByRole("checkbox", { name: /api/i }));

    expect(screen.getByLabelText("Local checkout for acme/api")).toHaveValue(
      "/workspace/nested/api",
    );
    expect(screen.getByText("GitHub repository verified")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save 1 selected" }));
    expect(save).toHaveBeenCalledWith("http://127.0.0.1:7010", ["acme/api"], [
      { repo: "acme/api", path: "/workspace/nested/api" },
    ]);
  });
});
