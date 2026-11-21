import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePageResource } from "./use-page-resource";
import { invalidatePageSnapshots } from "./page-snapshots";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("page resource lifecycle", () => {
  it("discards a read started before saving and fetches the saved value without waiting for a poll", async () => {
    let finish!: (value: string) => void;
    const load = vi.fn().mockImplementationOnce(() => new Promise<string>(resolve => { finish = resolve; }))
      .mockResolvedValue("保存后的数据");
    const { result } = renderHook(() => usePageResource(load));
    invalidatePageSnapshots();
    await act(async () => finish("保存前的数据"));
    await waitFor(() => expect(result.current.data).toBe("保存后的数据"));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("does not expose the previous account when an explicit scope changes with the same loader", async () => {
    const load = vi.fn().mockResolvedValueOnce("账号 A")
      .mockImplementationOnce(() => new Promise(() => {}));
    const { result, rerender } = renderHook(({ scope }) => usePageResource(load, 0, false, scope),
      { initialProps: { scope: "account:A" } });
    await waitFor(() => expect(result.current.data).toBe("账号 A"));
    rerender({ scope: "account:B" });
    expect(result.current.data).toBeNull();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("shows the last visit immediately and still revalidates on return", async () => {
    let complete!: (value: string[]) => void;
    const load = vi.fn().mockResolvedValueOnce(["上次结果"])
      .mockImplementationOnce(() => new Promise<string[]>(resolve => { complete = resolve; }));
    const first = renderHook(() => usePageResource(load));
    await waitFor(() => expect(first.result.current.data).toEqual(["上次结果"]));
    first.unmount();
    const second = renderHook(() => usePageResource(load));
    expect(second.result.current.data).toEqual(["上次结果"]);
    expect(load).toHaveBeenCalledTimes(2);
    await act(async () => complete(["最新结果"]));
    expect(second.result.current.data).toEqual(["最新结果"]);
  });

  it("does not restore data after a write invalidates the previous visit", async () => {
    const load = vi.fn().mockResolvedValueOnce([1]).mockImplementationOnce(() => new Promise(() => {}));
    const first = renderHook(() => usePageResource(load));
    await waitFor(() => expect(first.result.current.data).toEqual([1]));
    first.unmount();
    invalidatePageSnapshots();
    const second = renderHook(() => usePageResource(load));
    expect(second.result.current.data).toBeNull();
  });

  it("keeps successful data after a refresh fails, then recovers", async () => {
    const load = vi.fn().mockResolvedValueOnce([1]).mockRejectedValueOnce(new Error("离线")).mockResolvedValue([2]);
    const { result } = renderHook(() => usePageResource(load));
    await waitFor(() => expect(result.current.data).toEqual([1]));
    await act(() => result.current.reload());
    expect(result.current.data).toEqual([1]); expect(result.current.error).toBe("离线");
    await act(() => result.current.reload());
    expect(result.current.data).toEqual([2]); expect(result.current.error).toBe("");
  });

  it("cancels the old scope and ignores its late response", async () => {
    let complete!: (x: string) => void;
    let oldSignal!: AbortSignal;
    const old = (signal: AbortSignal) => { oldSignal = signal; return new Promise<string>((resolve) => { complete = resolve; }); };
    const fresh = async (_signal: AbortSignal) => "新矩阵";
    const { result, rerender } = renderHook(({ load }) => usePageResource(load), { initialProps: { load: old } });
    rerender({ load: fresh });
    expect(oldSignal.aborted).toBe(true);
    await waitFor(() => expect(result.current.data).toBe("新矩阵"));
    await act(async () => complete("旧矩阵"));
    expect(result.current.data).toBe("新矩阵");
  });

  it("avoids concurrent loads and pauses hidden-page polling", async () => {
    vi.useFakeTimers();
    let complete!: (x: string) => void;
    const load = vi.fn(() => new Promise<string>((resolve) => { complete = resolve; }));
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    const { result } = renderHook(() => usePageResource(load, 1000));
    await act(async () => { await result.current.reload(); vi.advanceTimersByTime(2000); });
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => complete("完成"));
    hidden.mockReturnValue(true);
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(load).toHaveBeenCalledTimes(1);
  });
});
