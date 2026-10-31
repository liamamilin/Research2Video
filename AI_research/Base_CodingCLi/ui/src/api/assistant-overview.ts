import { api } from './client';

export type OverviewView = 'attention' | 'working' | 'publish';
export interface OverviewRow {
  id: string; kind: string; title: string; label: string; detail: string;
  matrix: string; matrix_name: string; href: string; prompt: string;
  version?: number; script_id?: string; render_id?: string;
}
export interface OperationsOverview {
  at: string; date: string; timezone: string; matrix: string; platform: string; active: boolean; href: string;
  platforms: { id: string; name: string; account: { connected: boolean; uid?: string; name?: string } }[];
  matrices: { key: string; name: string; href: string; jobs: number; attention: number | null; working: number | null; publish: number | null }[];
  summary: { attention: number | null; working: number | null; publish: number | null; jobs: number | null;
    enabled_jobs: number; today_success: number | null; today_failed: number | null };
  schedules: { available: boolean; enabled: number | null; paused: number | null; href: string; note: string;
    next: { at: string; job: string; timezone: string; href: string } | null };
  items: { view: OverviewView; rows: OverviewRow[]; total: number; offset: number; next_offset: number | null; complete: boolean };
  warnings: string[]; availability: Record<string, boolean>; scope: string;
}
export const getOperationsOverview = (query: { matrix: string; platform: string; view: OverviewView; offset: number }, signal?: AbortSignal) =>
  api<OperationsOverview>('/api/assistant/overview', { query: { ...query, limit: 15 }, signal });
