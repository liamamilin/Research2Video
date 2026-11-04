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
export type ExperimentStatus = "draft" | "running" | "paused" | "ended" | "decided";
export type ExperimentRow = { id: string; status: ExperimentStatus; created_at: number; updated_at: number; started_at?: number | null; definition: Definition };
export type ExperimentsOverview = { enabled: boolean; experiments: ExperimentRow[]; registry: SwitchRow[] };
export type ExperimentDetail = ExperimentRow & { events: { kind: string; at: number; by: string }[]; start_problems: string[]; arms: { A: number; B: number } };
export type Move = "start" | "pause" | "resume" | "end";

const BASE = "/api/experiments";
export const loadExperiments = (signal?: AbortSignal) => api<ExperimentsOverview>(BASE, { signal });
export const loadExperiment = (id: string, signal?: AbortSignal) => api<ExperimentDetail>(`${BASE}/${encodeURIComponent(id)}`, { signal });
export const setExperimentsEnabled = (enabled: boolean) => api<{ enabled: boolean }>(`${BASE}/settings`, { method: "PUT", body: { enabled } });
export const createExperiment = (definition: Definition) => api<ExperimentRow>(BASE, { method: "POST", body: definition });
export const moveExperiment = (id: string, action: Move) => api<ExperimentRow>(`${BASE}/${encodeURIComponent(id)}/${action}`, { method: "POST" });
