import { describe, expect, it } from "vitest";

import { friendlyTime } from "./format";

// These fixtures are shared with the Python server helper test
// (tests/test_server_formatting.py). Both surfaces compute day boundaries and
// calendar fields in UTC, so a timestamp near midnight UTC renders the same
// relative date on the desktop client and in `alfred serve`.
const FIXTURE_NOW = "2026-03-16T12:00:00Z";
const FIXTURE_VALUE = "2026-03-14T23:50:00Z";
const FIXTURE_EXPECTED = "Mar 14, 23:50";

describe("friendlyTime", () => {
  it("renders the shared UTC day-boundary fixture consistently with Python", () => {
    expect(friendlyTime(FIXTURE_VALUE, new Date(FIXTURE_NOW))).toBe(FIXTURE_EXPECTED);
  });

  it("uses the UTC time of day for a 'yesterday' timestamp", () => {
    const now = new Date("2026-03-16T00:20:00Z");
    expect(friendlyTime("2026-03-15T00:05:00Z", now)).toBe("yesterday 00:05");
  });

  it("returns 'just now' inside the first minute", () => {
    const now = new Date("2026-03-15T12:00:00Z");
    expect(friendlyTime("2026-03-15T12:00:00Z", now)).toBe("just now");
  });

  it("renders minute and hour deltas", () => {
    const now = new Date("2026-03-15T12:00:00Z");
    expect(friendlyTime("2026-03-15T11:30:00Z", now)).toBe("30m ago");
    expect(friendlyTime("2026-03-15T09:00:00Z", now)).toBe("3h ago");
  });

  it("uses the UTC time of day for same-year dates", () => {
    const now = new Date("2026-03-15T00:30:00Z");
    expect(friendlyTime("2026-01-02T07:05:00Z", now)).toBe("Jan 2, 07:05");
  });

  it("drops the time for prior-year dates", () => {
    const now = new Date("2026-03-15T00:30:00Z");
    expect(friendlyTime("2025-11-20T07:05:00Z", now)).toBe("Nov 20, 2025");
  });

  it("returns 'never' for empty values", () => {
    expect(friendlyTime(null)).toBe("never");
    expect(friendlyTime("never")).toBe("never");
  });
});
