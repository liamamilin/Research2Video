import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { VideoScriptsPage } from "./VideoScripts";

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
}

function cell(id: string, key: string | null, status: string, label: string, type: string, extra: object = {}) {
  return {
    id,
    key,
    status,
    reason: status === "blocked" ? "竖屏需要新的画面版本" : "",
    usable: status !== "blocked",
    label,
    parts: { type: { key: type, name: label, version: 1 } },
    column: key
      ? {
          key,
          name: label,
          description: `${label}的说明`,
          target_minutes: 4,
          video_form: "information_motion",
          allowed_forms: ["information_motion"],
          topic_kind: "single",
          ...extra,
        }
      : null,
  };
}

const CELLS = [
  cell("deep_dive", "deep_dive", "enabled", "深度解读 · 开发者", "deep_dive", {
    allowed_forms: ["auto", "information_motion", "process_diagram"],
    video_form: "auto",
  }),
  cell("ai-developer-roundup-long", "ai-developer-roundup-long", "experimental", "盘点 · 开发者", "roundup", {
    topic_kind: "roundup",
  }),
  cell("ai-developer-comparison-long", "ai-developer-comparison-long", "experimental", "对比 · 开发者", "comparison", {
    topic_kind: "comparison",
  }),
  cell("ai-developer-quick_take-vertical", null, "blocked", "快讯 · 竖屏", "quick_take"),
];

const FLASH = [
  { id: "2026-10-19/F1", round: "2026-10-19", code: "F1", title: "价格下调", fields: {}, score: 8, scripted: false },
  { id: "2026-10-19/F2", round: "2026-10-19", code: "F2", title: "接口弃用", fields: {}, score: 7, scripted: false },
];

let calls: { url: string; method: string; body?: any }[] = [];

beforeEach(() => {
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (String(url).includes("/api/video/cells")) return jsonResponse(CELLS);
      if (String(url).includes("/api/video/topics")) {
        return jsonResponse({ round: "2026-10-19", topics: String(url).includes("source=flash") ? FLASH : [] });
      }
      if (String(url).includes("/api/video/quality")) return jsonResponse({ columns: {}, scripts: [] });
      if (method === "POST") return jsonResponse({ id: "new1" }, 202);
      return jsonResponse([]);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage() {
  render(
    <MemoryRouter>
      <VideoScriptsPage />
    </MemoryRouter>,
  );
}

describe("Video script cell picker", () => {
  it("marks unverified cells, disables blocked ones and offers the cell's other video forms", async () => {
    renderPage();
    const picker = (await screen.findByLabelText("格子")) as HTMLSelectElement;
    const options = Array.from(picker.options);
    expect(options.map((o) => o.text)).toEqual([
      "深度解读 · 开发者",
      "盘点 · 开发者（未验证）",
      "对比 · 开发者（未验证）",
      "快讯 · 竖屏（不可用）",
    ]);
    expect(options[3].disabled).toBe(true);
    const forms = screen.getByLabelText("视频形式") as HTMLSelectElement;
    expect(Array.from(forms.options).map((o) => o.text)).toEqual(["视频形式：默认（自动选择）", "信息动效", "流程图解"]);
  });

  it("sends the chosen cell's key and video form", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("手写选题"));
    fireEvent.change(screen.getByLabelText("视频形式"), { target: { value: "process_diagram" } });
    fireEvent.change(screen.getByPlaceholderText(/选题（必填）/), { target: { value: "一个手写的选题标题" } });
    fireEvent.click(screen.getByText("开始研究"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({ column: "deep_dive", video_form: "process_diagram", topic: { title: "一个手写的选题标题" } });
  });

  it("builds a roundup from flash items in the order they were ticked", async () => {
    renderPage();
    fireEvent.change(await screen.findByLabelText("格子"), { target: { value: "ai-developer-roundup-long" } });
    expect(await screen.findByText("这个格子还没出过片，写出的文稿和视频效果没有验证过。")).toBeTruthy();
    fireEvent.click(await screen.findByLabelText(/F2/));
    fireEvent.click(screen.getByLabelText(/F1/));
    const write = screen.getByText("写这一期盘点") as HTMLButtonElement;
    expect(write.disabled).toBe(false);
    fireEvent.click(write);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      column: "ai-developer-roundup-long",
      topic_ids: ["2026-10-19/F2", "2026-10-19/F1"],
    });
  });

  it("asks a comparison for two different subjects and what to compare them on", async () => {
    renderPage();
    fireEvent.change(await screen.findByLabelText("格子"), { target: { value: "ai-developer-comparison-long" } });
    const go = (await screen.findByText("开始研究")) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("对象一（必填）"), { target: { value: "Claude Code" } });
    fireEvent.change(screen.getByPlaceholderText("对象二（必填）"), { target: { value: "Claude Code" } });
    expect(go.disabled).toBe(true); // the same thing twice is no comparison
    fireEvent.change(screen.getByPlaceholderText("对象二（必填）"), { target: { value: "Codex CLI" } });
    fireEvent.change(screen.getByPlaceholderText(/对比维度/), { target: { value: "价格" } });
    fireEvent.click(go);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({
      column: "ai-developer-comparison-long",
      topic: { subjects: ["Claude Code", "Codex CLI"], dimensions: "价格" },
    });
  });

  it("filters the script list by status and keeps the topic from the topics page highlighted", async () => {
    const scripts = [
      { id: "a", title: "草稿一篇", status: "DRAFT", column_key: "deep_dive", published: false, updated_at: "2026-10-19T10:00:00+0800", length_check: null },
      { id: "b", title: "已发一篇", status: "APPROVED", column_key: "deep_dive", published: true, updated_at: "2026-10-19T10:00:00+0800", length_check: null },
    ];
    const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      String(url).endsWith("/api/video/scripts") ? jsonResponse(scripts) : base(url, init)));
    render(
      <MemoryRouter initialEntries={["/video?cell=ai-developer-roundup-long&topic=2026-10-19/F2"]}>
        <VideoScriptsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("草稿一篇")).toBeTruthy();
    expect(screen.getByText("已发一篇")).toBeTruthy();
    fireEvent.click(screen.getByText("已发布", { selector: "button" }));
    await waitFor(() => expect(screen.queryByText("草稿一篇")).toBeNull());
    expect(screen.getByText("已发一篇")).toBeTruthy();
  });
});
