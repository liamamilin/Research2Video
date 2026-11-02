import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { loadPlatforms } from "@/api/platforms";
import { loadAssistantRequest, loadAssistantUsage, loadComparison, loadPerformance, loadScriptBills, loadScriptDetail, loadStorage, loadTelemetrySummary, loadWeeklyReview, purgeRecords, type Usage } from "@/api/telemetry";
import { useAuthStore } from "@/lib/auth-store";
import { AnalyticsPage } from "./Analytics";
import { PlatformsPage } from "./Platforms";

vi.mock("@/api/telemetry", () => ({ loadTelemetrySummary: vi.fn(), loadScriptBills: vi.fn(), loadScriptDetail: vi.fn(), loadComparison: vi.fn(), loadPerformance: vi.fn(), loadAssistantUsage: vi.fn(), loadAssistantRequest: vi.fn(), loadStorage: vi.fn(), purgeRecords: vi.fn(), loadWeeklyReview: vi.fn(), weeklyReportUrl: (start: number, stage: string) => `/api/telemetry/weekly-review/report?start=${start}&stage=${stage}` }));
vi.mock("@/api/platforms", async original => ({ ...await original<typeof import("@/api/platforms")>(), loadPlatforms: vi.fn() }));
vi.mock("@/components/VideoAnalytics", () => ({ VideoAnalytics: ({ isAdmin, canEdit }: { isAdmin: boolean; canEdit: boolean }) => <div>视频数据面板 {String(isAdmin)} {String(canEdit)}</div> }));

const usage = (over: Partial<Usage> = {}): Usage => ({ calls: 2, requests: 12, failed: 0, prompt_tokens: 1_500_000, completion_tokens: 320_000, reasoning_tokens: 0, cache_read_tokens: 0,
  total_tokens: 1_820_000, seconds: 90, cost_usd: 0.1234, unpriced_calls: 0, estimated_calls: 0, partial: false, bases: ["table"], ...over });
const stage = (name: string, over = {}) => ({ ...usage(), stage: name, span_count: 1, span_seconds: 120, ...over });

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
  vi.mocked(loadStorage).mockResolvedValue({ enabled: true, calls: 5460, spans: 900, oldest: 1_760_000_000, newest: 1_793_000_000, bytes: 3_500_000 });
  vi.mocked(purgeRecords).mockResolvedValue({ enabled: true, cutoff: 1_770_000_000, calls: 120, spans: 30, dry_run: true });
  vi.mocked(loadTelemetrySummary).mockResolvedValue({
    enabled: true, llm: usage({ requests: 640, failed: 3 }), search: usage({ requests: 21, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }),
    totals: usage(), models: [{ ...usage({ partial: true, unpriced_calls: 4, bases: ["table", "unpriced"] }), kind: "llm", provider: "api.deepseek.com", model: "deepseek-flash" }],
    stages: [stage("write"), stage("render/tts-and-align", { span_seconds: 454 })], unpriced_models: ["x mystery"],
  });
  vi.mocked(loadScriptBills).mockResolvedValue({ enabled: true, total: 1, items: [{ ...usage(), script_id: "s1", title: "第一期视频", column_key: "news", matrix: "", form: "information_motion", recipe: "", path: "adapt", last_ts: 1, produce_seconds: 600, render_seconds: 1200, wait_seconds: 7200 }] });
  vi.mocked(loadScriptDetail).mockResolvedValue({ enabled: true, script_id: "s1", totals: usage(), stages: [stage("audit")], models: [] });
  vi.mocked(loadComparison).mockResolvedValue({ enabled: true, by: "column_key", groups: [{ value: "news", scripts: 2, tokens_avg: 2_000_000, cost_avg_usd: 0.5, cost_samples: 2, produce_median_s: 600, render_median_s: 1200, wait_median_s: null }] });
  vi.mocked(loadPerformance).mockResolvedValue({ enabled: true, connected: true, by: "column_key",
    groups: [{ value: "news", videos: 2, watched: 2, views: 4000, view_median: 2000, tokens_per_kview: 12_000, cost_samples: 2, cost_partial: false, cost_avg_usd: 1.75, cost_per_kview: 0.875 }],
    items: [{ bvid: "BV1abc", title: "已发布的一期", pubdate: 1, view: 3000, script_id: "s1", billed: true, column_key: "news", form: "", matrix: "", recipe: "", path: "",
      tokens: 3_000_000, cost_usd: 3, partial: false, produce_seconds: 600, render_seconds: 1200, cost_per_kview: 1, tokens_per_kview: 1_000_000 }] });
  vi.mocked(loadAssistantUsage).mockResolvedValue({ enabled: true, request_total: 1,
    totals: { requests: 1, turns: 3, turns_per_request: 3, prompt_tokens: 60_000, completion_tokens: 900, cache_read_tokens: 30_000, cache_rate: 0.5, cost_usd: 0.06, partial: false,
      tool_schema_share: 0.25, tool_calls: 2, failed: 0, earlier_calls: 728, earlier_prompt_tokens: 26_774_000, earlier_completion_tokens: 410_000 },
    tools: [{ name: "query_jobs", calls: 2, requests: 1, avg_chars: 5000, max_chars: 8000, total_chars: 10_000 }],
    turns: [{ turn: 0, requests: 1, prompt_tokens_avg: 10_000, completion_tokens_avg: 100, message_chars_avg: 40_000, tool_schema_chars_avg: 20_000, tool_count_avg: 12, retired: 0 }],
    requests: [{ request_id: "r1", conversation_id: "c1", user: "admin", ts: 1_790_000_000, question: "帮我看看今天的任务", turns: 3, tools: ["query_jobs", "query_jobs"],
      prompt_tokens: 60_000, completion_tokens: 900, cache_read_tokens: 30_000, cost_usd: 0.06, partial: false, failed: 0, seconds: 42, peak_prompt_tokens: 30_000 }] });
  vi.mocked(loadAssistantRequest).mockResolvedValue({ enabled: true, request_id: "r1", turns: [
    { turn: 0, message_chars: 40_000, tool_schema_chars: 20_000, tool_count: 12, retired: 0, saved_chars: 0, tools: [{ name: "query_jobs", chars: 5000 }], prompt_tokens: 10_000,
      completion_tokens: 100, cache_read_tokens: 0, cost_usd: 0.01, partial: false, seconds: 3, failed: 0 },
    { turn: 1, message_chars: 45_000, tool_schema_chars: 20_000, tool_count: 12, retired: 1, saved_chars: 800, tools: [], prompt_tokens: 20_000,
      completion_tokens: 200, cache_read_tokens: 10_000, cost_usd: 0.02, partial: false, seconds: 4, failed: 0 }] });
  vi.mocked(loadPlatforms).mockResolvedValue({ account: { connected: true, uid: "9", name: "up" } } as never);
});
afterEach(cleanup);

const show = (url = "/analytics") => render(<MemoryRouter initialEntries={[url]}><Routes><Route path="/analytics" element={<AnalyticsPage />} /><Route path="/platforms" element={<PlatformsPage />} /></Routes></MemoryRouter>);

it("shows tokens, requests and time per part, with every figure in readable units", async () => {
  show();
  expect(await screen.findByText("640")).toBeTruthy();
  expect(screen.getAllByText("150.0 万").length).toBeGreaterThan(0);
  expect(await screen.findByText("第一期视频")).toBeTruthy();
  expect(screen.getAllByText("10 分钟").length).toBeGreaterThan(0);
  expect(screen.getAllByText("2.0 小时")).toHaveLength(1);
  expect(screen.getAllByText("写稿").length).toBeGreaterThan(0);
  expect(screen.getByText("出片 · 配音与对齐")).toBeTruthy();
  expect(screen.getByText(/未定价：x mystery/)).toBeTruthy();
});

it("flags amounts that leave out unpriced calls and can hide all money, remembering the choice", async () => {
  show();
  expect((await screen.findAllByText("$0.1234")).length).toBeGreaterThan(0);
  expect(screen.getByText("$0.1234+")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("显示折算金额"));
  expect(screen.queryAllByText("$0.1234")).toHaveLength(0);
  expect(screen.queryAllByText("$0.1234+")).toHaveLength(0);
  expect(screen.queryAllByText("折算")).toHaveLength(0);
  expect(localStorage.getItem("analytics.showCost")).toBe("0");
});

it("opens one video's stages on demand", async () => {
  show();
  fireEvent.click(await screen.findByText("第一期视频"));
  await waitFor(() => expect(loadScriptDetail).toHaveBeenCalledWith("s1", expect.anything()));
  expect(await screen.findByText(/分镜规划与配音同时进行/)).toBeTruthy();
});

it("compares videos by the chosen attribute and reloads", async () => {
  show();
  await screen.findByRole("link", { name: "已发布的一期" });
  expect(screen.getAllByText("news").length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("对比维度"), { target: { value: "form" } });
  await waitFor(() => expect(loadComparison).toHaveBeenLastCalledWith("form", 30, expect.anything()));
});

it("sets cost against plays per group and per published video", async () => {
  show();
  const link = await screen.findByRole("link", { name: "已发布的一期" });
  expect(link.getAttribute("href")).toContain("BV1abc");
  expect(screen.getAllByText("$0.8750").length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("成本对照维度"), { target: { value: "recipe" } });
  await waitFor(() => expect(loadPerformance).toHaveBeenLastCalledWith("recipe", 30, expect.anything()));
});

it("asks for a Bilibili account when there are no plays to compare with", async () => {
  vi.mocked(loadPerformance).mockResolvedValue({ enabled: true, connected: false, by: "column_key", items: [], groups: [] });
  show();
  expect(await screen.findByText(/先在平台管理里连接哔哩哔哩账号并开启视频数据采集/)).toBeTruthy();
});

it("breaks the assistant down by turn, tool and request, and opens one request turn by turn", async () => {
  show("/analytics?tab=assistant");
  expect(await screen.findByText("帮我看看今天的任务")).toBeTruthy();
  expect(screen.getByText(/728 次更早的助手调用/)).toBeTruthy();
  expect(screen.getAllByText("query_jobs").length).toBeGreaterThan(0);
  expect(screen.getByText("缓存命中 50.0%")).toBeTruthy();
  fireEvent.click(screen.getByText("帮我看看今天的任务"));
  await waitFor(() => expect(loadAssistantRequest).toHaveBeenCalledWith("r1", expect.anything()));
  expect(await screen.findByText("直接回答")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("助手统计范围"), { target: { value: "7" } });
  await waitFor(() => expect(loadAssistantUsage).toHaveBeenLastCalledWith(7, 0, expect.anything()));
});

it("changes the range for every section", async () => {
  show();
  await screen.findByText("第一期视频");
  fireEvent.change(screen.getByLabelText("统计范围"), { target: { value: "0" } });
  await waitFor(() => expect(loadTelemetrySummary).toHaveBeenLastCalledWith(0, expect.anything()));
  expect(loadScriptBills).toHaveBeenLastCalledWith(0, 0, expect.anything());
});

it("hosts the video data tab with full rights, since only administrators reach the page", async () => {
  show("/analytics?tab=video");
  expect(await screen.findByText("视频数据面板 true true")).toBeTruthy();
});

it("is closed to anyone but administrators and asks the server for nothing", () => {
  for (const role of ["viewer", "editor"]) {
    useAuthStore.setState({ user: { username: role, role } as never });
    show();
    expect(screen.getByRole("alert").textContent).toContain("只对管理员开放");
    cleanup();
  }
  expect(loadTelemetrySummary).not.toHaveBeenCalled();
  expect(loadPlatforms).not.toHaveBeenCalled();
});

it("shows what is stored and previews a cleanup before deleting anything", async () => {
  show();
  expect(await screen.findByText(/共 5,460 条调用记录、900 条环节记录/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("保留时长"), { target: { value: "90" } });
  fireEvent.click(screen.getByText("查看会清理多少"));
  expect(await screen.findByText(/将删除 120 条调用记录、30 条环节记录/)).toBeTruthy();
  expect(purgeRecords).toHaveBeenCalledWith(90, true);
  vi.mocked(purgeRecords).mockResolvedValue({ enabled: true, cutoff: 1_770_000_000, calls: 120, spans: 30, dry_run: false });
  fireEvent.click(screen.getByText("确认删除"));
  expect(await screen.findByText("已删除 120 条调用记录、30 条环节记录")).toBeTruthy();
  expect(purgeRecords).toHaveBeenLastCalledWith(90, false);
  await waitFor(() => expect(loadStorage).toHaveBeenCalledTimes(2));
});

it("offers no delete button when nothing is old enough, and forgets a preview when the period changes", async () => {
  vi.mocked(purgeRecords).mockResolvedValue({ enabled: true, cutoff: 1, calls: 0, spans: 0, dry_run: true });
  show();
  fireEvent.click(await screen.findByText("查看会清理多少"));
  expect(await screen.findByText(/将删除 0 条调用记录/)).toBeTruthy();
  expect(screen.queryByText("确认删除")).toBeNull();
  fireEvent.change(screen.getByLabelText("保留时长"), { target: { value: "30" } });
  expect(screen.queryByText(/将删除/)).toBeNull();
});

it("sends someone without a connected account to connect one instead of an empty panel", async () => {
  vi.mocked(loadPlatforms).mockResolvedValue({ account: { connected: false } } as never);
  show("/analytics?tab=video");
  expect(await screen.findByText(/先在平台管理里连接/)).toBeTruthy();
  expect(screen.queryByText(/视频数据面板/)).toBeNull();
});

it("sends the old platform-management data link to its new home", async () => {
  show("/platforms?tab=data");
  expect(await screen.findByText("视频数据面板 true true")).toBeTruthy();
  expect(screen.getByRole("tab", { name: /视频数据/ }).getAttribute("aria-selected")).toBe("true");
});

it("opens the weekly review from the URL, picks another week and keeps gaps apart from plays", async () => {
  const video = { bvid: "BV1x", title: "ChatGPT 和 Claude 怎么选", pubdate: 1_793_000_000, duration: 300, view: 900, h24: 800, h72: null, h168: null, state: "ok" as const,
    form: "单篇", matrix: "ChatGPT 获客矩阵", column: "T05 · AI 产品横向选择", structure: "A 价格反差与分任务对决" };
  const review = { connected: true as const, weeks: [1_793_548_800, 1_792_944_000], saved: [], stage: "" as const, start: 1_792_944_000, end: 1_793_548_800, now: 1_793_600_000, min_n: 3, ratio: 2,
    summary: { videos: 2, ok: 1, pending: 0, gap: 1, median_24h: 800, previous_median_24h: 5, previous_videos: 4, views: 1000, top_share: 0.9 },
    dimensions: [{ key: "matrix", label: "矩阵", groups: [{ value: "ChatGPT 获客矩阵", videos: 2, measured: 1, median: 800, max: 800, best_title: video.title, previous_median: null, previous_measured: 0, flag: "" as const }] }],
    top: [video], findings: ["1 条视频在发布后 24 小时附近没有采集快照（采集当时停了），不计入中位数。"], suggestions: [],
    videos: [video, { ...video, bvid: "BV2y", title: "采集停掉那天的一条", h24: null, state: "gap" as const }] };
  vi.mocked(loadWeeklyReview).mockResolvedValue(review);
  show("/analytics?tab=review");
  expect(await screen.findByText("本周结论")).toBeTruthy();
  expect(screen.getByText(/没有采集快照/)).toBeTruthy();
  expect(screen.getByText("采集中断")).toBeTruthy();
  expect(screen.getAllByText("A 价格反差与分任务对决").length).toBe(2);
  expect(screen.getByRole("tab", { name: /每周复盘/ }).getAttribute("aria-selected")).toBe("true");
  fireEvent.change(screen.getByLabelText("复盘的周"), { target: { value: "1793548800" } });
  await waitFor(() => expect(vi.mocked(loadWeeklyReview).mock.calls.at(-1)?.slice(0, 2)).toEqual([1_793_548_800, ""]));
});

it("shows completion, watch time and early exits once the creator centre has been read", async () => {
  const video = { bvid: "BV1x", title: "Kimi 3D Make", pubdate: 1_793_000_000, duration: 181, view: 15, h24: 15, h72: null, h168: null, state: "ok" as const,
    form: "单篇", matrix: "AI", column: "快讯", structure: "", completion: 66.57, avg_seconds: 120, exit3s: 16.66, cover_pass: null, watched_share: 66.3 };
  const watch = { watch_measured: 1, completion: 66.57, avg_seconds: 120, exit3s: 16.66, cover_pass: 77.49 };
  vi.mocked(loadWeeklyReview).mockResolvedValue({ connected: true, weeks: [1_792_944_000], saved: [], stage: "", start: 1_792_944_000, end: 1_793_548_800, now: 1_793_600_000, min_n: 3, ratio: 2,
    summary: { videos: 1, ok: 1, pending: 0, gap: 0, median_24h: 15, previous_median_24h: null, previous_videos: 0, views: 15, top_share: 1, watch },
    dimensions: [{ key: "duration", label: "时长", groups: [{ value: "2–4 分钟", videos: 1, measured: 1, median: 15, max: 15, best_title: video.title, previous_median: null, previous_measured: 0, flag: "", ...watch }] }],
    top: [video], findings: [], suggestions: [], videos: [video] });
  show("/analytics?tab=review");
  expect(await screen.findByText("完播率中位数")).toBeTruthy();
  expect(screen.getByText("77.5%")).toBeTruthy();
  expect(screen.getAllByText("66.6%").length).toBe(3);
  expect(screen.getByTitle("看了全片的 66.3%").textContent).toBe("120 秒");
  expect(screen.queryByText(/这一周还没有读到/)).toBeNull();
});

it("opens a saved week from a notice link and offers its report", async () => {
  vi.mocked(loadWeeklyReview).mockImplementation(async (_start, stage) => stage
    ? { connected: true, weeks: [1_792_944_000], saved: [{ start: 1_792_944_000, stage: "24h", saved_at: 1_793_640_000, findings: 2, suggestions: 1 }], stage: "24h", saved_at: 1_793_640_000,
      start: 1_792_944_000, end: 1_793_548_800, now: 1_793_640_000, min_n: 3, ratio: 2,
      summary: { videos: 0, ok: 0, pending: 0, gap: 0, median_24h: null, previous_median_24h: null, previous_videos: 0, views: 0, top_share: null },
      dimensions: [], top: [], findings: ["存下时的结论"], suggestions: ["存下时的建议"], videos: [] }
    : { connected: true, missing: true, start: 1_792_944_000, weeks: [1_792_944_000], saved: [] });
  show("/analytics?tab=review&start=1792944000&stage=24h");
  expect(await screen.findByText("存下时的结论")).toBeTruthy();
  expect(vi.mocked(loadWeeklyReview).mock.calls[0].slice(0, 2)).toEqual([1_792_944_000, "24h"]);
  expect(screen.getByRole("link", { name: /下载报告/ }).getAttribute("href")).toContain("/weekly-review/report?start=1792944000&stage=24h");
  fireEvent.change(screen.getByLabelText("复盘的版本"), { target: { value: "" } });
  expect(await screen.findByText("这一周的这个版本还没有存档")).toBeTruthy();
});
