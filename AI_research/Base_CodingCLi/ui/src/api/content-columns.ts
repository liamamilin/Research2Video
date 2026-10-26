import { api } from "./client";
import type { ProductionConfig } from "./production";

export interface ColumnType {
  key: string; name: string; promise: string; structure: string[]; preferred_visuals: string[];
}
export interface ColumnBinding extends ProductionConfig {
  source_matrix: string; source_matrix_name: string; effective_render?: ProductionConfig["render"];
}
export interface ContentColumn {
  key: string; name: string; matrix: string; matrix_name: string; type: string; audience: string; promise: string;
  production_ids: string[]; render_inheritance: "source_compatible" | "content_matrix";
  render: NonNullable<ProductionConfig["render"]>;
  presentation: { strategy?: string; preferred_visuals?: string[] };
  status: "configured" | "planned"; notes: string; revision: string; errors: string[]; usable: boolean;
  bindings: ColumnBinding[]; auto_bindings: number; scripts: number; updated_at: string;
}
export type ContentColumnInput = Pick<ContentColumn, "name" | "matrix" | "type" | "audience" | "promise" |
  "production_ids" | "render_inheritance" | "render" | "presentation" | "status" | "notes"> &
  { key?: string; expected_revision?: string };
export interface ColumnScript {
  id: string; title: string; status: string; version: number; updated_at: string; production: string; legacy_association: boolean;
}
export interface PresentationRecipe {
  id: string; name: string; description: string; recommended_forms?: string[];
  defaults?: { production_mode?: string; video_form?: string };
}
export const listContentColumns = (matrix: string, signal?: AbortSignal) =>
  api<{ types: ColumnType[]; columns: ContentColumn[] }>("/api/content-columns", { query: { matrix }, signal });
export const saveContentColumn = (key: string, body: ContentColumnInput, create = false) =>
  api<ContentColumn>(create ? "/api/content-columns" : `/api/content-columns/${encodeURIComponent(key)}`,
    { method: create ? "POST" : "PUT", body: { ...body, ...(create ? { key } : {}) } });
export const getColumnScripts = (key: string, signal?: AbortSignal) =>
  api<{ total: number; items: ColumnScript[] }>(`/api/content-columns/${encodeURIComponent(key)}/scripts`, { signal });
export const listPresentationRecipes = (signal?: AbortSignal) =>
  api<{ reachable: boolean; recipes: PresentationRecipe[]; error: string }>("/api/presentation-recipes", { signal });
