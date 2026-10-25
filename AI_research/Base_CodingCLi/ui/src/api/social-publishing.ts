import { api } from "./client";
import type { PublishAdaptationResult } from "@/lib/publish-text";
import type { FirstComment, UploadCandidate } from "./platforms";
export type SocialDefinition = {
  id: string; name: string; creator_url: string; title_min: number; title_limit: number; description_limit: number; tag_limit: number; tag_length_limit?: number;
  combined_limit: number | null; public_check_gap: string; upload_note?: string; ownership_check?: boolean; acceptance: string;
  first_comment_limit?: number;
  capabilities: { connect: boolean; upload: boolean; public_check: boolean; first_comment: boolean; pin_comment: boolean; analytics: boolean };
};
export type SocialJob = {
  id: string; kind: string; platform: string; status: string; phase: string; error: string; work_id: string; url: string;
  script_id: string; version: number; render_id: string; created_at: number; updated_at: number; can_retry: boolean;
  can_confirm_absent?: boolean;
  login_purpose?: "comments";
  first_comment?: { id: string; status: string; phase: string; error: string; text: string; comment_id: string; read_back: boolean; can_retry: boolean } | null;
  verification?: { automatic: boolean; attempts?: number; checked_at?: number; next_check_at?: number; state?: string; reason?: string };
  payload: { title?: string; description?: string; tags?: string[]; first_comment?: FirstComment };
};
export type SocialOverview = {
  definition: SocialDefinition; account: { connected: boolean; uid?: string; name?: string; verified_at?: number; comments_verified_at?: number };
  runtime_ready: boolean; login_id: string | null; uploads: SocialJob[]; checks: SocialJob[]; candidates: UploadCandidate[];
  settings?: { first_comment?: FirstComment };
};
export type SocialPreview = {
  adaptation?: PublishAdaptationResult;
  definition: SocialDefinition; account_uid: string; render_id: string; script_id: string; version: number;
  title: string; description: string; tags: string[]; warnings: string[];
  first_comment?: FirstComment | null;
  draft?: { payload: { title?: string; description?: string; tags?: string[]; first_comment?: FirstComment }; render_id: string } | null;
};
export const socialBase = (platform: string) => `/api/platforms/social/${encodeURIComponent(platform)}`;
export const socialAction = <T = SocialJob>(platform: string, path: string, body?: unknown, method = "POST") => api<T>(`${socialBase(platform)}${path}`,{method,body});
