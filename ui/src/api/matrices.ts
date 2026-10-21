import { api } from "./client";
import type { JobState } from "./types";

// 矩阵：一组 Job，共用一个领域和一套关键词（spec/matrices.md）
export interface MatrixMember {
  job: string;
  exists: boolean;
  name: string;
  description?: string;
  enabled?: boolean;
  keywords?: string[];
  copy_as: string; // 复制矩阵时用的模板；空的就是暂不能复制（提示词写死了原来的领域）
  copyable: boolean;
  topics?: string; // 它是哪个选题来源
  state?: JobState | null;
  is_running?: boolean;
  searches_per_run?: number; // 上次运行用了几次搜索（0 是不知道）
  latest_report?: { path: string; ts: string; scripts: number }; // 最近一次成功写出的报告，和从它写出了几篇文稿（只在矩阵详情里有）
  schedule: {
    id?: string; schedule: string; timezone: string; enabled: boolean; next_runs: string[];
    period?: Period; runs_per_month?: number;
  } | null;
}

/** 周期：手动 / 每天 / 工作日 / 每周几天 / 每月几号；custom 是周期调度页里写的 cron，这里只读 */
export type PeriodKind = "manual" | "daily" | "weekdays" | "weekly" | "monthly";
export interface Period {
  kind: PeriodKind | "custom";
  time?: string;
  days?: number[]; // 0 是周日
  day?: number;
}
export interface PeriodPreview {
  schedule: string;
  next_runs: string[];
  runs_per_month: number;
  searches_per_run: number;
  searches_per_month: number;
}
export type PeriodBody = { kind: PeriodKind; time: string; days: number[]; day: number };

export interface Matrix {
  key: string;
  name: string;
  domain: string;
  keywords: string[];
  jobs: { job: string; copy_as: string }[];
  errors: string[];
  members: MatrixMember[];
  paused?: string[]; // 整个暂停时停掉定时的 Job，恢复时打开的就是它们
  monthly_searches?: number; // 每月最多几次搜索，0 是不限
  searches_this_month?: number; // 本月已用（只在设了上限的矩阵详情里有）
}

export interface MatrixFields {
  name: string;
  domain: string;
  keywords: string[];
  jobs?: string[];
  monthly_searches?: number; // 不写是不动，0 是不限
}

export const listMatrices = () => api<{ matrices: Matrix[]; ungrouped: { job: string; name: string }[] }>("/api/matrices");
export const getMatrix = (key: string) => api<Matrix>(`/api/matrices/${encodeURIComponent(key)}`);
export const createMatrix = (body: MatrixFields & { key: string }) => api<Matrix>("/api/matrices", { method: "POST", body });
export const updateMatrix = (key: string, body: MatrixFields) =>
  api<Matrix>(`/api/matrices/${encodeURIComponent(key)}`, { method: "PUT", body });
export const deleteMatrix = (key: string) => api<{ ok: boolean }>(`/api/matrices/${encodeURIComponent(key)}`, { method: "DELETE" });
export const copyMatrix = (key: string, body: { key: string; name: string; domain: string; keywords: string[]; jobs: string[] }) =>
  api<Matrix>(`/api/matrices/${encodeURIComponent(key)}/copy`, { method: "POST", body });

const periodPath = (matrix: string, job: string) => `/api/matrices/${encodeURIComponent(matrix)}/jobs/${job.split("/").map(encodeURIComponent).join("/")}/period`;
export const previewPeriod = (matrix: string, job: string, p: PeriodBody) =>
  api<PeriodPreview>(periodPath(matrix, job), { query: { kind: p.kind, time: p.time, days: p.days.join(","), day: p.day } });
export const setPeriod = (matrix: string, job: string, body: PeriodBody & { confirm?: boolean }) =>
  api<Matrix>(periodPath(matrix, job), { method: "PUT", body });

export const pauseMatrix = (key: string) => api<Matrix>(`/api/matrices/${encodeURIComponent(key)}/pause`, { method: "POST" });
export const resumeMatrix = (key: string, confirm: boolean) =>
  api<Matrix>(`/api/matrices/${encodeURIComponent(key)}/resume`, { method: "POST", body: { confirm } });

export interface MatrixRun {
  ts: string;
  job: string;
  name: string;
  trigger: "scheduled" | "manual" | ""; // 定时或自动 / 有人点的 / 老记录没写
  user: string;
  status: string;
  duration_seconds: number | null;
  searches: number;
  report_path: string;
  error: string;
  scripts: number; // 从这份报告写出了几篇文稿
  items: number; // 这份报告有几条（条目列表才有，0 是整份的）
}
export interface RunsQuery { job?: string; status?: string; days?: number; order?: "asc" | "desc"; limit?: number; offset?: number }
export const listMatrixRuns = (key: string, q: RunsQuery = {}) =>
  api<{ runs: MatrixRun[]; total: number; more: boolean }>(`/api/matrices/${encodeURIComponent(key)}/runs`, { query: { ...q } });

export interface MatrixCalendar {
  now: string;
  jobs: Record<string, { name: string; enabled: boolean; schedule: string; period: Period; searches_per_run: number; runs: string[] }>;
  runs: { ts: string; job: string; name: string; searches: number }[];
  searches_by_day: Record<string, number>;
  clashes: { ts: string; jobs: string[] }[];
  concurrent_runs: number;
}
export const getMatrixCalendar = (key: string) => api<MatrixCalendar>(`/api/matrices/${encodeURIComponent(key)}/calendar`);

/** 关键词输入框：逗号、顿号或换行分开 */
export const splitKeywords = (text: string) => [...new Set(text.split(/[,，、\n]/).map((k) => k.trim()).filter(Boolean))];
