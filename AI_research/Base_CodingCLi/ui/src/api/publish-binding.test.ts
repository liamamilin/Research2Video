import { afterEach, expect, it, vi } from "vitest";
import { getPublishPack } from "./video";
afterEach(() => vi.unstubAllGlobals());
it("refuses an unbound legacy response instead of showing chapters from another render", async () => {
  vi.stubGlobal("fetch",vi.fn(async () => ({ok:true,status:200,text:async () => JSON.stringify({title:"包",version:1})})));
  await expect(getPublishPack("s",1,undefined,"exact")).rejects.toThrow("尚未支持具体成片绑定");
});
it("keeps the requested render and version in the native request", async () => {
  const fetcher = vi.fn(async (_url: unknown) => ({ok:true,status:200,text:async () => JSON.stringify({render_id:"exact",version:2})}));vi.stubGlobal("fetch",fetcher);
  expect((await getPublishPack("s",2,undefined,"exact")).render_id).toBe("exact");
  expect(String(fetcher.mock.calls[0][0])).toContain("version=2&render_id=exact");
});
