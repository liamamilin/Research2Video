import { api } from "./client";
import type { ScriptStatus } from "./video";

// 产出：每篇文稿属于哪个矩阵、哪个 Job、哪次运行的报告、哪套生产配置（spec/outputs.md）
export interface OutputScript {
  id: string;
  title: string;
  status: ScriptStatus;
  column_key: string;
  created_at: string;
  archived: boolean;
  auto: boolean;
  matrix: string;
  job: string;
  report_path: string;
  report_date: string;
  manual: boolean; // 手填的选题，不是从报告来的
  production: string;
  production_name: string;
  production_guessed: boolean; // 配置出现之前写的：按配方和 Job 认出来的
  render: { status: string; created_at: string } | null;
  published: boolean;
}

export interface OutputJob {
  job: string;
  name: string;
  reports: number;
  unwritten: number; // 导进来的报告里，还没有一篇文稿是从它写的
  matrix: string;
}

export interface Outputs {
  scripts: OutputScript[];
  jobs: OutputJob[];
  matrices: { key: string; name: string; archived?: boolean }[]; // 归档的矩阵：它的文稿只在「看已归档的」里
}

export const listOutputs = (archived = false) => api<Outputs>(`/api/outputs${archived ? "?archived=true" : ""}`);
