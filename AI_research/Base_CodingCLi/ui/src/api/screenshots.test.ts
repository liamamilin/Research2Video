import { afterEach, expect, it, vi } from "vitest";
import { saveScreenshotSelection } from "./screenshots";

afterEach(() => vi.unstubAllGlobals());

it("resaves a server snapshot using only editable fields, without engine UUID or source metadata", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => "{}" });
  vi.stubGlobal("fetch", fetch);
  // GET returns a frozen record with provenance. PUT accepts editorial input.
  const material = { screenshot_id: "a1", caption: "功能页", role: "detail" as const,
    placements: [{ chapter_index: 0, paragraph_index: 1 }], image_id: "engine-id", source: { url: "https://example.com" } };
  await saveScreenshotSelection("s1", 2, { form: "dynamic_collage", materials: [material] });
  const [, request] = fetch.mock.calls[0];
  expect(JSON.parse(request.body)).toEqual({ version: 2, form: "dynamic_collage", materials: [{
    screenshot_id: "a1", caption: "功能页", role: "detail", placements: [{ chapter_index: 0, paragraph_index: 1 }],
  }] });
});
