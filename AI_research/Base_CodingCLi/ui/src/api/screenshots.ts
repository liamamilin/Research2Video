import { api } from "./client";

export type ScreenshotMode = "viewport" | "full_page" | "element" | "crop";
export type MaterialRole = "reference" | "overall" | "detail" | "comparison";
export type MaterialForm = "object_showcase" | "dynamic_collage";
export interface Placement { chapter_index: number; paragraph_index: number }
export interface ScreenshotAsset {
  id: string; version: number; image_id: string | null; preview_url: string;
  url: string; final_url: string; title: string; captured_at: string;
  width: number; height: number; mode: ScreenshotMode; selector: string;
  clip: { x: number; y: number; width: number; height: number } | null;
}
export interface ScreenshotMaterial {
  screenshot_id: string; caption: string; role: MaterialRole; placements: Placement[];
}
export interface ScreenshotSelection { form: MaterialForm; materials: ScreenshotMaterial[]; origin?: "manual" | "automatic" }
export interface AutomaticVisuals {
  state: "pending" | "running" | "completed" | "degraded" | "manual" | "skipped";
  reason: string; images: number; needs: number; accepted: number; text: number;
  decisions?: { need_id: string; status: string; reason: string; screenshot_id?: string; cached?: boolean;
    understanding?: { summary: string; paragraph_ids: string[]; focus_reason: string } }[];
}
export interface ScreenshotOverview {
  sources: { url: string; title: string }[]; assets: ScreenshotAsset[];
  chapters: { title: string; paragraphs: string[] }[]; selection: ScreenshotSelection; problem: string;
  automation_enabled?: boolean; automatic?: AutomaticVisuals | null;
}
const base = (id: string) => `/api/video/scripts/${encodeURIComponent(id)}`;
export const getScreenshots = (id: string, version: number) =>
  api<ScreenshotOverview>(`${base(id)}/screenshots`, { query: { version } });
export const captureScreenshot = (id: string, body: {
  version: number; url: string; mode: ScreenshotMode; selector?: string;
  clip?: { x: number; y: number; width: number; height: number };
}) => api<{ asset: ScreenshotAsset; problem: string }>(`${base(id)}/screenshots`, { method: "POST", body });
export const registerScreenshot = (id: string, version: number, assetId: string) =>
  api<ScreenshotAsset>(`${base(id)}/screenshots/${encodeURIComponent(assetId)}/register`, { method: "POST", query: { version } });
export const saveScreenshotSelection = (id: string, version: number, selection: ScreenshotSelection) =>
  api<ScreenshotSelection>(`${base(id)}/image-materials`, { method: "PUT", body: { version, form: selection.form,
    materials: selection.materials.map(({ screenshot_id, caption, role, placements }) => ({ screenshot_id, caption, role, placements })) } });
