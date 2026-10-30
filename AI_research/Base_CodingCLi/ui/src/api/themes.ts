import { api } from "./client";

export interface ThemeCategory {
  key: string; name: string; promise: string; audience: string; notes: string; production_ids: string[];
  legacy?: boolean;
}
export interface ThemeSignal {
  id: string; category: string; platform: string; title: string; url: string; excerpt: string;
  published_at?: string; collected_at: number; metrics: Record<string, number>;
  signal_kind: "public_metrics" | "indexed_content" | "reference_example";
  metrics_status?: string; metrics_observed_at?: number; metrics_error?: string;
  video_published_at?: number; video_author?: string;
  growth?: { views: number; hours: number; views_per_day: number };
}
export interface ThemeRanking {
  score: number; relevance: number; measured_signals: number; basis: string; formula: string;
  primary: { heat_score: number; signal_id: string; observed_at?: number; status: string;
    metrics: Record<string, number>; views_per_day?: number; speed_kind: string } | null;
}
export interface Theme {
  automatic_production?: { state: 'disabled' | 'ready' | 'quota' | 'queued' | 'working' | 'done' | 'attention' | 'skipped'; note: string; script_id?: string } | null;
  id: string; matrix: string; category: string; title: string; angle: string; audience: string; notes: string;
  origin: "manual" | "discovered" | "generated"; day: string; reason: string; signals: ThemeSignal[]; status: string;
  script_id: string; auto_video: boolean; created_at: number; updated_at: number;
  script_status?: string; stage?: string; error?: string; report_path?: string; version?: number;
  renders?: { id: string; status: string }[];
  ranking?: ThemeRanking;
  used_at?: number;
  feedback?: { action?: string; reason?: string; note?: string };
  inspiration?: { question: string; topic_key: string; pattern_id: string; pattern_name: string;
    title_pattern: string; outline: string[]; example_ids: string[]; basis: string };
}
export interface ThemePattern {
  id: string; category: string; name: string; title_pattern: string; outline: string[]; example_ids: string[];
}
export interface ThemeLibraryData {
  examples: { id: string; category: string; title: string; url: string; summary: string }[];
  patterns: ThemePattern[];
  runs: { id: string; status: string; brief: string; error: string;
    result: { recommended?: number; examples?: number } }[];
}
export interface ThemeHistory { themes: Theme[]; total: number; next_offset: number | null }
export type ThemeInput = Pick<Theme, "title" | "angle" | "audience" | "notes"> & { category: string };
export interface ThemeWorkbenchData {
  automatic_production?: ThemeProductionConfig;
  categories: ThemeCategory[]; legacy_categories?: ThemeCategory[]; themes: Theme[]; today: string; discovery_running: boolean;
  library?: ThemeLibraryData; used_count?: number; ideas_running?: boolean;
  runs: { id: string; status: string; started_at: number; error: string;
    result: { recommended?: number; categories?: number; signals?: number; empty_categories?: string[]; failures?: Record<string, string>;
      metrics?: { measured_signals: number; unique_videos: number; error?: string } } }[];
  configuration: { time: string; timezone: string; platforms: string[]; mode?: "ideas" | "discovery"; direction?: string };
  feedback?: { topics: number };
  schedule: { enabled: boolean; next_run: string } | null;
  scheduler: { online: boolean; detail: string };
}
export interface ThemeProductionConfig {
  enabled: boolean; daily_limit: number; minutes: number; daily_used?: number; timezone: string; starts_on: string;
  columns: Record<string, { name: string; period: 'day' | 'week'; count: number }>;
}
export const setThemeProduction = (matrix: string, config: Pick<ThemeProductionConfig, 'enabled' | 'daily_limit' | 'minutes'> & {
  columns: Record<string, { period: 'day' | 'week'; count: number }>;
}) => api<ThemeProductionConfig>(`${path(matrix)}/production`, { method: 'PUT', body: config });
const path = (matrix: string) => `/api/matrices/${encodeURIComponent(matrix)}/themes`;
export const getThemeWorkbench = (matrix: string, signal?: AbortSignal) => api<ThemeWorkbenchData>(path(matrix), { signal });
export const createTheme = (matrix: string, body: ThemeInput) => api<Theme>(path(matrix), { method: "POST", body });
export const updateTheme = (matrix: string, id: string, body: Omit<ThemeInput, "category">) =>
  api<Theme>(`${path(matrix)}/${id}`, { method: "PATCH", body });
export const discoverThemes = (matrix: string) => api<{ status: string; job: string }>(`${path(matrix)}/discover`, { method: "POST" });
export const generateThemes = (matrix: string, brief: string, category_key: string, pattern_id: string) =>
  api(`${path(matrix)}/generate`, { method: "POST", body: { brief, category_key, pattern_id } });
export const markThemeUsed = (matrix: string, id: string) => api<Theme>(`${path(matrix)}/${id}/used`, { method: "POST" });
export const getThemeHistory = (matrix: string, offset = 0) => api<ThemeHistory>(`${path(matrix)}/history`, { query: { offset } });
export const startTheme = (matrix: string, id: string, auto_video: boolean, minutes: number) =>
  api<Theme>(`${path(matrix)}/${id}/start`, { method: "POST", body: { auto_video, minutes } });
export const dismissTheme = (matrix: string, id: string) => api<Theme>(`${path(matrix)}/${id}/dismiss`, { method: "POST" });
export const setThemeSchedule = (matrix: string, enabled: boolean, time: string, platforms: string[], mode?: string, direction?: string) =>
  api(`${path(matrix)}/schedule`, { method: "PUT", body: { enabled, time, platforms, mode, direction } });
export const sendThemeFeedback = (matrix: string, id: string, action: string, reason = "", note = "") =>
  api<Theme>(`${path(matrix)}/${id}/feedback`, { method: "POST", body: { action, reason, note } });
export interface ThemeUsageStage {
  key: string; name: string; shared?: boolean; batch_topics?: number; partial?: boolean;
  requests: number | null; total_tokens: number | null; searches: number | null;
  duration_seconds: number | null; cost_usd: number | null;
  attempts: { id?: string; status: string }[];
}
export const getThemeUsage = (matrix: string, id: string) =>
  api<{ stages: ThemeUsageStage[]; note: string }>(`${path(matrix)}/${id}/usage`);
