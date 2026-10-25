import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePageResource } from "./use-page-resource";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("page resource lifecycle", () => {
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
