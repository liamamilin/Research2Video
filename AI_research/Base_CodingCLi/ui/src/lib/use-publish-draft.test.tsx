import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { usePublishDraft } from "./use-publish-draft";
import { useAuthStore } from "./auth-store";
const initial = { title: "标题", description: "简介", tags: "科技", tid: 188, firstComment: { enabled: false, text: "", pin: false } };
afterEach(() => { cleanup(); localStorage.clear(); });
it("tracks every field, survives remount, retains saved baselines and isolates platform/account/render/user", () => {
  useAuthStore.setState({user:{username:"one",role:"editor"} as any});
  const left = renderHook(() => usePublishDraft("bilibili", "r1", "123", initial, true));
  act(() => { left.result.current.field("tags", "新话题，"); left.result.current.field("tid", 95); left.result.current.field("firstComment", { enabled:true,text:"首评",pin:true }); });
  expect(left.result.current.dirty).toBe(true); left.unmount();
  const restored = renderHook(() => usePublishDraft("bilibili", "r1", "123", initial, true));
  expect(restored.result.current.recovered).toBe(true); expect(restored.result.current.value.tags).toBe("新话题，"); expect(restored.result.current.value.tid).toBe(95);
  for (const [p,r,a] of [["douyin","r1","123"],["bilibili","r2","123"],["bilibili","r1","456"]]) {
    const other = renderHook(() => usePublishDraft(p,r,a,initial,true)); expect(other.result.current.value).toEqual(initial); other.unmount();
  }
  act(() => restored.result.current.markSaved()); expect(restored.result.current.status).toBe("已保存本平台草稿");
  act(() => restored.result.current.field("description", "另改")); expect(restored.result.current.dirty).toBe(true);
  act(() => restored.result.current.discard()); expect(restored.result.current.value.firstComment.text).toBe("首评"); expect(restored.result.current.dirty).toBe(false); restored.unmount();
  useAuthStore.setState({user:{username:"two",role:"editor"} as any});
  expect(renderHook(() => usePublishDraft("bilibili","r1","123",initial,true)).result.current.value).toEqual(initial);
});
it("resets a mounted editor on account or render changes without copying old edits", () => {
  const hook = renderHook(({account, render}) => usePublishDraft("douyin",render,account,initial,false), {initialProps:{account:"123",render:"a"}});
  act(() => hook.result.current.field("title","账号甲的输入"));
  hook.rerender({account:"456",render:"a"}); expect(hook.result.current.value.title).toBe(initial.title);
  act(() => hook.result.current.field("title","账号乙的输入"));
  hook.rerender({account:"123",render:"b"}); expect(hook.result.current.value.title).toBe(initial.title);
  hook.rerender({account:"123",render:"a"}); expect(hook.result.current.value.title).toBe("账号甲的输入");
});
