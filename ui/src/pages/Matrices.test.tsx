import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { ToastProvider } from "@/lib/toast";
import { MatricesPage, MatrixDetailPage, MatrixJobVideoPage } from "./Matrices";

const AI = {
  key: "ai", name: "AI 矩阵", domain: "AI 与软件开发", keywords: [], errors: [],
  jobs: [{ job: "monitoring/ai_flash_discovery", copy_as: "_flash_discovery" }, { job: "research/deep", copy_as: "" }],
  members: [
    { job: "monitoring/ai_flash_discovery", exists: true, name: "AI Flash Discovery", enabled: true, keywords: ["新模型发布"], copy_as: "_flash_discovery", copyable: true, topics: "flash",
      state: { job_name: "monitoring/ai_flash_discovery", last_run_at: "2026-10-20T08:00:00+08:00", last_status: "success", last_output: null, last_error: null, last_duration_seconds: 500 }, is_running: false, schedule: null },
    { job: "research/deep", exists: true, name: "AI 深度", enabled: true, keywords: [], copy_as: "", copyable: false, topics: "", state: null, is_running: false,
      schedule: { schedule: "0 8 * * 1", timezone: "Asia/Shanghai", enabled: false, next_runs: [] } },
  ],
};
const LIST = { matrices: [AI], ungrouped: [{ job: "research/games", name: "独立游戏" }] };

let calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method || "GET";
    calls.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const body = method === "POST" && u.endsWith("/copy") ? { ...AI, key: "medical", name: "医疗矩阵", members: [] }
      : method === "PUT" ? AI : u.includes("/api/production") || u.includes("/api/video/cells") ? []
      : u.endsWith("/api/matrices") ? LIST : AI;
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null });
  vi.unstubAllGlobals();
});

const at = (path: string) =>
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/matrices" element={<MatricesPage />} />
          <Route path="/matrices/:key" element={<MatrixDetailPage />} />
          <Route path="/matrices/:key/video/:job" element={<MatrixJobVideoPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );

describe("Matrices", () => {
  it("lists each matrix with its domain and Jobs, and the Jobs in none", async () => {
    at("/matrices");
    const card = await screen.findByRole("link", { name: "AI 矩阵" });
    expect(card.textContent).toContain("2 个 Job");
    expect(card.textContent).toContain("关键词写在各个 Job 里");
    expect(screen.getByText(/还有 1 个 Job 不在任何矩阵里：独立游戏/)).toBeTruthy();
  });

  it("shows each Job's schedule and last run, and adds a Job by sending the new member list", async () => {
    at("/matrices/ai");
    expect(await screen.findByRole("link", { name: "AI Flash Discovery" })).toBeTruthy();
    expect(screen.getByText("手动")).toBeTruthy();
    expect(screen.getByText("定时（已暂停） 0 8 * * 1")).toBeTruthy();
    expect(screen.getByText("还没跑过")).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("放进来的 Job"), { target: { value: "research/games" } });
    fireEvent.click(screen.getByText("放进来"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect((calls.find((c) => c.method === "PUT")!.body as { jobs: string[] }).jobs).toEqual([
      "monitoring/ai_flash_discovery", "research/deep", "research/games",
    ]);
  });

  it("gives every Job a way into its own video configs, and keeps them off the matrix page", async () => {
    at("/matrices/ai");
    const links = (await screen.findAllByRole("link", { name: "视频配置" })) as HTMLAnchorElement[];
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/matrices/ai/video/monitoring%2Fai_flash_discovery", "/matrices/ai/video/research%2Fdeep",
    ]);  // the Job whose report is not a list of items has one too
    expect(screen.getAllByText("还没配").length).toBe(2);  // the whole-report Job too: it can take a config now
    expect(screen.queryByLabelText("视频配置")).toBeNull();  // no section at the bottom any more
  });

  it("the Job's own page is that Job's configs: no Job to pick, and a whole-report Job can be given one", async () => {
    at("/matrices/ai/video/monitoring%2Fai_flash_discovery");
    expect(await screen.findByText("AI Flash Discovery · 视频配置")).toBeTruthy();
    expect(await screen.findByLabelText("视频配置")).toBeTruthy();
    expect(screen.queryByLabelText("加配置的 Job")).toBeNull();
    cleanup();
    at("/matrices/ai/video/research%2Fdeep");
    expect(await screen.findByText(/这个 Job 的报告是整份的，还没配视频/)).toBeTruthy();
  });

  it("lists only the first of a Job's many keywords, the rest on request", async () => {
    const many = Array.from({ length: 40 }, (_, i) => `词${i + 1}`);
    (AI.members as { keywords: string[] }[])[1].keywords = many;
    at("/matrices/ai");
    expect(await screen.findByText(/词1、词2/)).toBeTruthy();
    expect(screen.queryByText(/词7/)).toBeNull();
    fireEvent.click(screen.getByText("…共 40 个，展开"));
    expect(screen.getByText(/词40/)).toBeTruthy();
    fireEvent.click(screen.getByText("收起"));
    expect(screen.queryByText(/词7/)).toBeNull();
    (AI.members as { keywords: string[] }[])[1].keywords = [];
  });

  it("copies with new keywords only the Jobs that have a neutral template", async () => {
    at("/matrices/ai");
    fireEvent.click(await screen.findByText("换关键词复制", { selector: "button" }));
    const form = screen.getByRole("region", { name: "换关键词复制" });
    expect(form.textContent).toContain("暂不能复制（提示词里写死了「AI 与软件开发」）：AI 深度");
    expect(screen.getByLabelText("AI Flash Discovery")).toBeTruthy();
    expect(screen.queryByLabelText("AI 深度")).toBeNull();
    const copy = screen.getByText("复制", { selector: "button" }) as HTMLButtonElement;
    expect(copy.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("新矩阵的名字，如 医疗矩阵"), { target: { value: "医疗矩阵" } });
    fireEvent.change(screen.getByPlaceholderText("英文名，如 medical"), { target: { value: "medical" } });
    fireEvent.change(screen.getByPlaceholderText("领域，如 医疗健康"), { target: { value: "医疗健康" } });
    fireEvent.change(screen.getByPlaceholderText("关键词，用逗号分开"), { target: { value: "新药审批，医保政策、新药审批" } });
    fireEvent.click(copy);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/matrices/ai/copy"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/copy"))!.body).toEqual({
      key: "medical", name: "医疗矩阵", domain: "医疗健康", keywords: ["新药审批", "医保政策"], jobs: ["monitoring/ai_flash_discovery"],
    });
  });

  it("is read-only for a viewer", async () => {
    useAuthStore.setState({ user: { username: "v", role: "viewer" } as never });
    at("/matrices/ai");
    expect(await screen.findByRole("link", { name: "AI Flash Discovery" })).toBeTruthy();
    expect(screen.queryByText("换关键词复制")).toBeNull();
    expect(screen.queryByText("移出")).toBeNull();
    expect(screen.queryByText("删除这个矩阵")).toBeNull();
  });
});
