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
  audience?: string;
  allowed_forms?: string[];
  topic_kind?: "single" | "roundup" | "comparison";
  recheck?: boolean;
  status?: CellStatus;
}

export type CellStatus = "enabled" | "experimental" | "blocked";

// 内容矩阵的一个格子（领域 × 受众 × 类型 × 形态），见 spec/video-matrix.md。
export interface VideoCell {
  id: string;
  key: string | null; // 创建文稿时用的栏目名；blocked 的格子没有
  status: CellStatus;
  reason: string;
  usable: boolean;
  label: string;
  parts: Record<"domain" | "audience" | "type" | "format", { key: string; name: string; version: number }> | null;
  column: VideoColumn | null;
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
  question?: string;
  audience: { knows: string[]; lacks: string[]; cares: string; takeaway: string };
  goals?: { question: string; answer: string; facts: string[] }[];
  chain?: { step: string; facts: string[] }[];
  misconceptions?: { wrong: string; fix: string }[];
  actions?: { condition: string; step: string; verify: string; needs: string[] }[];
  must_cover?: { item: string; beat: number }[];
  scope_out?: string[];
  fit?: { ok: boolean; note: string };
  core_message: string;
  hook: { kind: string; idea: string; facts: string[] };
  glossary: { term: string; plain: string; needs?: string[]; analogy: string; mapping: Record<string, string>; breaks: string }[];
  beats: { title: string; role: string; point: string; facts: string[]; terms: string[]; budget: number }[];
  left_out: { fact: string; reason: string; to_description: boolean }[];
}

export type AuditName = "fact" | "comprehension" | "communication";
export type Severity = "serious" | "must" | "warn";

export interface CheckIssue {
  audit: AuditName;
  rule: string;
  severity: Severity;
  section: number | null;
  paragraph: number | null;
  quote: string;
  problem: string;
  why: string;
  suggestion: string;
  source: "rule" | "model";
  unhandled?: string;
  resolved?: boolean | "deleted";
}

export interface ScriptChecks {
  version: 2;
  issues: CheckIssue[];
  quiz: {
    items: { id: string; question: string; expected: string; answer: string | null; correct: boolean | null; note: string }[];
    score: number | null;
  };
  newcomer: {
    retell: string;
    scores: Partial<Record<"hook" | "clarity" | "pacing" | "style", number>>;
    comments: string;
  } | null;
  core_message_match: boolean | null;
  core_message_note: string;
  model_ran: boolean;
  summary: {
    serious: number;
    must: number;
    warn: number;
    rule_must: number;
    questions: number;
    lost: number;
    quiz_score: number | null;
    low_scores: string[];
    by_audit: Record<AuditName, Record<Severity, number>>;
    passed: boolean;
  };
  rounds: number;
  deletions: { section: number; paragraph: number; quote: string; before: string; rule: string; reason: string }[];
  initial: CheckIssue[];
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
  published?: boolean;
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
    prompts?: Record<string, number>;
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
  published?: boolean;
}

// 发布记录和发布前复核（快讯），见 spec/video-publish.md。
export interface Publication {
  id: string;
  script_id: string;
  version: number | null;
  render_id: string | null;
  platform: string;
  url: string;
  published_on: string;
  title: string;
  note: string;
  recorded_by: string;
}

export interface Recheck {
  id: string;
  version: number;
  status: "RUNNING" | "DONE" | "FAILED";
  verdict: "无变化" | "有变化" | "无法确认" | null;
  summary: string;
  report_path?: string;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface VideoScriptDetail extends VideoScriptSummary {
  column: VideoColumn | null;
  version_shown: ScriptVersion | null;
  versions: (Pick<ScriptVersion, "version" | "title" | "edit_type" | "length_check" | "created_by" | "created_at"> & {
    style?: string | null;
  })[];
  renders: VideoRender[];
  publications?: Publication[];
  rechecks?: Recheck[];
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

// 上传用的发布包：标题、简介、章节、标签、封面文字。见 core/video/publish.py。
export interface PublishPack {
  title: string;
  description: string;
  chapters: { seconds: number; time: string; title: string }[];
  chapter_note: string;
  tags: string[];
  cover: { headline: string; badge: string };
  references: { title: string; url: string; date: string }[];
  warnings: string[];
  version: number;
  freshness?: {
    required: boolean;
    age_hours?: number;
    stale?: boolean;
    running?: boolean;
    last?: { verdict: string; summary: string; finished_at: string; version: number } | null;
    warnings: string[];
  };
}

export const getPublishPack = (id: string, version?: number) =>
  api<PublishPack>(`/api/video/scripts/${encodeURIComponent(id)}/publish`, { query: { version } });

// 初稿质量：定稿的文稿保留了多少生成的初稿，见 core/video/quality.py。
export interface DraftQuality {
  columns: Record<
    string,
    { scripts: number; edited_by_hand: number; median_kept_share: number | null; paragraphs_untouched: number | null }
  >;
  scripts: {
    script_id: string;
    column_key: string;
    title: string;
    draft_version: number;
    approved_version: number;
    unchanged: number;
    edited: number;
    dropped: number;
    added: number;
    kept_share: number | null;
    new_share: number | null;
    signals: { length_status: string | null; serious: number | null; must: number | null; problems: number; audited: boolean };
    examples: { before: string; after: string; similarity: number }[];
  }[];
}

export const getDraftQuality = () => api<DraftQuality>("/api/video/quality");

export const addPublication = (id: string, body: { url: string; published_on?: string; note?: string }) =>
  api<Publication>(`/api/video/scripts/${encodeURIComponent(id)}/publications`, { method: "POST", body });

export const deletePublication = (id: string, publicationId: string) =>
  api<{ deleted: string }>(`/api/video/scripts/${encodeURIComponent(id)}/publications/${encodeURIComponent(publicationId)}`, {
    method: "DELETE",
  });

export const recheckVideoScript = (id: string) =>
  api<Recheck>(`/api/video/scripts/${encodeURIComponent(id)}/recheck`, { method: "POST" });

export const listVideoCells = () => api<VideoCell[]>("/api/video/cells");

export const listVideoStyles = () => api<StyleCatalog>("/api/video/styles");

export const listVideoTopics = (source: "opportunities" | "flash" = "opportunities") =>
  api<{ round: string | null; topics: VideoTopic[] }>("/api/video/topics", { query: { source } });

export const listVideoScripts = () => api<VideoScriptSummary[]>("/api/video/scripts");

export const getVideoScript = (id: string, version?: number) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}`, { query: { version } });

export const createVideoScript = (body: {
  column?: string;
  topic_id?: string;
  topic?: { title?: string; angle?: string; audience?: string; subjects?: string[]; dimensions?: string };
  topic_ids?: string[];
  video_form?: string;
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

export interface BoardStage {
  key: string;
  label: string;
  count: number;
}

export interface BoardAttention {
  kind: "review" | "failed" | "render" | "render_done" | "recheck";
  script_id: string;
  title: string;
  column_key: string;
  text: string;
}

export interface VideoBoard {
  stages: BoardStage[];
  failed: number;
  attention: BoardAttention[];
}

export const getVideoBoard = () => api<VideoBoard>("/api/video/board");
