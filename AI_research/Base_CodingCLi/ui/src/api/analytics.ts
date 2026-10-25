import { api } from "./client";

export type Metric = "view" | "like" | "coin" | "favorite" | "reply" | "danmaku" | "share";
export type Metrics = Record<Metric, number | null>;
export type VideoData = {
  bvid: string; title: string; pubdate: number; duration: number; tname: string;
  state: number | null; state_desc: string; missing: number; available: boolean;
  metrics: Metrics; stats_at: number; error: string;
  publication: { script_id: string; version: number; render_id: string; published_on: string;
    matrix?: string; matrix_name?: string; matrix_archived?: boolean; matrix_unavailable?: boolean;
    job?: string; job_name?: string; production_name?: string } | null;
};
export type MatrixData = {
  key: string; name: string; videos: number; public_videos: number;
  view_samples: number; view: number | null; median_view: number | null; stale_samples: number;
  favorite_rate: number | null; rate_samples: number; rate_views: number; rate_favorites: number;
};
export type AnalyticsData = {
  uid: string;
  collector: { enabled: number; status: string; page: number; remote_total: number; error: string;
    last_success_at: number; last_attempt_at: number; blocked_until: number; requested_at: number };
  schedule: { enabled: boolean; online: boolean; next_run_at: number | null };
  overview: { period?: number; summary: { view: number | null; like: number | null; favorite: number | null; fan: number | null };
    points: { date: string; value: number }[]; platform_date: string; synced_at: number } | null;
  overview_error: { error: string; at: number } | null;
  catalog_total: number; next_video_at: number | null;
  matrices?: MatrixData[];
  videos: { items: VideoData[]; total: number; page: number; size: number };
};
export type VideoDetail = {
  video: VideoData; days: number;
  history: { baseline_at: number | null; points: { at: number; metrics: Metrics }[]; delta: Metrics };
  comments: { pending: number; synced_at: number | null } | null;
};
const BASE = "/api/platforms/bilibili/analytics";
export const loadAnalytics = (query: Record<string, string | number>, signal?: AbortSignal) => api<AnalyticsData>(BASE, { query, signal });
export const loadVideoData = (bvid: string, days: number, signal?: AbortSignal) => api<VideoDetail>(`${BASE}/videos/${encodeURIComponent(bvid)}`, { query: { days }, signal });
export const syncVideoData = () => api(BASE + "/sync", { method: "POST" });
export const enableVideoData = (enabled: boolean) => api(BASE + "/settings", { method: "PUT", body: { enabled } });
