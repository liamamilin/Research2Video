import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { enableVideoData, loadAnalytics, loadVideoData, syncVideoData, type AnalyticsData, type VideoDetail } from "@/api/analytics";
import { VideoAnalytics } from "./VideoAnalytics";

vi.mock("@/api/analytics", () => ({ loadAnalytics: vi.fn(), loadVideoData: vi.fn(), enableVideoData: vi.fn(), syncVideoData: vi.fn() }));
const metrics = { view: 100, like: 2, coin: 0, favorite: 3, reply: 1, danmaku: null, share: null };
const video = { bvid: "BV1xx411c7mD", title: "科学视频", pubdate: 1792800000, duration: 90, tname: "科学科普", state: 0, state_desc: "", missing: 0, available: true, metrics, stats_at: 1792800000, error: "", publication: null };
let data: AnalyticsData;
let detail: VideoDetail;
beforeEach(() => {
  vi.clearAllMocks();
  data = { uid: "123", collector: { enabled: 1, status: "IDLE", page: 1, remote_total: 1, error: "", last_success_at: 1792800000, last_attempt_at: 1792800000, blocked_until: 0, requested_at: 0 },
    schedule: { enabled: true, online: true, next_run_at: 1792800300 }, overview: { summary: { view: 9, like: 0, favorite: null, fan: -6 }, points: [{ date: "2026-10-23", value: 9 }], platform_date: "2026-10-23", synced_at: 1792800000 },
    overview_error: null, catalog_total: 1, next_video_at: 1792886400, videos: { items: [video], total: 1, page: 1, size: 25 } };
  detail = { video, days: 30, history: { baseline_at: 1792800000, points: [{ at: 1792800000, metrics }], delta: { view: null, like: null, coin: null, favorite: null, reply: null, danmaku: null, share: null } }, comments: null };
  vi.mocked(loadAnalytics).mockImplementation(async () => data);
  vi.mocked(loadVideoData).mockImplementation(async () => detail);
  vi.mocked(enableVideoData).mockResolvedValue({});
  vi.mocked(syncVideoData).mockResolvedValue({});
});
afterEach(cleanup);
function show(admin = true, edit = true) { render(<MemoryRouter><VideoAnalytics isAdmin={admin} canEdit={edit} /></MemoryRouter>); }

it("shows actual account cutoff, zero and negative fans without making missing values zero", async () => {
  show();
  expect(await screen.findByText("-6")).toBeTruthy();
  expect(screen.getByText(/统计截至 2026-10-23/)).toBeTruthy();
  expect(screen.getByText("近7天点赞").nextElementSibling?.textContent).toBe("0");
  expect(screen.getByText("近7天收藏").nextElementSibling?.textContent).toBe("—");
  expect(screen.getByText(/独立调度器在线/)).toBeTruthy();
  expect(syncVideoData).not.toHaveBeenCalled();
});

it("loads search, workflow filter, sorting, paging, and 30-day overview from cache API", async () => {
  data.videos.total = 60;
  show();
  await screen.findByText("科学视频");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(loadAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }), expect.anything()));
  fireEvent.change(screen.getByLabelText("搜索视频数据"), { target: { value: "科学" } });
  await waitFor(() => expect(loadAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({ q: "科学", page: 1 }), expect.anything()));
  fireEvent.change(screen.getByLabelText("视频数据范围"), { target: { value: "workflow" } });
  fireEvent.change(screen.getByLabelText("视频数据排序"), { target: { value: "favorite_rate" } });
  fireEvent.change(screen.getByLabelText("账号统计范围"), { target: { value: "30" } });
  await waitFor(() => expect(loadAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({ period: 30, scope: "workflow", sort: "favorite_rate" }), expect.anything()));
  expect(syncVideoData).not.toHaveBeenCalled();
});

it("explains first baseline, provides comment route, and preserves negative net change", async () => {
  show();
  fireEvent.click(await screen.findByText("科学视频"));
  expect(await screen.findByText(/已建立基线，等待下一次采集/)).toBeTruthy();
  expect(screen.getByRole("link", { name: /前往评论收件箱/ }).getAttribute("href")).toBe("/platforms?tab=comments&bvid=BV1xx411c7mD");
  expect(screen.getByText(/已采集待处理观众评论：尚未采集/)).toBeTruthy();
  detail = { ...detail, history: { ...detail.history, points: [...detail.history.points, { at: 1792803600, metrics: { ...metrics, view: 90 } }], delta: { ...detail.history.delta, view: -10 } } };
  fireEvent.change(screen.getByLabelText("单视频趋势范围"), { target: { value: "7" } });
  expect(await screen.findByText("-10")).toBeTruthy();
  expect(loadVideoData).toHaveBeenLastCalledWith(video.bvid, 7, expect.anything());
});

it("queues manual collection and independently pauses automatic collection", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "立即同步数据" }));
  await waitFor(() => expect(syncVideoData).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "暂停定时采集" }));
  await waitFor(() => expect(enableVideoData).toHaveBeenCalledWith(false));
});

it("enforces read-only view, unpublished statistics, and reports stale data", async () => {
  data.videos.items = [{ ...video, available: false, state: -30, state_desc: "审核中" }];
  data.collector.error = "登录失效";
  show(false, false);
  expect(await screen.findByText(/登录失效/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "立即同步数据" })).toBeNull();
  expect(screen.queryByRole("button", { name: "暂停定时采集" })).toBeNull();
  expect(screen.getByText(/审核中/)).toBeTruthy();
  expect(screen.getAllByRole("row")[1].textContent).not.toContain("100");
});

it("disables duplicate or rate-limited manual requests", async () => {
  data.collector.status = "RUNNING";
  show();
  const button = await screen.findByRole("button", { name: "正在同步…" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  expect(syncVideoData).not.toHaveBeenCalled();
});

it("filters by matrix across pages and exposes an unlinked bucket without changing account totals", async () => {
  data.videos.total = 60;
  data.matrices = [
    { key: "matrix:science", name: "科学与知识百科", videos: 5, public_videos: 3, view_samples: 2, view: 1000, median_view: 500, stale_samples: 0, favorite_rate: .01, rate_samples: 2, rate_views: 1000, rate_favorites: 10 },
    { key: "unlinked", name: "未关联矩阵", videos: 2, public_videos: 0, view_samples: 0, view: null, median_view: null, stale_samples: 0, favorite_rate: null, rate_samples: 0, rate_views: 0, rate_favorites: 0 },
  ];
  show();
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  await waitFor(() => expect(loadAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }), expect.anything()));
  fireEvent.change(screen.getByLabelText("视频数据矩阵"), { target: { value: "matrix:science" } });
  await waitFor(() => expect(loadAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({ matrix: "matrix:science", page: 1 }), expect.anything()));
  expect(screen.getByText("近7天播放").nextElementSibling?.textContent).toBe("9");
  fireEvent.click(screen.getByText("按矩阵看表现"));
  fireEvent.click(screen.getByRole("button", { name: "未关联矩阵" }));
  await waitFor(() => expect(loadAnalytics).toHaveBeenLastCalledWith(expect.objectContaining({ matrix: "unlinked", page: 1 }), expect.anything()));
  expect(screen.getByText("1.00%")).toBeTruthy();
  expect(screen.getByText(/2条样本 · 10\/1,000/)).toBeTruthy();
  expect(screen.getByText(/发布后24小时\/7天的同龄比较/)).toBeTruthy();
  expect(syncVideoData).not.toHaveBeenCalled();
});

it("shows matrix, source job and production configuration on the published version", async () => {
  const publication = { script_id: "s1", version: 3, render_id: "r1", published_on: "2026-10-24", matrix: "science", matrix_name: "科学矩阵", job_name: "科学日报", production_name: "科学速览" };
  data.videos.items = [{ ...video, publication }];
  detail.video = { ...video, publication };
  show();
  fireEvent.click(await screen.findByText("科学视频"));
  expect(await screen.findByText(/矩阵：科学矩阵 · 来源任务：科学日报 · 生产配置：科学速览/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "查看文稿 v3 →" }).getAttribute("href")).toContain("publish_version=3");
});
