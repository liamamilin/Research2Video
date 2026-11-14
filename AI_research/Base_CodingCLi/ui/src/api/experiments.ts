import { api } from "./client";

export type SwitchRow = {
  path: string; label: string; readers: string[]; timing: "before_writing" | "from_writing";
  reaches: "all" | "with_column"; applied_by: string; status: "existing" | "planned" | "unconfirmed";
  values: unknown[]; note: string;
};
export type Definition = {
  id: string; name: string; hypothesis?: string; kind: "compare" | "aa"; share_b: number;
  scope?: { matrices?: string[]; columns?: string[]; content_mode?: "individual" | "episode" | null };
  arms: { A: { name: string; overrides: Record<string, unknown> }; B: { name: string; overrides: Record<string, unknown> } };
  metric: { primary: string; better: "higher" | "lower"; min_effect: number };
  guardrails?: { metric: string; better: "higher" | "lower" }[];
  stop: { per_arm: number; max_days: number };
};
export type ExperimentStatus = "draft" | "queued" | "running" | "paused" | "ended" | "decided";
export type ExperimentRow = { id: string; status: ExperimentStatus; created_at: number; updated_at: number; started_at?: number | null; definition: Definition };
export type ExperimentsOverview = { enabled: boolean; experiments: ExperimentRow[]; registry: SwitchRow[] };
export type ExperimentDetail = ExperimentRow & { events: { kind: string; at: number; by: string }[]; start_problems: string[]; arms: { A: number; B: number } };
export type Move = "start" | "pause" | "resume" | "end" | "queue" | "unqueue";

const BASE = "/api/experiments";
export const loadExperiments = (signal?: AbortSignal) => api<ExperimentsOverview>(BASE, { signal });
export const loadExperiment = (id: string, signal?: AbortSignal) => api<ExperimentDetail>(`${BASE}/${encodeURIComponent(id)}`, { signal });
export const setExperimentsEnabled = (enabled: boolean) => api<{ enabled: boolean }>(`${BASE}/settings`, { method: "PUT", body: { enabled } });
export const createExperiment = (definition: Definition) => api<ExperimentRow & { duplicates?: { id: string; name: string; status: string; verdict: string | null }[] }>(BASE, { method: "POST", body: definition });
export const moveExperiment = (id: string, action: Move) => api<ExperimentRow>(`${BASE}/${encodeURIComponent(id)}/${action}`, { method: "POST" });

export type PreviewSource = { render_id: string; script_id: string; title: string; completed_at: string };
export type PreviewState = {
  kind: "render" | "cover" | "none"; confirmed: boolean; sources: PreviewSource[];
  preview: { id: string; status: "requested" | "running" | "done" | "failed"; error: string; source_render_id: string; files: string[] } | null;
  a_video: string | null; b_video: string | null; covers?: { a: string; b: string } | null;
};
export const loadPreview = (id: string, signal?: AbortSignal) => api<PreviewState>(`${BASE}/${encodeURIComponent(id)}/preview`, { signal });
export const requestPreview = (id: string, sourceRenderId?: string) => api(`${BASE}/${encodeURIComponent(id)}/preview`, { method: "POST", body: { source_render_id: sourceRenderId || null } });
export const confirmPreview = (id: string) => api(`${BASE}/${encodeURIComponent(id)}/preview/confirm`, { method: "POST" });

export type Estimate = { estimate: number | null; low: number | null; high: number | null; n_a: number; n_b: number; strata: number; skipped_strata: string[]; sd: number | null };
export type Results = {
  frozen: boolean; n: { A: number; B: number }; with_data: { A: number; B: number }; missing: { A: number; B: number };
  srm_p: number; primary: Estimate & { metric: string }; guardrails: (Estimate & { metric: string; status: "ok" | "worse" | "hint" })[];
  harm: string | null; applied_rate: number | null; active_days: number; endpoint_reached: boolean; settled: boolean;
  verdict: { verdict: "running" | "srm" | "b_better" | "b_worse" | "no_difference"; notes: string[]; minimum_detectable?: number | null };
  median_views: { A: number | null; B: number | null }; median_duration: { A: number | null; B: number | null };
  no_metric?: { A: number; B: number }; missing_alarm?: boolean; reference?: Estimate & { min_views: number };
  completion_skipped?: { A: number; B: number }; code_changed?: { files: string[] } | null; production_gaps?: number;
  costs?: Partial<Record<"A" | "B", { scripts: number; cost_usd: number; render_minutes: number; partial: boolean }>>;
  videos: { script_id: string; arm: "A" | "B"; bvid: string; state: "measured" | "pending" | "missing"; [metric: string]: unknown }[];
};
export const loadResults = (id: string, signal?: AbortSignal) => api<Results>(`${BASE}/${encodeURIComponent(id)}/results`, { signal });

export type Suggestion = { path: string; to: unknown; column: string; column_name?: string; from?: unknown; editable: boolean; link?: string; reason?: string };
export const loadSuggestion = (id: string, signal?: AbortSignal) => api<{ verdict: string | null; items: Suggestion[] }>(`${BASE}/${encodeURIComponent(id)}/suggestion`, { signal });

export type RecordRow = { id: string; name: string; kind: "compare" | "aa"; hypothesis: string; status: ExperimentStatus; changes: Record<string, unknown>;
  metric: string; verdict: string | null; estimate: number | null; low: number | null; high: number | null; minimum_detectable: number | null;
  n: { A: number; B: number } | null; decision: { choice: string; label: string } | null };
export const loadRecords = (signal?: AbortSignal) => api<{ items: RecordRow[] }>(`${BASE}/records`, { signal });
export const decideExperiment = (id: string, choice: "adopt" | "keep" | "defer") => api<ExperimentRow>(`${BASE}/${encodeURIComponent(id)}/decide`, { method: "POST", body: { choice } });

export type EstimateResult = { per_day: number; per_arm_per_day?: number; coverage: number | null; days: number | null; max_days?: number; window_days: number; stopped_days?: number; within_max?: boolean };
export const estimateExperiment = (definition: Definition) => api<EstimateResult>(`${BASE}/estimate`, { method: "POST", body: definition });
