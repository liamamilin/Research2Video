import { api } from "./client";
import type { PublishAdaptationResult } from "@/lib/publish-text";
import type { UploadCandidate, FirstComment, PublishWindow, PlatformOverview } from "./platforms";

export const DOUYIN_API = "/api/platforms/douyin";
export type DouyinRecovery = {
  lookup_count?: number;
  last_checked_at?: number;
  next_lookup_at?: number;
  note?: string;
  matched_work_id?: string;
  auto_stopped?: boolean;
};
export type DouyinJob = {
  id: string; kind: string; status: string; phase: string; error: string; work_id: string;
  script_id: string; version: number; render_id: string; created_at: number; updated_at: number;
  automatic?: boolean;
  has_diagnostic?: boolean;
  recovery?: DouyinRecovery;
  first_comment?: { id: string; status: string; phase: string; error: string; text: string; comment_id: string; read_back: boolean; can_retry: boolean; created_at: number; updated_at: number } | null;
  verification?: { automatic: boolean; status: string; checked_at?: number | null; next_check_at?: number | null; reason: string; count: number };
  payload: { title?: string; description?: string; tags?: string[]; first_comment?: FirstComment }; url: string; can_retry: boolean;
};
export type DouyinLogin = DouyinJob & { image?: string; connected: boolean };
export type DouyinSettings = {
  first_comment: FirstComment;
  auto_upload: boolean;
  auto_since?: number;
  headless: boolean;
  publish_window: PublishWindow;
};
export type DouyinPreview = {
  adaptation?: PublishAdaptationResult;
  first_comment?: FirstComment;
  account_uid?: string;
  draft?: { payload: { title?: string; description?: string; tags?: string[]; first_comment?: FirstComment }; render_id: string } | null;
  credibility?: { tier: string; text: string };
  freshness_warnings?: string[];
  covers?: { portrait_url: string; landscape_url: string };
  existing_upload?: DouyinJob | null;
  render_id: string; script_id: string; version: number;
  title: string; description: string; tags: string[]; warnings: string[];
};
export type DouyinOverview = {
  account: { connected: boolean; uid?: string; name?: string; verified_at?: number };
  settings?: DouyinSettings;
  capabilities?: { first_comment: boolean; pin_comment: boolean };
  runtime_ready: boolean; login_id: string | null; uploads: DouyinJob[]; checks: DouyinJob[];
  candidates: UploadCandidate[];
  candidate_summary?: PlatformOverview["candidate_summary"] | null;
};
export const loadDouyin = (signal: AbortSignal) => api<DouyinOverview>(DOUYIN_API, { signal });
export const douyinAction = <T = DouyinJob>(path: string, body?: unknown, method = "POST") =>
  api<T>(`${DOUYIN_API}${path}`, { method, body });
