import { api } from "./client";

// 信息源注册表（config/sources.yaml）：哪些 Job 在用、最近一次抓取怎么样
export interface SourceRow {
  automatic?: boolean;
  id: string; // 空的是 Job 里直接写了完整写法、注册表里没有的源
  name: string;
  spec: string;
  tags: string[];
  note: string;
  used_by: { job: string; name: string }[];
  last: { at: string; job: string; items: number; error: string } | null;
}

export interface SourceCatalog {
  revision?: string;
  adapters?: string[];
  sources: SourceRow[];
  problems: { job: string; problem: string }[]; // Job 里写了注册表没有的源或标签
}

export const listSources = (signal?: AbortSignal) => api<SourceCatalog>("/api/sources", { signal });
export const getSource = (id: string) => api<SourceRow & { revision: string }>(`/api/sources/${encodeURIComponent(id)}`);
export const saveSource = (id: string, revision: string, fields: { name: string; spec: string; tags: string[]; note: string }, create = false) => api(
  create ? '/api/sources' : `/api/sources/${encodeURIComponent(id)}`, { method: create ? 'POST' : 'PATCH',
    body: { ...(create ? { id } : {}), expected_revision: revision, fields } });
export const deleteSource = (id: string, revision: string) => api(`/api/sources/${encodeURIComponent(id)}`, {
  method: 'DELETE', query: { expected_revision: revision } });
