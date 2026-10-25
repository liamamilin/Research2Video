// 视频文稿：选题 → 研究 → 文稿 → 定稿 → AutoVideo 出片。见 spec/video-scripts.md；
// 风格、改编和审校见 spec/video-adaptation.md。
import { api } from "./client";
import type { UploadTask } from "./platforms";

export type ScriptStatus = "RESEARCHING" | "WRITING" | "PLAN_READY" | "DRAFT" | "APPROVED" | "FAILED";
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
  parts: Record<"domain" | "audience" | "type" | "format", { key: string; name: string }> | null;
  column: VideoColumn | null;
  recipe?: string; // 栏目名：配方文件名，或配方加上它的另一个受众
  base?: string; // 配方文件名（config/video_recipes/<base>.yaml）
  audience?: string; // 给谁看的档案名；配方没写 audiences 就是空的
  minutes_range?: number[]; // 配置可以把时长改成哪个范围 [最短, 最长]；空：时长不能改
  research_optional?: boolean; // 配置可以关掉重新调研（research.optional）
  whole_report?: boolean; // 可以从整份报告写（topic.whole_report），给没有条目清单的 Job 用
  error?: string; // 配方文件有错时的原因
  topic_sources?: string[];
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
  scope?: "article_fidelity";
  fidelity_checked?: boolean;
  article_summary?: string;
  mode?: "on_request";
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

export type CredibilityTier = "serious" | "verify" | "spoken" | "unaudited" | "partial" | "trusted" | "generated";

/** 文稿可信度：从审计结果和出处标注算出，只提示不拦截（core/video/credibility.py）。 */
export interface Credibility {
  tier: CredibilityTier;
  text: string;
  reasons: string[];
  cited_share: number | null;
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
  distributions?: Distribution[];
  dismissed?: boolean;
  stale?: boolean;
  event_date?: string | null;
  scripts?: { id: string; column_key: string; status: ScriptStatus; created_at: string; archived?: number }[];
  /** 来自哪个选题来源：Job 的 topics.alias（或 Job 路径），手动添加的是 manual */
  source?: string;
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
  timings?: { elapsed_seconds?: number; download_seconds?: number; process_seconds?: number };
  visuals?: import("./screenshots").AutomaticVisuals | null;
}

export interface VideoScriptSummary {
  id: string;
  column_key: string;
  topic: { id: string | null; title: string; fields?: Record<string, string>; article?: { mode: "read" | "adapt"; kind: "literary" | "information" } };
  status: ScriptStatus;
  stage?: string | null;
  research_job: string;
  report_path: string;
  report_id?: string | null; // 研究报告记录（/api/video/reports），同一份报告可以写多篇
  current_version: number;
  approved_version: number | null;
  error: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  title: string;
  length_check: LengthCheck | null;
  published?: boolean;
  distributions?: Distribution[];
  archived?: number;
  credibility?: Credibility | null;
  auto?: boolean;
  matrix?: string;
  matrix_name?: string;
  job?: string;
  job_name?: string;
  column_label?: string;
  has_video?: boolean;
  has_previous_video?: boolean;
  render?: { id?: string; version?: number; status: string; created_at: string } | null; // 当前版本的出片进度
}

export interface Distribution {
  script_id: string;
  version: number | null;
  platform: string;
  account_uid: string;
  account_name: string;
  connected: boolean;
  current_account: boolean;
  status: string;
  can_prepare: boolean;
  blocked_reason: string;
  task: { id: string; status: string; error: string; render_id: string; work_id: string; updated_at: number } | null;
  draft: { render_id: string; updated_at: string } | null;
  publications: Publication[];
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
  account_uid?: string;
  account_name?: string;
  work_id?: string;
  sources?: string;
  confirmed_at?: string;
  removed_at?: string;
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
  configuration?: {
    links: { label: string; name: string; href: string; scope: string; next: string }[];
    changed_since_generation: boolean | null;
    recipe_available: boolean;
  };
  column: VideoColumn | null;
  version_shown: ScriptVersion | null;
  versions: (Pick<ScriptVersion, "version" | "title" | "edit_type" | "length_check" | "created_by" | "created_at"> & {
    style?: string | null;
  })[];
  renders: VideoRender[];
  publications?: Publication[];
  platform_uploads?: UploadTask[];
  rechecks?: Recheck[];
  steps?: ScriptStep[]; // 调研 → 写稿 → 审计 → 定稿 → 出片，按现有记录读出来（spec/video-steps.md）
  events?: ScriptEvent[]; // 谁在什么时候启动 / 重跑了哪一步
  pending_plan?: PendingPlan | null; // 配方 writing.pause_after_plan：方案定好后等你确认
}

export interface VideoComparisonSide {
  version: number; title: string; sections: ScriptSection[]; created_at: string;
  duration: { estimated_seconds?: number; target_minutes?: number; binding: boolean };
  configuration: { recipe?: string; recipe_sha256?: string; creative_direction?: string; parts: Record<string, unknown> };
  report: { path: string; href: string } | null; render: VideoRender | null; renders: VideoRender[]; script_href: string;
}
export interface VideoComparison {
  script_id: string; before: VideoComparisonSide; after: VideoComparisonSide;
  sections: { index: number; changed: boolean; before: ScriptSection | null; after: ScriptSection | null }[];
  changes: { title: boolean; changed_sections: number; configuration_fields: string[]; same_report: boolean | null };
  href: string; note: string;
}
export const getVideoComparison = (id: string, before: number, after: number, beforeRender: string, afterRender: string, signal?: AbortSignal) => api<VideoComparison>(
  `/api/video/scripts/${id}/compare`, { query: { from_version: before, to_version: after, before_render: beforeRender || undefined, after_render: afterRender || undefined }, signal });

export interface PendingPlan {
  id: string;
  plan: ScriptPlan;
  problems: string[];
  style: { depth?: string; tone?: string };
  note: string;
  created_by: string;
  created_at: string;
}

/** 只放改了的字；beats 每节一项，没改的节给空对象。 */
export interface PlanEdits {
  question?: string;
  core_message?: string;
  hook_idea?: string;
  beats?: { title?: string; point?: string }[];
}

export type StepName = "research" | "write" | "audit" | "approve" | "render";
export interface ScriptStep {
  step: StepName;
  label: string;
  state: "PENDING" | "RUNNING" | "DONE" | "FAILED" | "SKIPPED" | "WAITING" | "INCOMPLETE";
  note: string;
}
export interface ScriptEvent { step: StepName; event: string; note: string; by: string; at: string }

export const rerunVideoStep = (id: string, step: StepName) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/steps/${step}/rerun`, { method: "POST" });

export const confirmVideoPlan = (id: string, edits: PlanEdits) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/plan/confirm`, { method: "POST", body: edits });
export const redoVideoPlan = (id: string, note: string) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/plan/redo`, { method: "POST", body: { note } });
export const discardVideoPlan = (id: string) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/plan/discard`, { method: "POST" });

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
  references: { title: string; url: string; date: string; cited?: boolean; kind?: string }[];
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

export const getPublishPack = (id: string, version?: number, signal?: AbortSignal) =>
  api<PublishPack>(`/api/video/scripts/${encodeURIComponent(id)}/publish`, { query: { version }, signal });

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

export const addPublication = (id: string, body: { url: string; published_on?: string; note?: string; version?: number; render_id?: string; platform?: string; account_uid?: string }) =>
  api<Publication>(`/api/video/scripts/${encodeURIComponent(id)}/publications`, { method: "POST", body });

export const deletePublication = (id: string, publicationId: string) =>
  api<{ deleted: string }>(`/api/video/scripts/${encodeURIComponent(id)}/publications/${encodeURIComponent(publicationId)}`, {
    method: "DELETE",
  });

export const recheckVideoScript = (id: string) =>
  api<Recheck>(`/api/video/scripts/${encodeURIComponent(id)}/recheck`, { method: "POST" });

export const listVideoCells = (signal?: AbortSignal) => api<VideoCell[]>("/api/video/cells", { signal });

export interface CellCoverage {
  cell: string;
  label: string;
  status: string;
  sources: string[];
  open: number;
  written: number;
  dismissed: number;
  multi: boolean;
  autopilot: boolean;
  report_date: string | null;
  /** 配方里写了、但没有 Job 声明的选题来源 */
  unknown_sources?: string[];
}

export interface DiscoveryStatus {
  source: string;
  job: string;
  label: string;
  running: boolean;
  report_date: string | null;
  report_at: number | null;
  /** 上次跑这个 Job 用了几次搜索；没跑过是 null */
  searches: number | null;
}

/** 选题来源：声明了 topics: 的 Job，加上「手动添加」（name 为 manual）。 */
export interface TopicSource {
  name: string;
  job: string | null;
  label: string;
  format: string | null;
  prefix: string;
  refresh: boolean;
  error: string;
  reports: number;
  latest: string | null;
  report_error: string;
  count?: number;
}

export const listTopicSources = (signal?: AbortSignal) => api<TopicSource[]>("/api/video/topics/sources", { signal });
export const getDiscovery = (source = "flash") => api<DiscoveryStatus>("/api/video/topics/discovery", { query: { source } });
export const startDiscovery = (source = "flash") =>
  api<DiscoveryStatus>("/api/video/topics/discovery", { method: "POST", query: { source } });
export const addVideoTopic = (body: { title: string; angle?: string; audience?: string; fields?: Record<string, string> }) =>
  api<VideoTopic>("/api/video/topics", { method: "POST", body });
export const dismissTopic = (topic_id: string, dismissed: boolean) =>
  api<{ topic_id: string; dismissed: boolean }>("/api/video/topics/dismiss", { method: "POST", body: { topic_id, dismissed } });

export const listCellCoverage = (signal?: AbortSignal) => api<CellCoverage[]>("/api/video/coverage", { signal });

export const listVideoStyles = (signal?: AbortSignal) => api<StyleCatalog>("/api/video/styles", { signal });

export interface TopicListing {
  round: string | null;
  rounds?: string[];
  topics: VideoTopic[];
}

export interface TopicBrowser {
  cells: VideoCell[];
  coverage: CellCoverage[];
  sources: TopicSource[];
  initial: TopicListing & { source: string };
}

export const getTopicBrowser = (signal?: AbortSignal) => api<TopicBrowser>("/api/video/topics/browser", { signal });

export const listVideoTopics = (source = "opportunities", round?: string, signal?: AbortSignal) =>
  api<TopicListing>("/api/video/topics", { query: { source, ...(round ? { round } : {}) }, signal });

export const listVideoScripts = (archived = false, signal?: AbortSignal) =>
  api<VideoScriptSummary[]>("/api/video/scripts", { query: archived ? { archived: "true" } : undefined, signal });

/** 归档：从看板和列表里收起来（不删除）；archived=false 取消归档。 */
export const archiveVideoScript = (id: string, archived = true) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/archive`, { method: "POST", body: { archived } });

export const getVideoScript = (id: string, version?: number, signal?: AbortSignal) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}`, { query: { version }, signal });

export const createVideoScript = (body: {
  column?: string;
  topic_id?: string;
  topic?: { title?: string; angle?: string; audience?: string; subjects?: string[]; dimensions?: string };
  topic_ids?: string[];
  video_form?: string;
  report_id?: string; // 用已有的研究报告写，不再调研；不给选题时用报告当初调研的那个选题
}) => api<VideoScriptSummary>("/api/video/scripts", { method: "POST", body });

// 研究报告记录：每个报告文件一行，记着从它写出的所有文稿（spec/video-reports.md）
export interface ResearchReport {
  id: string;
  report_path: string;
  kind: "video_research" | "topic_source" | "other";
  kind_label: string;
  job: string;
  topic_id: string;
  topic_title: string;
  research: string;
  created_by: string;
  created_at: string;
  on_disk: boolean;
  scripts: { id: string; column_key: string; status: ScriptStatus; title: string; archived: boolean; created_at: string }[];
}

export const listResearchReports = () => api<ResearchReport[]>("/api/video/reports");
export const getResearchReport = (id: string) => api<ResearchReport>(`/api/video/reports/${encodeURIComponent(id)}`);
// 任何一份报告（报告页的路径，相对 output/）记成研究报告，就能直接写成视频；title 是报告的第一个标题
export const recordResearchReport = (path: string) =>
  api<ResearchReport & { title: string }>("/api/video/reports", { method: "POST", body: { path } });

export const saveVideoScript = (id: string, body: { title: string; sections: ScriptSection[]; base_version?: number }) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}`, { method: "PUT", body });

export const approveVideoScript = (id: string, version?: number) =>
  api<VideoScriptDetail>(`/api/video/scripts/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: version ? { version } : {},
  });

export const renderVideoScript = (id: string, voiceId?: string, productionMode?: "standard" | "fast") =>
  api<VideoRender>(`/api/video/scripts/${encodeURIComponent(id)}/render`, {
    method: "POST",
    body: { ...(voiceId ? { voice_id: voiceId } : {}), ...(productionMode ? { production_mode: productionMode } : {}) },
  });

export const revealVideoRender = (id: string, name = "video.mp4") =>
  api<{ path: string; folder: string; opened: boolean }>(`/api/video/renders/${encodeURIComponent(id)}/reveal`, { method: "POST", query: { name } });
export const getVideoRender = (id: string) => api<VideoRender>(`/api/video/renders/${encodeURIComponent(id)}`);

export const getAutoVideoStatus = (signal?: AbortSignal) => api<AutoVideoStatus>("/api/video/autovideo", { signal });

export const restyleVideoScript = (id: string, style: { depth?: string; tone?: string }) =>
  api<VideoScriptSummary>(`/api/video/scripts/${encodeURIComponent(id)}/restyle`, { method: "POST", body: style });

export const getVideoFacts = (id: string, version?: number, signal?: AbortSignal) =>
  api<FactSheet>(`/api/video/scripts/${encodeURIComponent(id)}/facts`, { query: { version }, signal });

export interface BoardStage {
  key: string;
  label: string;
  count: number;
}

export interface BoardAttention {
  kind: "review" | "plan" | "failed" | "render" | "render_done" | "recheck";
  script_id: string;
  title: string;
  column_key: string;
  text: string;
  created_at?: string;
  credibility?: Credibility | null;
}

export interface VideoBoard {
  stages: BoardStage[];
  failed: number;
  attention: BoardAttention[];
  items?: BoardItem[];
  matrices?: { key: string; name: string }[];
  updated_at?: string;
}

export interface BoardItem {
  script_id: string;
  title: string;
  column_key: string;
  column_label: string;
  matrix: string;
  matrix_name: string;
  job: string;
  job_name: string;
  status: ScriptStatus;
  stage: string;
  version: number;
  created_at: string;
  updated_at: string;
  credibility: Credibility | null;
  has_video: boolean;
  has_previous_video: boolean;
  action: "failed" | "plan" | "review" | "recheck" | "approve" | "retry_render" | "render" | "publish" | "working" | "published";
  group: "todo" | "publish" | "working" | "done";
  action_label: string;
  note: string;
}

export const getVideoBoard = (signal?: AbortSignal) => api<VideoBoard>("/api/video/board", { signal });

/** 自动化异常：自己跑的东西出了问题（批次空跑、Job 失败或被反复重启、周计划没触发、情报轮次失败）。 */
export interface AutomationAlert {
  key: string;
  kind: "job_failed" | "job_restarted" | "batch_empty" | "batch_stuck" | "batch_items" | "plan_late" | "round_failed" | "matrix_cap";
  at: number;
  title: string;
  detail: string;
  link: string;
}

export const listAutomationAlerts = () => api<AutomationAlert[]>("/api/video/alerts");

export const ackAutomationAlert = (key: string) =>
  api<{ key: string; acked: boolean }>("/api/video/alerts/ack", { method: "POST", body: { key } });

export interface Shipment {
  matrix?: string | null;
  matrix_name?: string;
  job?: string | null;
  job_name?: string;
  column_label?: string;
  script_id: string;
  title: string;
  column_key: string;
  approved_version: number | null;
  created_at: string;
  credibility?: Credibility | null;
  auto?: boolean;
  render: { id: string; status: string; version: number; error: string | null; completed_at?: string | null } | null;
  video_url: string | null;
  publications: Publication[];
  distributions?: Distribution[];
  warnings: string[];
}

export const listShipments = (signal?: AbortSignal) => api<Shipment[]>("/api/video/shipments", { signal });

export interface AutopilotItem {
  id: string;
  slot: number;
  column_key: string;
  topic: { id: string | null; title: string };
  script_id: string | null;
  render_id: string | null;
  state: "PENDING" | "WORKING" | "HELD" | "DONE" | "FAILED" | "CANCELLED";
  note: string;
  credibility?: Credibility;
  gate: { label?: CredibilityTier; text?: string; approved?: string; reasons?: string[]; ignored?: { must: number; warn: number } };
}

export interface AutopilotRun {
  id: string;
  status: "RUNNING" | "DONE" | "CANCELLED";
  stage: string;
  note: string;
  created_at: string;
  items: AutopilotItem[];
}

export interface AutopilotPlan {
  id: string;
  name: string;
  expression: string;
  next_fire: number | null;
  enabled: number;
  spec?: AutopilotSpec;
}

export interface AutopilotPick {
  cell?: string;
  column?: string;
  source?: string;
  count: number;
}

export interface AutopilotSpec {
  picks: AutopilotPick[];
  refresh?: boolean;
}

export const getAutopilot = () => api<{ plans: AutopilotPlan[]; runs: AutopilotRun[] }>("/api/video/autopilot");
export const startAutopilotRun = (spec?: AutopilotSpec) =>
  api<AutopilotRun>("/api/video/autopilot/runs", { method: "POST", ...(spec ? { body: { spec } } : {}) });
export const setPlanSpec = (id: string, spec: AutopilotSpec) =>
  api<AutopilotPlan>(`/api/video/autopilot/plans/${encodeURIComponent(id)}/spec`, { method: "PUT", body: { spec } });
export const cancelAutopilotRun = (id: string) =>
  api<AutopilotRun>(`/api/video/autopilot/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });

// 文稿配方：config/video_recipes/<key>.yaml，一份文件就是一种文稿的完整做法（spec/video-recipes.md）
export interface VideoRecipe {
  key: string;
  label: string;
  status: CellStatus;
  reason: string;
  error: string;
  usable: boolean;
  cell: string;
  name: string;
  pipeline: "single" | "adapt" | null;
  research_mode: "job" | "none" | null;
  target_minutes: number | null;
  topic_kind: string | null;
  topic_sources: string[];
  // 这份文件给哪些受众各做一个栏目（第一个用文件自己的名字，其余按 tags 拼出）；没写 audiences 的配方是空的
  audiences: { key: string; recipe: string }[];
  // 引用的共用部件：research → 调研模板（jobs/_templates/video_research），output / writing / audience → config/video_parts
  uses: Partial<Record<VideoPartKind, { kind: VideoPartKind; name: string }>>;
  scripts: number;
  mtime: number | null;
  sha256: string;
}

export interface VideoRecipeDetail extends VideoRecipe {
  content: string;
  column: VideoColumn | null;
}

export const listVideoRecipes = () => api<VideoRecipe[]>("/api/video/recipes");
export const getVideoRecipe = (key: string) => api<VideoRecipeDetail>(`/api/video/recipes/${encodeURIComponent(key)}`);
export const validateVideoRecipe = (key: string, content: string) =>
  api<{ ok: boolean; error: string; column: VideoColumn | null }>(`/api/video/recipes/${encodeURIComponent(key)}/validate`, {
    method: "POST",
    body: { content },
  });
export const saveVideoRecipe = (key: string, content: string, expectedMtime?: number | null) =>
  api<VideoRecipeDetail>(`/api/video/recipes/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: { content, expected_mtime: expectedMtime ?? undefined },
  });
export const copyVideoRecipe = (key: string, copyFrom: string) =>
  api<VideoRecipeDetail>("/api/video/recipes", { method: "POST", body: { key, copy_from: copyFrom } });
export const deleteVideoRecipe = (key: string) =>
  api<{ ok: boolean }>(`/api/video/recipes/${encodeURIComponent(key)}`, { method: "DELETE" });

// 共用部件：多个配方引用同一份调研模板或出片设置，改一处、引用它的配方一起变（配方自己写的字段优先）
export type VideoPartKind = "research" | "output" | "writing" | "audience" | "packaging";

export interface VideoPart {
  kind: VideoPartKind;
  kind_label: string;
  name: string;
  label: string;
  path: string;
  used_by: string[];
}

export interface VideoPartDetail extends VideoPart {
  content: string;
  mtime: number;
}

const partUrl = (kind: string, name: string) =>
  `/api/video/recipes/parts/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`;
export const listVideoParts = () => api<VideoPart[]>("/api/video/recipes/parts");
export const getVideoPart = (kind: string, name: string) => api<VideoPartDetail>(partUrl(kind, name));
export const saveVideoPart = (kind: string, name: string, content: string, expectedMtime?: number | null) =>
  api<VideoPartDetail>(partUrl(kind, name), { method: "PUT", body: { content, expected_mtime: expectedMtime ?? undefined } });
export const copyVideoPart = (kind: string, name: string, copyFrom: string) =>
  api<VideoPartDetail>(`/api/video/recipes/parts/${encodeURIComponent(kind)}`, {
    method: "POST",
    body: { name, copy_from: copyFrom },
  });
