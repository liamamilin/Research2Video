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
  split: "each" | "some" | "";
  split_name: string;
  reverify: boolean;
  audience: string;
  target_minutes: number;
  label: string;
  scripts: number;
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

export const listProduction = (job?: string) =>
  api<ProductionConfig[]>("/api/production", { query: job ? { job } : undefined });
export const getPending = (key: string) =>
  api<{ report_date: string | null; report_path: string; items: PendingItem[] }>(`/api/production/${encodeURIComponent(key)}/pending`);
export const addProduction = (body: { job: string; recipe: string; audience?: string; minutes?: number; name?: string }) =>
  api<ProductionConfig>("/api/production", { method: "POST", body });
export const removeProduction = (key: string) => api<{ ok: boolean }>(`/api/production/${encodeURIComponent(key)}`, { method: "DELETE" });
export const writeFromProduction = (key: string, topicIds: string[]) =>
  api<({ id: string } | { failed: unknown })[]>(`/api/production/${encodeURIComponent(key)}/write`, { method: "POST", body: { topic_ids: topicIds } });
