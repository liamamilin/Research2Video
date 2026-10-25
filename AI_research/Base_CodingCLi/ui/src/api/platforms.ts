import { api } from "./client";

export const PLATFORM_API = "/api/platforms/bilibili";
export type BilibiliCategory = { tid: number; name: string; path: string; description: string };
export type BilibiliCategoryCatalog = { categories: BilibiliCategory[]; updated_at: number; stale: boolean };
export const loadBilibiliCategories = (signal: AbortSignal) => api<BilibiliCategoryCatalog>(`${PLATFORM_API}/categories`, { signal });
export type FirstComment = { enabled: boolean; text: string; pin: boolean };
export const EMPTY_FIRST_COMMENT: FirstComment = { enabled: false, text: "", pin: false };
export type PlatformSettings = { auto_upload: boolean; sync_comments: boolean; tid: number; auto_since?: number; first_comment?: FirstComment };
export type UploadPayload = { render_id: string; title: string; description: string; tags: string[]; tid: number; first_comment?: FirstComment };
export type UploadPreview = UploadPayload & { warnings: string[]; credibility: { text: string }; freshness_warnings: string[]; existing_upload?: UploadTask | null };
export type UploadTask = { id: string; render_id: string; script_id: string; version: number; status: string; bvid: string; error: string; created_at: number; payload: UploadPayload;
  first_comment_status?: string; first_comment_rpid?: string; first_comment_error?: string; first_comment_pin_status?: string; first_comment_pin_error?: string };
export type PlatformComment = { bvid: string; rpid: string; root: string; author: string; message: string; ctime: number; likes: number; status: string; draft: string; reply: string; reply_rpid: string; error: string };
export type UploadCandidate = { render_id: string; script_id: string; title: string; version: number; completed_at: string; archived?: number; current_version?: number; approved_version?: number; script_status?: string };
export type PlatformOverview = {
  account: { connected: boolean; uid?: string; name?: string; verified_at?: number; upload_authorized?: boolean };
  settings: PlatformSettings;
  biliup_ready: boolean;
  publication_flow?: boolean;
  uploads: UploadTask[];
  videos: { bvid: string; title: string; synced_at: number | null; sync_error?: string }[];
  comments: PlatformComment[];
  candidates: UploadCandidate[];
  candidate_summary?: { completed_renders: number; available_versions: number; published_versions: number; queued_versions: number; unavailable_versions: number; ready_versions: number };
};
export const loadPlatforms = (signal: AbortSignal) => api<PlatformOverview>(PLATFORM_API, { signal });
export const platformAction = <T,>(path: string, body?: unknown, method = "POST") =>
  api<T>(PLATFORM_API + path, { method, body, timeoutMs: 60_000 });
