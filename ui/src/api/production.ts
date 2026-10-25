import { api } from "./client";

// 生产配置：一个配方挂在矩阵的一个 Job 上，从这个 Job 的报告写视频（spec/production.md）
export interface ProductionConfig {
  key: string;
  name: string;
  job: string;
  recipe: string; // 它写稿用的栏目名（配方，加上选的受众）
  base_recipe: string; // 配方文件
  audience_key: string; // 选的受众档案；配方没写 audiences 就是空的
  minutes: number; // 配置要的时长；0 是用配方自己的
  minutes_range: number[]; // 配方允许改成的时长 [最短, 最长]；空是不能改
  errors: string[];
  source: string;
  split: "each" | "some" | "whole" | ""; // 每条一期 / 挑几条合一期 / 整份报告一期
  split_name: string;
  reverify: boolean;
  auto: number; // Job 每出一份新报告，自动写几条（0 是不自动写）
  audience: string;
  target_minutes: number;
  label: string;
  scripts: number;
  render?: { production_recipe?: string; production_mode?: "standard" | "fast"; visual_style?: string; video_form?: string;
    voice_id?: string; video_speed?: number; creative_intent?: Record<string, unknown>; packaging?: Record<string, unknown> };
}

export interface PendingItem {
  id: string;
  code: string;
  title: string;
  fields: Record<string, string>;
  written: boolean;
  dismissed: boolean;
  scripts: { id: string; status: string }[];
}

export const listProduction = (job?: string, signal?: AbortSignal) =>
  api<ProductionConfig[]>("/api/production", { query: job ? { job } : undefined, signal });
/** 整份报告的配置：这个 Job 最近的报告，每份写过没有 */
export interface WholeReport {
  date: string;
  path: string;
  written: boolean;
  scripts: { id: string; status: string }[];
}

export const getPending = (key: string) =>
  api<{ report_date: string | null; report_path: string; items: PendingItem[]; reports?: WholeReport[] }>(`/api/production/${encodeURIComponent(key)}/pending`);
export const addProduction = (body: { job: string; recipe: string; audience?: string; minutes?: number; reverify?: boolean; auto?: number; name?: string; render?: ProductionConfig["render"] }) =>
  api<ProductionConfig>("/api/production", { method: "POST", body });
export const removeProduction = (key: string) => api<{ ok: boolean }>(`/api/production/${encodeURIComponent(key)}`, { method: "DELETE" });
export const writeFromProduction = (key: string, topicIds: string[]) =>
  api<({ id: string } | { failed: unknown })[]>(`/api/production/${encodeURIComponent(key)}/write`, { method: "POST", body: { topic_ids: topicIds } });
export const writeReportFromProduction = (key: string, reportPath: string) =>
  api<{ id: string }[]>(`/api/production/${encodeURIComponent(key)}/write`, { method: "POST", body: { report_path: reportPath } });
export const setAuto = (key: string, auto: number | null) =>
  api<ProductionConfig>(`/api/production/${encodeURIComponent(key)}`, { method: "PATCH", body: { auto } });
