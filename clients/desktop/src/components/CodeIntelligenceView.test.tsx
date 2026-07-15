import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { loadCodeIntelligence } from "../api/code";
import type { CodeIntelligenceResponse } from "../types";
import { CodeIntelligenceView } from "./CodeIntelligenceView";

vi.mock("../api/code", () => ({
  loadCodeIntelligence: vi.fn(),
}));

const mockedLoad = vi.mocked(loadCodeIntelligence);

const summary: CodeIntelligenceResponse = {
  schema: "alfred-codegraph@1",
  generated_at: "2026-07-15T12:00:00Z",
  repo_count: 1,
  contract_drift_count: 1,
  selected_repo: null,
  query_path: null,
  impact: null,
  repos: [
    {
      name: "web",
      head_sha: "a".repeat(40),
      summary: {
        files: 12,
        symbols: 30,
        imports: 18,
        languages: { typescript: 12 },
        truncated: false,
      },
      endpoint_count: 2,
      route_count: 3,
      api_call_count: 4,
      contract_drift_count: 1,
    },
  ],
};

function analyzedSummary(): CodeIntelligenceResponse {
  return {
    ...summary,
    selected_repo: "web",
    query_path: "src/api.ts",
    impact: {
      kind: "impact-brief",
      repo: "web",
      path: "src/api.ts",
      matched_file: "src/api.ts",
      match_status: "exact",
      head_sha: "a".repeat(40),
      language: "typescript",
      level: "high",
      reasons: ["contract drift"],
      summary:
        "High impact because one caller and one contract drift record are attached.",
      counts: {
        symbols: 1,
        direct_dependents: 1,
        direct_dependencies: 0,
        contract_surfaces: 1,
        contract_drift: 1,
        nearby_files: 1,
      },
      symbols: [{ name: "loadData", line: 4 }],
      direct_dependents: [
        { path: "src/App.tsx", via: "./api", kind: "import" },
      ],
      direct_dependencies: [],
      contract_surfaces: [
        {
          kind: "api_call",
          method: "GET",
          path: "/api/data",
          file: "src/api.ts",
        },
      ],
      contract_drift: [
        {
          method: "GET",
          path: "/api/data",
          normalized: "/data",
          file: "src/api.ts",
        },
      ],
      nearby_files: ["src/api.test.ts"],
      candidate_matches: [],
      next_checks: ["Run nearby tests."],
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("CodeIntelligenceView", () => {
  beforeEach(() => {
    mockedLoad.mockReset();
  });

  it("shows the indexed repository summary", async () => {
    mockedLoad.mockResolvedValue(summary);
    render(<CodeIntelligenceView baseUrl="http://127.0.0.1:7010" />);

    expect(await screen.findByText("Code intelligence")).toBeInTheDocument();
    expect((await screen.findAllByText("12")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Typescript")).toBeInTheDocument();
    expect(mockedLoad).toHaveBeenCalledWith("http://127.0.0.1:7010", {});
  });

  it("analyzes a path and renders bounded relationships", async () => {
    mockedLoad
      .mockResolvedValueOnce(summary)
      .mockResolvedValueOnce(analyzedSummary());
    render(<CodeIntelligenceView baseUrl="http://127.0.0.1:7010" />);

    const input = await screen.findByLabelText("File path");
    fireEvent.change(input, { target: { value: "src/api.ts" } });
    fireEvent.click(screen.getByRole("button", { name: "Analyze impact" }));

    await waitFor(() =>
      expect(mockedLoad).toHaveBeenLastCalledWith("http://127.0.0.1:7010", {
        repo: "web",
        path: "src/api.ts",
      }),
    );
    expect(await screen.findByText("src/App.tsx")).toBeInTheDocument();
    expect(screen.getByText("src/api.test.ts")).toBeInTheDocument();
    expect(screen.getByText("loadData at line 4")).toBeInTheDocument();
    expect(screen.getByText("Contract drift")).toBeInTheDocument();
  });

  it("keeps the repository catalog available", async () => {
    mockedLoad.mockResolvedValue({
      ...summary,
      repos: [
        ...summary.repos,
        {
          ...summary.repos[0],
          name: "worker",
          summary: {
            ...summary.repos[0].summary,
            languages: { python: 8 },
          },
        },
      ],
    });
    render(<CodeIntelligenceView baseUrl="http://127.0.0.1:7010" />);

    fireEvent.click(
      await screen.findByRole("combobox", { name: "Repository" }),
    );
    expect(
      await screen.findByRole("option", { name: "worker" }),
    ).toBeInTheDocument();
  });

  it("ignores analysis that finishes after the repository changes", async () => {
    const staleAnalysis = deferred<CodeIntelligenceResponse>();
    const catalog: CodeIntelligenceResponse = {
      ...summary,
      repo_count: 2,
      repos: [
        ...summary.repos,
        {
          ...summary.repos[0],
          name: "worker",
          contract_drift_count: 0,
          summary: {
            ...summary.repos[0].summary,
            languages: { python: 8 },
          },
        },
      ],
    };
    mockedLoad
      .mockResolvedValueOnce(catalog)
      .mockReturnValueOnce(staleAnalysis.promise);
    render(<CodeIntelligenceView baseUrl="http://127.0.0.1:7010" />);

    fireEvent.change(await screen.findByLabelText("File path"), {
      target: { value: "src/api.ts" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Analyze impact" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Repository" }));
    fireEvent.click(await screen.findByRole("option", { name: "worker" }));

    staleAnalysis.resolve({ ...analyzedSummary(), repos: catalog.repos });

    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Repository" }),
      ).toHaveTextContent("worker"),
    );
    expect(screen.queryByText("src/App.tsx")).not.toBeInTheDocument();
    expect(screen.getByLabelText("worker index summary")).toBeInTheDocument();
  });

  it("shows how to build a missing index", async () => {
    mockedLoad.mockResolvedValue({
      ...summary,
      repos: [],
      repo_count: 0,
      contract_drift_count: 0,
    });
    render(<CodeIntelligenceView baseUrl="http://127.0.0.1:7010" />);

    expect(
      await screen.findByText("No repositories indexed yet"),
    ).toBeInTheDocument();
    expect(screen.getByText("alfred code-map build .")).toBeInTheDocument();
  });

  it("does not leave stale impact evidence visible after a read failure", async () => {
    mockedLoad
      .mockResolvedValueOnce(summary)
      .mockRejectedValueOnce(new Error("Index read failed"));
    render(<CodeIntelligenceView baseUrl="http://127.0.0.1:7010" />);

    fireEvent.change(await screen.findByLabelText("File path"), {
      target: { value: "src/api.ts" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Analyze impact" }));

    expect(
      await screen.findByText("Code intelligence is unavailable."),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("web index summary"),
    ).not.toBeInTheDocument();
  });
});
