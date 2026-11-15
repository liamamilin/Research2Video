import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import type { ScriptVersion } from "@/api/video";
import { useScriptEditor } from "./use-script-editor";

beforeEach(() => localStorage.clear());

const version = (number: number) => ({ version: number, title: `版本 ${number}`, sections: [] }) as unknown as ScriptVersion;

it("keeps recovered drafts separate when switching versions during editing", () => {
  const next = { title: "第二版草稿", sections: [], baseVersion: 2, original: "original" };
  const nextKey = "script-editor:alice:script:v2";
  localStorage.setItem(nextKey, JSON.stringify(next));
  const hook = renderHook(({ shown }) => useScriptEditor("script", "alice", shown), {
    initialProps: { shown: version(1) },
  });
  act(() => hook.result.current.start());
  act(() => hook.result.current.setTitle("第一版草稿"));
  hook.rerender({ shown: version(2) });
  expect(hook.result.current.editing).toBe(false);
  expect(hook.result.current.recovery).toEqual(next);
  expect(JSON.parse(localStorage.getItem(nextKey)!)).toEqual(next);
  expect(JSON.parse(localStorage.getItem("script-editor:alice:script:v1")!).title).toBe("第一版草稿");
  act(() => hook.result.current.start());
  expect(hook.result.current.title).toBe("第二版草稿");
});

it("does not copy unsaved text to a different script or user", () => {
  const hook = renderHook(({ id, user }) => useScriptEditor(id, user, version(1)), {
    initialProps: { id: "first", user: "alice" },
  });
  act(() => hook.result.current.start());
  act(() => hook.result.current.setTitle("仅属于 Alice 的第一份文稿"));
  hook.rerender({ id: "second", user: "bob" });
  expect(hook.result.current.recovery).toBeNull();
  expect(localStorage.getItem("script-editor:bob:second:v1")).toBeNull();
});
