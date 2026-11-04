import { api } from "./client";

export type SwitchRow = {
  path: string; label: string; readers: string[]; timing: "before_writing" | "from_writing";
  reaches: "all" | "with_column"; applied_by: string; status: "existing" | "planned" | "unconfirmed";
  values: unknown[]; note: string;
};
export type ExperimentRow = {
  id: string; status: "draft" | "running" | "paused" | "ended" | "decided"; created_at: number; updated_at: number;
  definition: { name: string; kind: "compare" | "aa"; share_b: number; metric: { primary: string }; stop: { per_arm: number; max_days: number } };
};
export type ExperimentsOverview = { enabled: boolean; experiments: ExperimentRow[]; registry: SwitchRow[] };

export const loadExperiments = (signal?: AbortSignal) => api<ExperimentsOverview>("/api/experiments", { signal });
