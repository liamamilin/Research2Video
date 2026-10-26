import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Matrix } from "@/api/matrices";
import { ToastProvider } from "@/lib/toast";
import { ContentColumns } from "./ContentColumns";

const matrix = { key: "ai_media", name: "AI 视频栏目", domain: "AI", keywords: [], jobs: [], members: [], errors: [],
  content: { role: "domain", name: "AI 与科技", audience: "关注 AI 的观众" } } as Matrix;
const column = { key: "model_review", name: "AI 模型横评", matrix: "ai_media", matrix_name: "AI 与科技",
  type: "comparison", audience: "开发者", promise: "讲清模型差异与适用条件", production_ids: ["arena"],
  render_inheritance: "source_compatible", render: {}, presentation: { preferred_visuals: ["截图", "性能图", "价格卡"] },
  status: "configured", notes: "当前写法为报告速览", revision: "revision-1", errors: [], usable: true,
  bindings: [{ key: "arena", name: "模型竞技场", recipe: "report_digest", label: "报告速览", auto: 1,
    source_matrix: "ai_media", job: "ai_media/weekly_model_arena", errors: [] }], auto_bindings: 1, scripts: 2, updated_at: "" };
let calls: { url: string; method: string; body: Record<string, unknown> | null }[];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url); const method = init?.method || "GET";
    calls.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const result = method === "PUT" || method === "POST" ? column : u.includes("/scripts")
      ? { total: 1, items: [{ id: "s1", title: "两个模型怎么选", version: 1, status: "DRAFT", legacy_association: false }] }
      : u.includes("/presentation-recipes") ? { reachable: true, recipes: [{ id: "mixed_tutorial", name: "混合步骤", description: "操作截图与流程组合" }] }
      : u.includes("/api/production") ? [{ ...column.bindings[0], content_column_id: "model_review" }]
      : { types: [{ key: "comparison", name: "横评" }, { key: "tutorial", name: "教程" }], columns: [column] };
    return { ok: true, status: 200, text: async () => JSON.stringify(result) } as Response;
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const mount = (canEdit = true) => render(<MemoryRouter><ToastProvider><ContentColumns matrix={matrix} canEdit={canEdit} /></ToastProvider></MemoryRouter>);

describe("ContentColumns", () => {
  it("separates named column, script recipe and mixed visual preferences", async () => {
    mount();
    expect(await screen.findByText("AI 模型横评")).toBeTruthy();
    expect(screen.getByText("讲清模型差异与适用条件")).toBeTruthy();
    expect(screen.getByText("当前写法：报告速览 · 自动制作")).toBeTruthy();
    expect(screen.getByText("画面偏好：截图、性能图、价格卡")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/presentation-recipes"))).toBe(false);
    expect(calls.some((c) => c.url.includes("/api/production"))).toBe(false);
    expect(screen.getByRole("link", { name: "来源与制作" }).getAttribute("href")).toContain("ai_media%2Fweekly_model_arena");
  });

  it("loads recent content only when expanded and links to the script", async () => {
    mount(); await screen.findByText("AI 模型横评");
    expect(calls.some((c) => c.url.endsWith("/scripts"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "最近内容" }));
    expect((await screen.findByRole("link", { name: "两个模型怎么选" })).getAttribute("href")).toBe("/video/s1");
  });

  it("saves only the business column and makes fast mode a compatible mixed request", async () => {
    mount(); await screen.findByText("AI 模型横评");
    fireEvent.click(screen.getByRole("button", { name: "编辑栏目" }));
    await screen.findByLabelText("制作配方");
    fireEvent.change(screen.getByLabelText("栏目名称"), { target: { value: "模型与成本横评" } });
    fireEvent.change(screen.getByLabelText("制作档位"), { target: { value: "fast" } });
    fireEvent.change(screen.getByLabelText("画面偏好"), { target: { value: "截图、价格卡、流程" } });
    fireEvent.click(screen.getByRole("button", { name: "保存栏目" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const saved = calls.find((c) => c.method === "PUT")!;
    expect(saved.url).toContain("/api/content-columns/model_review");
    expect(saved.body?.expected_revision).toBe("revision-1");
    expect(saved.body?.render).toEqual({ production_mode: "fast", video_form: "information_cards", visual_style: "editorial_light" });
    expect(saved.body?.presentation).toEqual({ preferred_visuals: ["截图", "价格卡", "流程"] });
    expect(calls.some((c) => c.method !== "GET" && c.url.includes("/api/production"))).toBe(false);
  });

  it("reads the engine recipe library only when requested", async () => {
    mount(); await screen.findByText("AI 模型横评");
    fireEvent.click(screen.getByRole("button", { name: "制作配方库" }));
    expect(await screen.findByText("混合步骤")).toBeTruthy();
    expect(screen.getByText("操作截图与流程组合")).toBeTruthy();
    expect(calls.filter((c) => c.url.includes("/presentation-recipes"))).toHaveLength(1);
  });

  it("keeps viewer access read only", async () => {
    mount(false); await screen.findByText("AI 模型横评");
    expect(screen.queryByRole("button", { name: "新增栏目" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑栏目" })).toBeNull();
  });
});
