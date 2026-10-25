import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { TopicsPage } from "./Topics";

const TOPICS = {
  round: "2026-10-19",
  rounds: ["2026-10-19", "2026-10-18"],
  topics: [
    { id: "2026-10-19/F1", round: "2026-10-19", code: "F1", title: "Gemini 4 Argon 上线", fields: { 发布日期: "2026-10-14" }, score: 9, scripted: true, published: true, scripts: [{ id: "s9", column_key: "flash", status: "APPROVED", created_at: "2026-10-19T14:45:00+0800" }] },
    { id: "2026-10-19/F2", round: "2026-10-19", code: "F2", title: "GPT-5.5 从 ChatGPT 退役", fields: { 发布日期: "2026-10-10" }, score: null, scripted: false, published: false, stale: true },
    { id: "2026-10-19/F3", round: "2026-10-19", code: "F3", title: "不想写的一条", fields: {}, score: null, scripted: false, published: false, dismissed: true },
  ],
};
const CELLS = [
  { id: "rnd-cell", key: "rnd", label: "盘点", usable: true, status: "experimental", parts: { type: { key: "roundup", name: "盘点" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "roundup" } },
  { id: "cmp-cell", key: "cmp", label: "对比", usable: true, status: "experimental", parts: { type: { key: "comparison", name: "对比" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "comparison" } },
  { id: "whole-cell", key: "digest", label: "报告速览", usable: true, status: "experimental", parts: { type: { key: "digest", name: "报告速览" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "single" } },
  { id: "ai-developer-quick-take-long", key: "flash", label: "快讯", usable: true, status: "enabled", parts: { type: { key: "quick_take", name: "快讯" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "single" } },
];

let DISCOVERY = { running: false, report_date: "2026-10-19", report_at: 1792400000, searches: 20 };
const COVERAGE = [
  { cell: "flash", label: "快讯", status: "enabled", sources: ["flash"], open: 1, written: 1, dismissed: 0, multi: false, autopilot: true, report_date: "2026-10-19" },
  { cell: "rnd", label: "盘点", status: "experimental", sources: ["flash"], open: 2, written: 0, dismissed: 0, multi: true, autopilot: false, report_date: "2026-10-19" },
  { cell: "cmp", label: "对比", status: "experimental", sources: [], open: 0, written: 0, dismissed: 0, multi: false, autopilot: false, report_date: null },
];

const SOURCES = [
  { name: "flash", job: "monitoring/ai_flash_discovery", label: "AI Flash Discovery", format: "flash", prefix: "", refresh: true, error: "", reports: 2, latest: "2026-10-19", report_error: "" },
  { name: "manual", job: null, label: "手动添加", format: null, prefix: "M", refresh: false, error: "", reports: 0, latest: null, report_error: "", count: 0 },
];

function browserBody(cells = CELLS, coverage = COVERAGE) {
  return { cells, coverage, sources: SOURCES, initial: { source: "flash", ...TOPICS } };
}

beforeEach(() => {
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, status: 200, statusText: "ok",
    text: async () => JSON.stringify(String(url).includes("/topics/browser") ? browserBody() : String(url).includes("/topics/sources") ? SOURCES : String(url).includes("/discovery") ? DISCOVERY : String(url).includes("/coverage") ? COVERAGE : String(url).includes("/cells") ? CELLS : TOPICS),
  }) as Response));
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null });
  vi.unstubAllGlobals();
});

describe("Topics page", () => {
  it("gets the first topics with one browser request, without waiting for discovery status", async () => {
    const paths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      paths.push(String(url));
      if (String(url).includes("/discovery")) return new Promise<Response>(() => {});
      return { ok: true, status: 200, text: async () => JSON.stringify(browserBody()) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    expect(await screen.findByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
    expect(paths.filter((p) => !p.includes("/discovery"))).toEqual(["/api/video/topics/browser"]);
  });

  it("keeps the last list during a failed refresh and still lets the user change sources", async () => {
    let reads = 0;
    let failRefresh: (reason: Error) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/topics/browser") && ++reads > 1) return new Promise<Response>((_resolve, reject) => { failRefresh = reject; });
      const body = u.includes("/topics/browser") ? browserBody() : u.includes("/discovery") ? DISCOVERY
        : { round: null, rounds: [], topics: [{ ...TOPICS.topics[1], id: "manual/1", title: "手动选题新内容", source: "manual" }] };
      return { ok: true, status: 200, text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    await screen.findByText("GPT-5.5 从 ChatGPT 退役");
    fireEvent.click(screen.getByRole("button", { name: "刷新选题" }));
    expect(screen.getByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
    expect((screen.getByRole("button", { name: "刷新选题" }) as HTMLButtonElement).disabled).toBe(true);
    failRefresh(new Error("offline"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "手动添加" }));
    expect(await screen.findByText("手动选题新内容")).toBeTruthy();
  });

  it("reads fresh topics when returning to the default source and ignores an older source response", async () => {
    let finishManual: (value: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("source=manual")) return new Promise<Response>((resolve) => { finishManual = resolve; });
      const body = u.includes("/topics/browser") ? browserBody() : u.includes("/discovery") ? DISCOVERY
        : { ...TOPICS, topics: [{ ...TOPICS.topics[1], title: "默认来源的新报告条目" }] };
      return { ok: true, status: 200, text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    await screen.findByText("GPT-5.5 从 ChatGPT 退役");
    fireEvent.click(screen.getByRole("button", { name: "手动添加" }));
    await screen.findByText("正在读取选题…");
    fireEvent.click(screen.getByRole("button", { name: "AI Flash Discovery" }));
    expect(await screen.findByText("默认来源的新报告条目")).toBeTruthy();
    finishManual({ ok: true, status: 200, text: async () => JSON.stringify({ round: null, topics: [{ ...TOPICS.topics[1], title: "迟到的旧来源条目" }] }) } as Response);
    await waitFor(() => expect(screen.queryByText("迟到的旧来源条目")).toBeNull());
  });

  it("flags written and published topics and links the rest to the cell picker", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText("已发布")).toBeTruthy();
    const link = (await screen.findByText("用这个写稿")).closest("a");
    expect(link?.getAttribute("href")).toBe("/video?cell=ai-developer-quick-take-long&topic=2026-10-19%2FF2&topic_source=flash&topic_round=2026-10-19");
    expect(screen.getByText(/还有 1 条没写/)).toBeTruthy();
  });

  it("shows the event date next to the report date and which script already covers a topic, and filters", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText("事件日期 2026-10-14")).toBeTruthy();
    expect(screen.getByText("已有文稿").getAttribute("href")).toBe("/video/s9");
    fireEvent.change(screen.getByLabelText("写稿"), { target: { value: "no" } });
    await waitFor(() => expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull());
    expect(screen.getByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
  });

  it("has a tab per cell; a cell without a source says it is chosen by hand", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    fireEvent.click(await screen.findByRole("button", { name: "对比 开发者" }));
    expect(await screen.findByText(/还没有选题来源/)).toBeTruthy();
    expect(screen.queryByText("GPT-5.5 从 ChatGPT 退役")).toBeNull();
  });

  it("leaves out a recipe written from a whole report: it has no items to pick here", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    await screen.findByRole("button", { name: "快讯 开发者" });
    expect(screen.queryByRole("button", { name: "报告速览 开发者" })).toBeNull();
  });

  it("marks old news, hides what was marked not-writing, and disables discovery while it runs", async () => {
    DISCOVERY = { ...DISCOVERY, running: true };
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText("偏旧")).toBeTruthy();
    expect(screen.queryByText("不想写的一条")).toBeNull();
    const button = await screen.findByText("正在生成…");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    DISCOVERY = { ...DISCOVERY, running: false };
  });

  it("offers a roundup built from the ticked flash items", async () => {
    DISCOVERY = { ...DISCOVERY, running: false };
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    fireEvent.click(await screen.findByRole("button", { name: "盘点 开发者" }));
    const box = await screen.findByLabelText("选 F2");
    fireEvent.click(box);
    expect(screen.queryByText(/合成一期盘点（/)).toBeNull(); // one is not enough
    expect(screen.queryByText("用这个写稿")).toBeNull();
  });

  it("adds a topic by hand, which lands under the 手动添加 tab, and flags a source no job declares", async () => {
    const posts: { url: string; body: string }[] = [];
    const manual = { id: "manual/1", round: "", code: "M1", title: "为什么我不再用向量库", fields: {}, score: null, scripted: false, source: "manual" };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (init?.method === "POST") posts.push({ url: u, body: String(init.body) });
      const coverage = COVERAGE.map((c) => (c.cell === "flash" ? { ...c, unknown_sources: ["rss"] } : c));
      const body = u.includes("/topics/browser") ? browserBody(CELLS, coverage) : u.includes("/topics/sources") ? SOURCES : u.includes("/discovery") ? DISCOVERY : u.includes("/coverage") ? coverage
        : u.includes("/cells") ? CELLS : u.includes("source=manual") ? { round: null, rounds: [], topics: posts.length ? [manual] : [] }
        : init?.method === "POST" ? manual : TOPICS;
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText(/没有 Job 声明的选题来源：rss/)).toBeTruthy();
    expect(await screen.findByRole("button", { name: "AI Flash Discovery" })).toBeTruthy();
    fireEvent.click(screen.getByText("手动添加选题"));
    fireEvent.change(screen.getByPlaceholderText("标题（至少 4 个字）"), { target: { value: "为什么我不再用向量库" } });
    fireEvent.click(screen.getByText("添加"));
    await waitFor(() => expect(posts.some((p) => p.url.endsWith("/api/video/topics"))).toBe(true));
    expect(JSON.parse(posts[0].body).title).toBe("为什么我不再用向量库");
    expect(await screen.findByText("为什么我不再用向量库")).toBeTruthy(); // the 手动添加 tab is shown
    expect(screen.getByRole("button", { name: "手动添加" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("gives each domain its own grid, so a second domain's recipe for the same type and audience is not hidden", async () => {
    const ai = { domain: { key: "ai", name: "AI" } };
    const cells = [
      ...CELLS.map((c) => ({ ...c, parts: { ...c.parts, ...ai } })),
      { id: "med-developer-quick-take-long", key: "med_flash", label: "医疗快讯", usable: true, status: "enabled", parts: { domain: { key: "medical", name: "医疗" }, type: { key: "quick_take", name: "快讯" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "single" } },
    ];
    const coverage = [...COVERAGE, { cell: "med_flash", label: "医疗快讯", status: "enabled", sources: ["medical_flash"], open: 4, written: 0, dismissed: 0, multi: false, autopilot: false, report_date: "2026-10-19" }];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      const body = u.includes("/topics/browser") ? browserBody(cells, coverage) : u.includes("/topics/sources") ? SOURCES : u.includes("/discovery") ? DISCOVERY : u.includes("/coverage") ? coverage : u.includes("/cells") ? cells : TOPICS;
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByRole("button", { name: "医疗 快讯 开发者" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "AI 快讯 开发者" }).textContent).toContain("1 条待写");
    expect(screen.getByRole("button", { name: "医疗 快讯 开发者" }).textContent).toContain("4 条待写");
    expect(screen.getByLabelText("医疗内容矩阵")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "快讯 开发者" })).toBeNull(); // with one domain the label stays as it was
  });
});
