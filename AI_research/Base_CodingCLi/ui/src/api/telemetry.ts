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

export type PerformanceVideo = {
  bvid: string; title: string; pubdate: number; view: number | null; script_id: string; billed: boolean;
  column_key: string; form: string; matrix: string; recipe: string; path: string;
  tokens: number | null; cost_usd: number | null; partial: boolean; produce_seconds: number | null; render_seconds: number | null;
  cost_per_kview: number | null; tokens_per_kview: number | null;
};
export type PerformanceGroup = {
  value: string; videos: number; watched: number; views: number; view_median: number | null;
  tokens_per_kview: number | null; cost_samples: number; cost_partial: boolean; cost_avg_usd: number | null; cost_per_kview: number | null;
};
export type Performance = { enabled: boolean; connected?: boolean; by: CompareBy; items: PerformanceVideo[]; groups: PerformanceGroup[] };
export const loadPerformance = (by: CompareBy, days: number, signal?: AbortSignal) => api<Performance>(`${BASE}/performance`, { query: { by, days }, signal });

export type AssistantRequest = {
  request_id: string; conversation_id: string; user: string; ts: number; question: string; turns: number; tools: string[];
  prompt_tokens: number; completion_tokens: number; cache_read_tokens: number; cost_usd: number; partial: boolean;
  failed: number; seconds: number; peak_prompt_tokens: number; origin: AssistantOrigin;
};
/** app: sent from the app's own page; script: another program (curl, a coding session); "": before the origin was kept. */
export type AssistantOrigin = "app" | "script" | "";
export type OriginGroup = { requests: number; turns: number; prompt_tokens: number; cost_usd: number };
export type AssistantTool = { name: string; calls: number; requests: number; avg_chars: number | null; max_chars: number; total_chars: number };
export type AssistantTurn = {
  turn: number; requests: number; prompt_tokens_avg: number | null; completion_tokens_avg: number | null;
  message_chars_avg: number; tool_schema_chars_avg: number; tool_count_avg: number; retired: number;
};
export type AssistantReport = {
  enabled: boolean; syncing?: boolean; request_total?: number;
  totals?: {
    requests: number; turns: number; turns_per_request: number | null; prompt_tokens: number; completion_tokens: number;
    cache_read_tokens: number; cache_rate: number | null; cost_usd: number; partial: boolean; tool_schema_share: number | null;
    tool_calls: number; failed: number; earlier_calls: number; earlier_prompt_tokens: number; earlier_completion_tokens: number;
    by_origin?: Record<"app" | "script" | "", OriginGroup>;
  };
  tools?: AssistantTool[]; turns?: AssistantTurn[]; requests?: AssistantRequest[];
};
export type AssistantRequestTurn = {
  turn: number; message_chars: number | null; tool_schema_chars: number | null; tool_count: number | null; retired: number | null;
  saved_chars: number | null; tools: { name: string; chars: number | null }[]; prompt_tokens: number | null;
  completion_tokens: number | null; cache_read_tokens: number | null; cost_usd: number | null; partial: boolean;
  seconds: number | null; failed: number;
};
export type AssistantRequestDetail = { enabled: boolean; request_id: string; turns: AssistantRequestTurn[] };
export const loadAssistantUsage = (days: number, offset: number, signal?: AbortSignal, origin = "") =>
  api<AssistantReport>(`${BASE}/assistant`, { query: { days, offset, limit: 25, ...(origin ? { origin } : {}) }, signal });
export const loadAssistantRequest = (id: string, signal?: AbortSignal) =>
  api<AssistantRequestDetail>(`${BASE}/assistant/requests/${encodeURIComponent(id)}`, { signal });

export type StorageStats = { enabled: boolean; calls?: number; spans?: number; oldest?: number | null; newest?: number | null; bytes?: number };
export type PurgeResult = { enabled: boolean; cutoff: number; calls: number; spans: number; dry_run: boolean };
export const loadStorage = (signal?: AbortSignal) => api<StorageStats>(`${BASE}/storage`, { signal });
export const purgeRecords = (olderThanDays: number, dryRun: boolean) =>
  api<PurgeResult>(`${BASE}/purge`, { method: "POST", body: { older_than_days: olderThanDays, dry_run: dryRun } });

export type ReviewGroup = { value: string; videos: number; measured: number; median: number | null; max: number | null; best_title: string;
  previous_median: number | null; previous_measured: number; flag: "" | "high" | "low" } & Partial<WatchMedians>;
/** Creator-centre watch numbers: percentages, except avg_seconds. Absent in reviews saved before they were collected. */
export type WatchMedians = { watch_measured: number; completion: number | null; avg_seconds: number | null; exit3s: number | null; cover_pass: number | null };
export type ReviewVideo = { bvid: string; title: string; pubdate: number; duration: number; view: number | null; h24: number | null; h72: number | null; h168: number | null;
  state: "ok" | "pending" | "gap"; form: string; matrix: string; column: string; structure: string; metrics?: Record<string, number>;
  completion?: number | null; avg_seconds?: number | null; exit3s?: number | null; cover_pass?: number | null; watched_share?: number | null };
export type SavedReview = { start: number; stage: "24h" | "7d"; saved_at: number; findings: number; suggestions: number };
export type WeeklyReview = { connected: false } | { connected: true; missing: true; start: number; weeks: number[]; saved: SavedReview[] } | {
  connected: true; missing?: undefined; weeks: number[]; saved: SavedReview[]; stage: "" | "24h" | "7d"; saved_at?: number;
  start: number; end: number; now: number; min_n: number; ratio: number;
  summary: { videos: number; ok: number; pending: number; gap: number; median_24h: number | null; previous_median_24h: number | null; previous_videos: number; views: number; top_share: number | null; watch?: WatchMedians };
  dimensions: { key: string; label: string; groups: ReviewGroup[] }[]; top: ReviewVideo[]; findings: string[]; suggestions: string[]; videos: ReviewVideo[];
};
export const loadWeeklyReview = (start: number | null, stage: string, signal?: AbortSignal) =>
  api<WeeklyReview>(`${BASE}/weekly-review`, { query: { ...(start == null ? {} : { start }), ...(stage ? { stage } : {}) }, signal });
export const weeklyReportUrl = (start: number, stage: string) => `${BASE}/weekly-review/report?start=${start}&stage=${stage}`;
