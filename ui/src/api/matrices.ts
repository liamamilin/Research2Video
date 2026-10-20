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
  schedule: { schedule: string; timezone: string; enabled: boolean; next_runs: string[] } | null;
}

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

/** 关键词输入框：逗号、顿号或换行分开 */
export const splitKeywords = (text: string) => [...new Set(text.split(/[,，、\n]/).map((k) => k.trim()).filter(Boolean))];
