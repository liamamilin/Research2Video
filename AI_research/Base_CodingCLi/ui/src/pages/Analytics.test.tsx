import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { loadPlatforms } from "@/api/platforms";
import { loadComparison, loadPerformance, loadScriptBills, loadScriptDetail, loadStorage, loadTelemetrySummary, purgeRecords, type Usage } from "@/api/telemetry";
import { useAuthStore } from "@/lib/auth-store";
import { AnalyticsPage } from "./Analytics";
import { PlatformsPage } from "./Platforms";

vi.mock("@/api/telemetry", () => ({ loadTelemetrySummary: vi.fn(), loadScriptBills: vi.fn(), loadScriptDetail: vi.fn(), loadComparison: vi.fn(), loadPerformance: vi.fn(), loadStorage: vi.fn(), purgeRecords: vi.fn() }));
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
