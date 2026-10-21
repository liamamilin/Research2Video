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
}

export interface MatrixFields {
  name: string;
  domain: string;
  keywords: string[];
  jobs?: string[];
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

/** 关键词输入框：逗号、顿号或换行分开 */
export const splitKeywords = (text: string) => [...new Set(text.split(/[,，、\n]/).map((k) => k.trim()).filter(Boolean))];
