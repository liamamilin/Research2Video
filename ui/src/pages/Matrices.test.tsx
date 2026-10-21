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
      state: { job_name: "monitoring/ai_flash_discovery", last_run_at: "2026-10-20T08:00:00+08:00", last_status: "success", last_output: null, last_error: null, last_duration_seconds: 500 }, is_running: false, searches_per_run: 20, schedule: null },
    { job: "research/deep", exists: true, name: "AI 深度", enabled: true, keywords: [], copy_as: "", copyable: false, topics: "", state: null, is_running: false,
      schedule: { schedule: "0 8 * * 1", timezone: "Asia/Shanghai", enabled: false, next_runs: [] } },
  ],
};
const LIST = { matrices: [AI], ungrouped: [{ job: "research/games", name: "独立游戏" }] };

const RUNS = {
  total: 3, more: true,
  runs: [
    { ts: "2026-10-20T10:00:00+08:00", job: "monitoring/ai_flash_discovery", name: "AI Flash Discovery", trigger: "manual", user: "admin", status: "success", duration_seconds: 188, searches: 18, report_path: "output/flash/c.md", error: "", scripts: 0, items: 10 },
    { ts: "2026-10-19T14:12:00+08:00", job: "monitoring/ai_flash_discovery", name: "AI Flash Discovery", trigger: "", user: "", status: "failed", duration_seconds: null, searches: 0, report_path: "", error: "超时了", scripts: 0, items: 0 },
    { ts: "2026-09-29T16:00:00+08:00", job: "research/deep", name: "AI 深度", trigger: "scheduled", user: "scheduler", status: "success", duration_seconds: 600, searches: 30, report_path: "output/d/x.md", error: "", scripts: 2, items: 0 },
  ],
};
const CALENDAR = {
  now: "2026-10-21T12:00+08:00", concurrent_runs: 2, searches_by_day: { "2026-10-27": 20 },
  jobs: { "monitoring/ai_flash_discovery": { name: "AI Flash Discovery", enabled: true, schedule: "0 5 * * 2", period: { kind: "weekly", time: "05:00", days: [2], day: 1 }, searches_per_run: 20, runs: ["2026-10-27T05:00+08:00"] } },
  runs: [{ ts: "2026-10-27T05:00+08:00", job: "monitoring/ai_flash_discovery", name: "AI Flash Discovery", searches: 20 }],
  clashes: [{ ts: "2026-10-27T05:00+08:00", jobs: ["A", "B", "C"] }],
};
let PRODUCTION: unknown[] = [];
let calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  PRODUCTION = [];
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method || "GET";
    calls.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const body = u.includes("/runs") ? RUNS : u.includes("/calendar") ? CALENDAR
      : u.includes("/period") && method === "GET"
      ? { schedule: "0 5 * * 2", next_runs: ["2026-10-27T05:00:00+08:00"], runs_per_month: 4, searches_per_run: 20, searches_per_month: 80 }
      : method === "POST" && u.includes("/api/jobs/") ? { ok: true, task_id: "t1", started_at: "x" }
      : method === "POST" && u.endsWith("/copy") ? { ...AI, key: "medical", name: "医疗矩阵", members: [] }
      : method === "PUT" ? AI : u.includes("/api/production") ? PRODUCTION : u.includes("/api/video/cells") ? []
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

  it("runs a Job once after asking, and says when its report is ready to be written", async () => {
    PRODUCTION = [{ job: "monitoring/ai_flash_discovery", auto: 3 }, { job: "monitoring/ai_flash_discovery", auto: 0 }];
    (AI.members[0] as { latest_report?: unknown }).latest_report = { path: "output/flash/c.md", ts: "x", scripts: 0 };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    at("/matrices/ai");
    expect(await screen.findByText("最新报告还没写，会自动写")).toBeTruthy(); // one config writes by itself
    expect(screen.getByText("2 套，1 套自动写")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "运行 AI Flash Discovery 一次" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/jobs/monitoring%2Fai_flash_discovery/run"))).toBe(true));
    expect(confirm.mock.calls[0][0]).toContain("20 次搜索");
    confirm.mockRestore();
    delete (AI.members[0] as { latest_report?: unknown }).latest_report;
  });

  it("does not run when the question is declined", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    at("/matrices/ai");
    fireEvent.click(await screen.findByRole("button", { name: "运行 AI Flash Discovery 一次" }));
    expect(calls.some((c) => c.url.includes("/run"))).toBe(false);
    confirm.mockRestore();
  });

  it("lets an admin change a Job's period with a preview, and an editor cannot", async () => {
    at("/matrices/ai");
    await screen.findByRole("link", { name: "AI Flash Discovery" });
    expect(screen.queryByRole("button", { name: "改 AI Flash Discovery 的周期" })).toBeNull(); // an editor
    cleanup();
    useAuthStore.setState({ user: { username: "a", role: "admin" } as never });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    at("/matrices/ai");
    fireEvent.click(await screen.findByRole("button", { name: "改 AI Flash Discovery 的周期" }));
    fireEvent.change(screen.getByLabelText("周期"), { target: { value: "weekly" } });
    expect(await screen.findByText(/每月约 4 次运行，上次每次用了 20 次搜索，约 80 次/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.includes("/jobs/monitoring/ai_flash_discovery/period"))).toBe(true));
    const put = calls.find((c) => c.method === "PUT" && c.url.includes("/period"))!;
    expect(put.body).toMatchObject({ kind: "weekly", time: "05:00", days: [1], confirm: true });
    confirm.mockRestore();
  });

  it("lists the runs of every Job with what came of them, filters, and offers the next step for a report with no video", async () => {
    at("/matrices/ai?tab=runs");
    expect(await screen.findByText("运行记录（3）")).toBeTruthy();
    expect(screen.getByText("18 次搜索")).toBeTruthy();
    expect(screen.getByText("超时了")).toBeTruthy();
    expect(screen.getByText("写了 2 篇文稿")).toBeTruthy();
    expect(screen.getByText("10 条")).toBeTruthy();
    expect(screen.getAllByText("看报告")).toHaveLength(2); // the failed run has no report to look at
    expect(screen.getByText("2026 年 10 月")).toBeTruthy();
    expect(screen.getByRole("link", { name: "还没写视频 →" }).getAttribute("href")).toBe("/matrices/ai/video/monitoring%2Fai_flash_discovery");
    fireEvent.change(screen.getByLabelText("按状态筛"), { target: { value: "failed" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("/runs") && c.url.includes("status=failed"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "最新在前" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("order=asc"))).toBe(true));
    fireEvent.click(await screen.findByRole("button", { name: "再加载 20 条" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("offset=3"))).toBe(true));
  });

  it("shows the next week of timed runs, with a warning when too many are due together", async () => {
    at("/matrices/ai?tab=week");
    expect(await screen.findByText("接下来 7 天")).toBeTruthy();
    expect(screen.getByText(/同时到点 3 个：A、B、C/)).toBeTruthy();
    expect(screen.getAllByText("05:00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("20 次").length).toBeGreaterThan(0);
    expect(screen.getByText(/按时间排/)).toBeTruthy();
  });

  it("switches between the Job table and the tabs", async () => {
    at("/matrices/ai");
    fireEvent.click(await screen.findByRole("tab", { name: "运行记录" }));
    expect(await screen.findByLabelText("按 Job 筛")).toBeTruthy();
    expect(screen.queryByLabelText("矩阵里的 Job")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Job" }));
    expect(await screen.findByLabelText("矩阵里的 Job")).toBeTruthy();
  });

  it("pauses and resumes a whole matrix: the resume says what it costs and only a yes confirms it", async () => {
    useAuthStore.setState({ user: { username: "a", role: "admin" } as never });
    const paused = { ...AI, paused: ["research/deep"] };
    const live = { ...AI, members: [{ ...AI.members[0], schedule: { schedule: "0 5 * * 2", timezone: "x", enabled: true, next_runs: [] } }, AI.members[1]] };
    const sent: { url: string; body: unknown }[] = [];
    let state: unknown = live;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (init?.method === "POST") sent.push({ url: u, body });
      if (u.endsWith("/pause")) { state = paused; return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(paused) } as Response; }
      if (u.endsWith("/resume")) {
        if (!(body as { confirm: boolean }).confirm) {
          return { ok: false, status: 409, statusText: "Conflict", text: async () => JSON.stringify({ error: { code: "confirm_needed", message: "恢复后 1 个 Job 会自己运行：每月约 4 次运行" } }) } as Response;
        }
        state = AI;
        return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(AI) } as Response;
      }
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(u.includes("/api/production") || u.includes("/cells") ? [] : u.endsWith("/api/matrices") ? LIST : state) } as Response;
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    at("/matrices/ai");
    fireEvent.click(await screen.findByRole("button", { name: "整个暂停" }));
    expect(await screen.findByText(/矩阵已暂停：1 个 Job 的定时没在跑/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "恢复定时" }));
    await waitFor(() => expect(sent.filter((c) => c.url.endsWith("/resume")).map((c) => (c.body as { confirm: boolean }).confirm)).toEqual([false, true]));
    expect(String(confirm.mock.calls.at(-1)?.[0])).toContain("每月约 4 次运行");
    await waitFor(() => expect(screen.queryByText(/矩阵已暂停/)).toBeNull());
    confirm.mockRestore();
  });

  it("says whether the newest report still needs a video: a link when nothing writes it, a count once written", async () => {
    const flash = AI.members[0] as { latest_report?: unknown };
    try {
      flash.latest_report = { path: "output/flash/c.md", ts: "2026-10-20T10:00:00+08:00", scripts: 0 };
      PRODUCTION = [{ job: "monitoring/ai_flash_discovery", auto: 0 }];
      at("/matrices/ai");
      expect((await screen.findByRole("link", { name: "最新报告还没写视频 →" })).getAttribute("href")).toBe("/matrices/ai/video/monitoring%2Fai_flash_discovery");
      cleanup();
      flash.latest_report = { path: "output/flash/c.md", ts: "x", scripts: 3 };
      at("/matrices/ai");
      expect(await screen.findByText("最新报告已写 3 篇")).toBeTruthy();
      expect(screen.queryByRole("link", { name: /还没写视频/ })).toBeNull();
      cleanup();
      PRODUCTION = []; // no config on the Job: nothing to say about writing
      at("/matrices/ai");
      await screen.findByRole("link", { name: "AI Flash Discovery" });
      expect(screen.queryByText(/最新报告/)).toBeNull();
    } finally {
      delete flash.latest_report;
    }
  });

  it("starts a run that did not finish again from the beginning, after asking", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    at("/matrices/ai?tab=runs");
    fireEvent.click(await screen.findByRole("button", { name: "重新运行" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/jobs/monitoring%2Fai_flash_discovery/run"))).toBe(true));
    expect(String(confirm.mock.calls[0][0])).toContain("从头再跑");
    confirm.mockRestore();
  });

  it("shows the month's search limit against what is used, and lets an admin set it", async () => {
    useAuthStore.setState({ user: { username: "a", role: "admin" } as never });
    at("/matrices/ai");
    expect(await screen.findByText(/没设（Job 想跑多少跑多少）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "设上限" }));
    fireEvent.change(screen.getByLabelText("每月搜索上限"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && (c.body as { monthly_searches?: number })?.monthly_searches === 500)).toBe(true));
  });

  it("warns when the month's searches are used up, and an editor cannot change the limit", async () => {
    const limited = { ...AI, monthly_searches: 100, searches_this_month: 100 };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, status: 200, statusText: "ok",
      text: async () => JSON.stringify(String(url).includes("/api/production") || String(url).includes("/cells") ? [] : String(url).endsWith("/api/matrices") ? LIST : limited) }) as Response));
    at("/matrices/ai");
    expect(await screen.findByText(/100 次，本月已用 100 次，这个矩阵的 Job 本月不会再启动/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "改上限" })).toBeNull(); // an editor
  });

  it("opens the runs already filtered to the Job it was linked for", async () => {
    at("/matrices/ai?tab=runs&job=monitoring%2Fai_flash_discovery");
    await screen.findByText("运行记录（3）");
    expect(calls.some((c) => c.url.includes("/runs") && c.url.includes("job=monitoring%2Fai_flash_discovery"))).toBe(true);
    expect((screen.getByLabelText("按 Job 筛") as HTMLSelectElement).value).toBe("monitoring/ai_flash_discovery");
  });

  it("lists a config whose Job is in no matrix on the list page, and removes it after asking", async () => {
    PRODUCTION = [{ key: "orphan", name: "孤儿配置", job: "research/gone", errors: ["Job 不在了"] }, { key: "fine", name: "好的", job: "research/deep", errors: [] }];
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    at("/matrices");
    expect(await screen.findByText("1 套视频配置挂的 Job 不在任何矩阵里")).toBeTruthy();
    expect(screen.getByText("research/gone")).toBeTruthy();
    expect(screen.queryByText("好的")).toBeNull(); // its Job is in a matrix
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/production/orphan"))).toBe(true));
    await waitFor(() => expect(screen.queryByText("research/gone")).toBeNull());
    confirm.mockRestore();
  });
});
