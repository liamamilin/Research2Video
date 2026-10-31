import { api } from './client';

export interface AssistantContext {
  kind: 'video' | 'job' | 'report' | 'matrix' | 'recipe' | 'part' | 'platform';
  id: string;
}
export interface ConversationSummary { id: string; title: string; updated: number; request_count: number }
export interface PublicationRecovery { kind: 'connection' | 'retry' | 'check' | 'platform'; label: string; href: string; message: string; resumable: boolean }
export interface PublicationProgress { label: string; state: string; active: boolean; needs_attention: boolean; url?: string; recovery?: PublicationRecovery | null }
export interface WorkspaceObject {
  kind: string; id: string; title: string; href: string; status: string; active: boolean;
  detail?: string; error?: string; version?: number; video_version?: number;
  local_video?: boolean;
  links: { label: string; name?: string; href: string; scope?: string; next?: string }[];
  renders?: { id: string; version: number; status: string; active_stage?: string; error_message?: string }[];
  tasks?: { id: string; title?: string; status: string; error?: string }[];
  members?: { job: string; name: string; enabled: boolean; href: string; schedule: { enabled: boolean; schedule: string; timezone: string; next_runs: string[] } | null }[];
  render?: Record<string, unknown>;
}
export interface AssistantWorkspace {
  conversation_id: string; context: AssistantContext | null; active: boolean; objects: WorkspaceObject[];
  operations: { id: string; tool: string; status: string; created: number; label: string; error?: string; href?: string; submitted: boolean; impact?: string }[];
  tasks?: AssistantTask[];
}
export interface AssistantTask {
  batches?: ApplicationBatch[];
  id: string; message: string; created: number; status: string; active: boolean;
  conversation_id?: string;
  runs?: { source: 'manual' | 'scheduled'; job_name: string; run_id: string; title?: string; status: string; state: string; active: boolean;
    label: string; progress?: string; error?: string; href: string; cancel_requested?: boolean;
    report?: { path: string; href: string; verified_file: boolean } | null }[];
  changes: { id: string; label: string; status: string; href?: string; error?: string; impact?: string; fields?: string[]; recovered_by?: string;
    configuration?: { level: string; label: string; scope: string; next: string; existing_outputs: string; used_by: string[];
      changes: { field: string; before: unknown; after: unknown }[] } | null }[];
  videos: {
    script_id: string; title?: string; status: string; label: string; active: boolean; href: string; error?: string; retry_step?: string;
    target_version?: number; before_version?: number; current_version?: number; script_ready?: boolean; render_requested?: boolean;
    render?: { id: string; version: number; status: string; active_stage?: string } | null; previous_video_version?: number;
    report?: { path: string; href: string } | null; recipe?: string; recipe_sha256?: string;
    review?: { state: string; label: string; verdict: string | null };
    duration?: { estimated_seconds?: number; target_minutes?: number; binding: boolean };
    script_href?: string; video_href?: string; compare_href?: string;
  }[];
  preparations?: { platform: string; render_id: string; script_id: string; version: number; account_uid: string; account_name: string;
    fields: { title?: string; description?: string; tags?: string[] }; warnings: string[]; href: string }[];
  publications?: { platform: string; id: string; title?: string; version?: number; status: string; state: string; active: boolean;
    phase?: string; error?: string; href: string; script_id?: string; account_uid?: string; account_name?: string;
    label?: string; recovery?: PublicationRecovery | null; url?: string }[];
  failures?: { render_id?: string; error: string }[];
}
export const listAssistantConversations = () => api<ConversationSummary[]>('/api/assistant/conversations');
export const setAssistantContext = (id: string, context: AssistantContext | null) => api(`/api/assistant/conversations/${id}/context`, { method: 'PUT', body: { context } });
export const getAssistantWorkspace = (id: string, context: AssistantContext | null, signal?: AbortSignal) => api<AssistantWorkspace>(
  '/api/assistant/workspace', { query: { conversation_id: id, context: JSON.stringify(context) }, signal });
export interface AssistantTaskCenter { tasks: AssistantTask[]; active: boolean; total: number; limit: number }
export const getAssistantTasks = (limit = 20, signal?: AbortSignal) => api<AssistantTaskCenter>('/api/assistant/tasks', { query: { limit }, signal });

export interface ApplicationBatch {
  id: string; title: string; kind: string; status: string; active: boolean; created: number; updated: number; href: string;
  scope: { all?: boolean; matrices?: string[]; ids?: string[]; jobs?: string[] }; counts: Record<string, number>;
  items: { id: string; target: string; title: string; stage: string; state: string; error: string;
    result: { href?: string; version?: number; reason?: string; publication?: PublicationProgress; recovery?: PublicationRecovery | null; report?: { path: string; href: string }; scripts?: string[]; render_ids?: string[];
      script_versions?: Record<string, number>; renders?: { id: string; script_id: string; version?: number }[] } }[];
}
export const getApplicationBatches = (signal?: AbortSignal) => api<{ items: ApplicationBatch[] }>('/api/assistant/batches', { signal });
export const manageApplicationBatch = (id: string, action: 'start' | 'retry' | 'cancel') => api('/api/assistant/batches/' + id, { method: 'POST', body: { action } });
export const getAssistantCapabilities = (signal?: AbortSignal) => api<{
  areas: { key: string; title: string; detail: string; example: string }[]; role: string; boundaries: string[];
}>('/api/assistant/capabilities', { signal });
