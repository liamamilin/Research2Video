import { afterEach, expect, it, vi } from "vitest";
import { recoverChunk } from "./page-recovery";
afterEach(() => sessionStorage.clear());
it("refreshes a stale chunk once with the exact route and flushes drafts before reloading", () => {
  const flush = vi.fn(), reload = vi.fn(); window.addEventListener("ui-before-reload", flush);
  const error = new Error("Failed to fetch dynamically imported module /old-hash.js");
  const href = "http://local/video/s?tab=publish&render=exact&prepare=douyin%3A123";
  expect(recoverChunk(error, sessionStorage, href, reload, 1000)).toBe(true);
  expect(flush).toHaveBeenCalledOnce(); expect(reload).toHaveBeenCalledOnce();
  expect(recoverChunk(error, sessionStorage, href, reload, 2000)).toBe(false);
  expect(reload).toHaveBeenCalledOnce(); window.removeEventListener("ui-before-reload", flush);
});
it("does not refresh unrelated errors or loop when storage fails", () => {
  const reload = vi.fn(); expect(recoverChunk(new Error("render bug"), sessionStorage, "/", reload)).toBe(false);
  const storage = { getItem: () => { throw Error("disabled"); } } as unknown as Storage;
  expect(recoverChunk(new Error("Unable to preload CSS"), storage, "/", reload)).toBe(false); expect(reload).not.toHaveBeenCalled();
});
