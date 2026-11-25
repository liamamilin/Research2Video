import { describe, expect, it, vi } from "vitest";
import { announceSessionChange } from "./session-events";
import { invalidatePageSnapshots, readPageSnapshot, savePageSnapshot, snapshotRevision } from "./page-snapshots";

describe("page snapshots", () => {
  it("isolates scopes and never restores another login's result", () => {
    savePageSnapshot("active", [1], snapshotRevision());
    expect(readPageSnapshot("archived")).toBeNull();
    announceSessionChange("logout");
    expect(readPageSnapshot("active")).toBeNull();
  });
  it("rejects an old response that completes after a write", () => {
    const before = snapshotRevision();
    invalidatePageSnapshots();
    savePageSnapshot("page", [1], before);
    expect(readPageSnapshot("page")).toBeNull();
  });
  it("expires old visits and bounds retained pages", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1000);
    for (let i = 0; i < 41; i++) savePageSnapshot(i, i, snapshotRevision());
    expect(readPageSnapshot(0)).toBeNull();
    expect(readPageSnapshot(40)).toBe(40);
    now.mockReturnValue(61_001);
    expect(readPageSnapshot(40)).toBeNull();
    now.mockRestore();
  });
});
