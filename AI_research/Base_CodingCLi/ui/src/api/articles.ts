import { api } from "./client";

export interface ArticleImport {
  id: string; text: string; filename: string; chars: number; warnings: string[];
}
export interface ArticleSource extends ArticleImport {
  title: string; author: string; source_url: string; published_on: string; origin: "original" | "copied" | "ai";
  mode: "read" | "adapt"; kind: "literary" | "information"; original_url: string; extracted_text: string;
  edited_after_import: boolean;
}
export interface ArticleCreate {
  title: string; text: string; mode: "read" | "adapt"; kind: "literary" | "information";
  column: string; production: string; import_id?: string; author: string; source_url: string;
  published_on: string; origin: "original" | "copied" | "ai";
}
export function importArticle(file: File) {
  const body = new FormData(); body.append("file", file);
  return api<ArticleImport>("/api/video/articles/import", { method: "POST", body });
}
export const createArticle = (body: ArticleCreate) =>
  api<{ id: string }>("/api/video/articles/scripts", { method: "POST", body });
export const getArticle = (id: string, signal?: AbortSignal) =>
  api<ArticleSource>(`/api/video/scripts/${id}/article`, { signal });
