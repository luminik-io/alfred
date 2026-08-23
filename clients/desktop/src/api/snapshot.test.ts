import { beforeEach, describe, expect, it, vi } from "vitest";

const clientMocks = vi.hoisted(() => ({
  readAlfredJson: vi.fn(),
}));

vi.mock("./client", () => ({
  readAlfredJson: clientMocks.readAlfredJson,
  settledError: (reason: unknown) => String(reason),
  streamingUrl: (_baseUrl: string, path: string) => path,
  withTimeout: <T>(promise: Promise<T>) => promise,
}));

import { loadUsage } from "./snapshot";

describe("loadUsage", () => {
  beforeEach(() => {
    clientMocks.readAlfredJson.mockReset();
  });

  it("loads subscription usage from the versioned contract", async () => {
    const expected = {
      available: true,
      kind: "subscription",
      source: "fixture",
    };
    clientMocks.readAlfredJson.mockResolvedValue(expected);

    await expect(loadUsage("http://127.0.0.1:7010")).resolves.toBe(expected);
    expect(clientMocks.readAlfredJson).toHaveBeenCalledOnce();
    expect(clientMocks.readAlfredJson).toHaveBeenCalledWith(
      "http://127.0.0.1:7010",
      "/api/v1/usage",
    );
  });
});
