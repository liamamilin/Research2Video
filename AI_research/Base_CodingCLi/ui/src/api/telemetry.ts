import { api } from "./client";

export type CostBasis = "reported" | "snapshot" | "table" | "unpriced";

/** Sums over a set of calls. `cost_usd` covers only what could be priced; `partial` says some could not. */
export type Usage = {
  calls: number; requests: number; failed: number;
  prompt_tokens: number; completion_tokens: number; reasoning_tokens: number; cache_read_tokens: number; total_tokens: number;
  seconds: number; cost_usd: number; unpriced_calls: number; estimated_calls: number; partial: boolean; bases: CostBasis[];
};
export type ModelUsage = Usage & { kind: "llm" | "search"; provider: string; model: string };
export type StageUsage = Usage & { stage: string; span_count: number; span_seconds: number; failed_spans?: number };
export type TelemetrySummary = {
  enabled: boolean; syncing?: boolean; totals?: Usage; llm?: Usage; search?: Usage;
  models?: ModelUsage[]; stages?: StageUsage[]; unpriced_models?: string[];
};
export type ScriptBill = Usage & {
  script_id: string; title: string; column_key: string; matrix: string; form: string; recipe: string; path: string;
  last_ts: number; produce_seconds: number; render_seconds: number; wait_seconds: number;
};
export type ScriptBills = { enabled: boolean; syncing?: boolean; items: ScriptBill[]; total: number };
export type ScriptDetail = {
  enabled: boolean; script_id: string; totals: Usage; stages: StageUsage[]; models: ModelUsage[];
};
export type CompareBy = "column_key" | "form" | "matrix" | "recipe" | "path";
export type CompareGroup = {
  value: string; scripts: number; tokens_avg: number; cost_avg_usd: number | null; cost_samples: number;
  produce_median_s: number | null; render_median_s: number | null; wait_median_s: number | null;
};
export type Comparison = { enabled: boolean; by: CompareBy; groups: CompareGroup[] };

const BASE = "/api/telemetry";
export const loadTelemetrySummary = (days: number, signal?: AbortSignal) => api<TelemetrySummary>(`${BASE}/summary`, { query: { days }, signal });
export const loadScriptBills = (days: number, offset: number, signal?: AbortSignal) =>
  api<ScriptBills>(`${BASE}/scripts`, { query: { days, offset, limit: 25 }, signal });
export const loadScriptDetail = (id: string, signal?: AbortSignal) => api<ScriptDetail>(`${BASE}/scripts/${encodeURIComponent(id)}`, { signal });
export const loadComparison = (by: CompareBy, days: number, signal?: AbortSignal) => api<Comparison>(`${BASE}/compare`, { query: { by, days }, signal });
