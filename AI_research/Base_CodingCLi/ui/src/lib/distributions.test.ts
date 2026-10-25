import { describe, expect, it } from "vitest";
import type { Distribution } from "@/api/video";
import { matchesDistribution, publishedPlatformCount } from "./distributions";
const row = (platform: string, status: string, account_uid = "current", current_account = true) => ({ platform, status, account_uid, current_account }) as Distribution;
describe("target distribution filters", () => {
  it("finds Bilibili-published videos still unpublished on Douyin", () => {
    const rows = [row("bilibili", "PUBLISHED"), row("douyin", "UNPUBLISHED")];
    expect(matchesDistribution(rows, "douyin", "UNPUBLISHED", true)).toBe(true);
    expect(matchesDistribution(rows, "bilibili", "UNPUBLISHED", true)).toBe(false);
    expect(matchesDistribution(rows, "", "never", true)).toBe(false);
  });
  it("counts platforms rather than records or accounts and excludes old accounts from target filtering", () => {
    const rows = [row("bilibili", "PUBLISHED"), row("bilibili", "PUBLISHED", "old", false), row("douyin", "UNPUBLISHED")];
    expect(publishedPlatformCount(rows)).toBe(1);
    expect(matchesDistribution([row("douyin", "PUBLISHED", "old", false), row("douyin", "UNPUBLISHED")], "douyin", "UNPUBLISHED", true)).toBe(true);
  });
});
