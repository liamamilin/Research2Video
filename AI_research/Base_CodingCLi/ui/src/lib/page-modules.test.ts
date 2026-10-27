import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { pageLoader, pageLoaders, preloadPage } from "./page-modules";

afterEach(() => vi.restoreAllMocks());

describe("destination code preloading", () => {
  it("selects list, detail and nested configuration pages without fetching data", () => {
    expect(pageLoader("/matrices/ai/video/daily")).toBe(pageLoaders.MatrixJobVideoPage);
    expect(pageLoader("/matrices/ai")).toBe(pageLoaders.MatrixDetailPage);
    expect(pageLoader("/jobs/research/daily")).toBe(pageLoaders.JobDetailPage);
    expect(pageLoader("/reports/view")).toBe(pageLoaders.ReportViewPage);
    expect(pageLoader("/video-recipes/parts/tone/news")).toBe(pageLoaders.VideoRecipesPage);
    expect(pageLoader("/api/video/renders/a/files/video.mp4")).toBeUndefined();
  });

  it("coalesces repeated intent and allows retry after a failed preload", async () => {
    const load = vi.spyOn(pageLoaders, "ReportsPage")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ default: () => createElement("div") });
    preloadPage("/reports");
    preloadPage("/reports");
    expect(load).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    preloadPage("/reports");
    await Promise.resolve();
    preloadPage("/reports");
    expect(load).toHaveBeenCalledTimes(2);
  });
});
