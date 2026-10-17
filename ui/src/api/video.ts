// 视频文稿：选题 → 研究 → 文稿 → 定稿 → AutoVideo 出片。见 spec/video-scripts.md；
// 风格、改编和审校见 spec/video-adaptation.md。
import { api } from "./client";

export type ScriptStatus = "RESEARCHING" | "WRITING" | "DRAFT" | "APPROVED" | "FAILED";
export type Evidence = "CITED" | "UNCITED" | "NEEDS_REVIEW";
export type RenderStatus =
  | "SUBMITTING"
  | "QUEUED"
  | "RUNNING"
  | "DOWNLOADING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface VideoColumn {
  key: string;
  name: string;
  description: string;
  target_minutes: number;
  video_form: string;
  writer?: "legacy" | "adapt";
  default_style?: { depth: string; tone: string };
  allowed_tones?: string[];
}

export interface StyleCatalog {
  depths: { key: string; name: string; version: number; audience: string }[];
  tones: { key: string; name: string; version: number; guidance: string }[];
}

export interface ScriptStyle {
  depth: string;
  tone: string;
  label: string;
  presets: string[];
}

export interface ScriptPlan {
  audience: { knows: string[]; lacks: string[]; cares: string; takeaway: string };
  core_message: string;
  hook: { kind: string; idea: string; facts: string[] };
  glossary: { term: string; plain: string; analogy: string; mapping: Record<string, string>; breaks: string }[];
  beats: { title: string; role: string; point: string; facts: string[]; terms: string[]; budget: number }[];
  left_out: { fact: string; reason: string; to_description: boolean }[];
}

export interface CheckIssue {
  rule: string;
  severity: "must" | "warn";
  section: number | null;
  paragraph: number | null;
  detail: string;
}

export interface ScriptChecks {
  rules: CheckIssue[];
  newcomer: {
    paragraphs: Record<string, { questions: string[]; lost: string[] }>;
    retell: string;
    scores: Partial<Record<"hook" | "clarity" | "pacing" | "style", number>>;
    comments: string;
  } | null;
  fidelity: {
    issues: { id: string; rule: string; detail: string }[];
    core_message_match: boolean | null;
    core_message_note: string;
  } | null;
  summary: {
    must: number;
    warn: number;
    questions: number;
    lost: number;
    fidelity: number;
    low_scores: string[];
    core_message_mismatch: boolean;
    passed: boolean;
  };
  rounds: number;
  model_checks_stale?: boolean;
}

export interface ScriptDescription {
  summary: string;
  details: string[];
  open_questions: string[];
  not_covered: string[];
  sources: { url: string; facts: string[] }[];
}

export interface Fact {
  id: string;
  claim: string;
  confidence: string;
  sources: string[];
  caveat: string;
  derived_from: string[];
  formula: string;
}

export interface FactSheet {
  id: string;
  report_path: string;
  question: string;
  answer: string;
  facts: Fact[];
  concepts: { term: string; meaning: string; needs: string[] }[];
}

export interface VideoTopic {
  id: string;
  round: string;
  code: string;
  title: string;
  fields: Record<string, string>;
  score: number | null;
  scripted: boolean;
}

export interface ScriptParagraph {
  text: string;
  citations: string[];
  evidence: Evidence;
  note?: string | null;
  facts?: string[];
  introduces?: string[];
  recap?: boolean;
}

export interface ScriptSection {
  title: string;
  paragraphs: ScriptParagraph[];
}

export interface LengthCheck {
  spoken_length: number;
  estimated_seconds: number;
  target_minutes: number | null;
  budget: number | null;
  ratio: number | null;
  status: "OK" | "OVER" | "UNDER" | null;
  sections: { title: string; spoken_length: number; budget?: number }[];
  dropped_citations?: number;
}

export interface ScriptVersion {
  script_id: string;
  version: number;
  title: string;
  sections: ScriptSection[];
  length_check: LengthCheck;
  edit_type: "GENERATED" | "EDITED";
  meta: {
    problems?: string[];
    attempts?: number;
    report_path?: string;
    writer?: "adapt";
    style?: ScriptStyle;
    plan?: ScriptPlan;
    description?: ScriptDescription;
    fact_sheet_id?: string;
  };
  checks?: ScriptChecks;
  created_by: string;
  created_at: string;
}

export interface VideoRender {
  id: string;
  script_id: string;
  version: number;
  status: RenderStatus;
  title: string;
  autovideo_job_id: string | null;
  autovideo_url: string | null;
  active_stage: string | null;
  attempt_count: number;
  resolved_video_form: string | null;
  error_code: string | null;
  error_message: string | null;
  source_chars: number;
  files: { name: string; content_type: string; bytes: number | null; url: string }[];
  submitted_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface VideoScriptSummary {
  id: string;
  column_key: string;
  topic: { id: string | null; title: string; fields?: Record<string, string> };
  status: ScriptStatus;
  stage?: string | null;
  research_job: string;
  report_path: string;
  current_version: number;
  approved_version: number | null;
  error: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  title: string;
  length_check: LengthCheck | null;
}

export interface VideoScriptDetail extends VideoScriptSummary {
  column: VideoColumn | null;
  version_shown: ScriptVersion | null;
  versions: (Pick<ScriptVersion, "version" | "title" | "edit_type" | "length_check" | "created_by" | "created_at"> & {
    style?: string | null;
  })[];
  renders: VideoRender[];
}

export interface AutoVideoStatus {
  base_url: string;
  reachable: boolean;
  ready: boolean;
  blockers: string[];
  voices: { id: string; label: string }[];
  error: string | null;
}

export const listVideoColumns = () => api<VideoColumn[]>("/api/video/columns");

export const listVideoStyles = () => api<StyleCatalog>("/api/video/styles");

export const listVideoTopics = () => api<{ round: string | null; topics: VideoTopic[] }>("/api/video/topics");

export const listVideoScripts = () => api<VideoScriptSummary[]>("/api/video/scripts");

export const getVideoScript = (id: string, version?: number) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}`, { query: { version } });

export const createVideoScript = (body: {
  column?: string;
  topic_id?: string;
  topic?: { title: string; angle?: string; audience?: string };
}) => api<VideoScriptSummary>("/api/video/scripts", { method: "POST", body });

export const saveVideoScript = (id: string, body: { title: string; sections: ScriptSection[]; base_version?: number }) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}`, { method: "PUT", body });

export const approveVideoScript = (id: string, version?: number) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: version ? { version } : {},
  });

export const renderVideoScript = (id: string, voiceId?: string) =>
  api<VideoRender>(`/api/video/scripts/${encodeURIComponent(id)}/render`, {
    method: "POST",
    body: voiceId ? { voice_id: voiceId } : {},
  });

export const getVideoRender = (id: string) => api<VideoRender>(`/api/video/renders/${encodeURIComponent(id)}`);

export const getAutoVideoStatus = () => api<AutoVideoStatus>("/api/video/autovideo");

export const restyleVideoScript = (id: string, style: { depth?: string; tone?: string }) =>
  api<VideoScriptSummary>(`/api/video/scripts/${encodeURIComponent(id)}/restyle`, { method: "POST", body: style });

export const getVideoFacts = (id: string, version?: number) =>
  api<FactSheet>(`/api/video/scripts/${encodeURIComponent(id)}/facts`, { query: { version } });
