import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Matrix, MatrixRun } from "@/api/matrices";
import { ToastProvider } from "@/lib/toast";
import { MatrixRuns } from "./MatrixRuns";

const matrix: Matrix = { key: "ai", name: "AI", domain: "", keywords: [], jobs: [], members: [], errors: [] };
const run = (name: string, status = "success"): MatrixRun => ({ job: name, name, status, ts: "2026-10-23T10:00:00+0800", trigger: "manual", user: "e", duration_seconds: 10, searches: 2, report_path: "", error: "", scripts: 0, items: 0 });
const response = (runs: MatrixRun[], more = false) => ({ ok: true, status: 200, text: async () => JSON.stringify({ runs, total: runs.length, more }) }) as Response;
const open = () => render(<ToastProvider><MemoryRouter><MatrixRuns matrix={matrix} /></MemoryRouter></ToastProvider>);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Matrix run pagination", () => {
  it("ignores an old page that arrives after switching the status filter", async () => {
    let finish!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).includes("offset=") ? new Promise<Response>((resolve) => { finish = resolve; })
      : String(url).includes("status=failed") ? response([run("失败的运行", "failed")]) : response([run("原来的运行")], true)));
    open();
    await screen.findByRole("link", { name: "原来的运行" });
    fireEvent.click(screen.getByRole("button", { name: "再加载 20 条" }));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    fireEvent.change(screen.getByLabelText("按状态筛"), { target: { value: "failed" } });
    await screen.findByRole("link", { name: "失败的运行" });
    await act(async () => { finish(response([run("迟到的旧页面")])); });
    expect(screen.queryByRole("link", { name: "迟到的旧页面" })).toBeNull();
    expect(screen.queryByRole("link", { name: "原来的运行" })).toBeNull();
  });

  it("retries failures and never duplicates overlapping history entries", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(response([run("已完成的运行")], true))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(response([run("已完成的运行"), run("下一条运行")], false));
    vi.stubGlobal("fetch", fetcher);
    open();
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("link", { name: "已完成的运行" });
    fireEvent.click(screen.getByRole("button", { name: "再加载 20 条" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("link", { name: "已完成的运行" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("link", { name: "下一条运行" });
    expect(screen.getAllByRole("link", { name: "已完成的运行" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "再加载 20 条" })).toBeNull();
  });
});
