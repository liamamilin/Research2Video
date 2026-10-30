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
// as the shipped recipes say: deep dives are picked from the rounds, roundups from the flash reports
Object.assign(CELLS[0], { topic_sources: ["opportunities"] });
Object.assign(CELLS[1], { topic_sources: ["flash"] });

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

async function openCreate() {
  fireEvent.click(await screen.findByText("＋ 新建文稿"));
}

function renderPage() {
  render(
    <MemoryRouter>
      <VideoScriptsPage />
    </MemoryRouter>,
  );
}

describe("Video script cell picker", () => {
  it("allows a topic covered by another cell but blocks a matching cell's active task", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/cells")) return jsonResponse(CELLS);
      if (u.includes("/topics")) return jsonResponse({ round: "2026-10-19", topics: [
        { ...FLASH[0], scripted: true, published: true, scripts: [{ id: "flash-task", column_key: "flash-nr", cell_key: "flash", status: "WRITING", created_at: "2026-10-19" }] },
        { ...FLASH[1], scripted: true, scripts: [{ id: "same-task", column_key: "deep_dive-m8-nr", status: "WRITING", created_at: "2026-10-19" }] },
      ] });
      return jsonResponse([]);
    }));
    render(<MemoryRouter initialEntries={["/video?cell=deep_dive&topic=2026-10-19/F1&topic_source=flash"]}><VideoScriptsPage /></MemoryRouter>);
    const allowed = await screen.findByRole("button", { name: "写这一篇" });
    expect((allowed as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "已有文稿任务" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("marks unverified cells, disables blocked ones and offers the cell's other video forms", async () => {
    renderPage();
    await openCreate();
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
    await openCreate();
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
    await openCreate();
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
    await openCreate();
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
      { id: "a", title: "草稿一篇", status: "DRAFT", column_key: "deep_dive", published: false, updated_at: "2026-10-19T10:00:00+0800", created_at: "2026-10-19T10:00:00+0800", length_check: null, credibility: { tier: "trusted", text: "有出处可信", reasons: [], cited_share: 1 } },
      { id: "b", title: "已发一篇", status: "APPROVED", column_key: "deep_dive", published: true, updated_at: "2026-10-19T10:00:00+0800", length_check: null },
    ];
    const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      String(url).endsWith("/api/video/scripts?compact=true") ? jsonResponse(scripts) : base(url, init)));
    render(
      <MemoryRouter initialEntries={["/video?cell=ai-developer-roundup-long&topic=2026-10-19/F2"]}>
        <VideoScriptsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("草稿一篇")).toBeTruthy();
    expect(screen.getByText("已发一篇")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从未发布到任何平台 1" }));
    expect(screen.queryByText("已发一篇")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "全部 2" }));
    expect(screen.getByText("已发一篇")).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部 2" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /^已发布/ }));
    await waitFor(() => expect(screen.queryByText("草稿一篇")).toBeNull());
    expect(screen.getByText("已发一篇")).toBeTruthy();
  });
});

describe("Script list columns", () => {
  it("names a column made by a production config with the config's name, not its internal key", async () => {
    const scripts = [
      { id: "a", title: "每天的快讯", status: "APPROVED", column_key: "flash-nr", published: false, created_at: "2026-10-22T10:00:00+0800", length_check: null },
    ];
    const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/api/video/scripts?compact=true")) return jsonResponse(scripts);
      if (String(url).endsWith("/api/production")) return jsonResponse([{ key: "flash_daily", name: "快讯 · 每天（不再调研）", recipe: "flash-nr" }]);
      return base(url, init);
    }));
    renderPage();
    const row = (await screen.findByText("每天的快讯")).closest("tr")!;
    await waitFor(() => expect(row.textContent).toContain("快讯 · 每天（不再调研）"));
    expect(row.textContent).not.toContain("flash-nr");
  });
});

describe("Script list stages", () => {
  it("shows how far an approved script got with its video, counts them per stage and groups by day", async () => {
    const at = (day: string) => `${day}T10:00:00+0800`;
    const scripts = [
      { id: "a", title: "出完片的", status: "APPROVED", column_key: "flash", published: false, created_at: at("2026-10-22"), render: { status: "COMPLETED", created_at: at("2026-10-22") } },
      { id: "b", title: "排队出片的", status: "APPROVED", column_key: "flash", published: false, created_at: at("2026-10-21"), render: { status: "RUNNING", created_at: at("2026-10-21") } },
      { id: "c", title: "还没出片的", status: "APPROVED", column_key: "flash", published: false, created_at: at("2026-10-21"), render: null },
    ];
    const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      String(url).endsWith("/api/video/scripts?compact=true") ? jsonResponse(scripts) : base(url, init)));
    renderPage();
    const row = (await screen.findByText("出完片的")).closest("tr")!;
    expect(row.textContent).toContain("成片完成");
    expect(row.textContent).not.toContain("已定稿");
    expect(screen.getByText("排队出片的").closest("tr")!.textContent).toContain("出片中");
    expect(screen.getByText("还没出片的").closest("tr")!.textContent).toContain("已定稿");
    expect(screen.getByRole("button", { name: "成片完成 1" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "成片完成 1" }));
    await waitFor(() => expect(screen.queryByText("还没出片的")).toBeNull());
    expect(screen.getByText("出完片的")).toBeTruthy();
  });
});

describe("Script list filters", () => {
  it("filters by credibility label and by text, shows when each script was made, and clears", async () => {
    const scripts = [
      { id: "a", title: "缓存那一篇", status: "DRAFT", column_key: "flash", published: false, auto: true, created_at: "2020-01-02T09:30:00+0800", updated_at: "2026-10-20T10:00:00+0800", length_check: null, topic: { id: null, title: "缓存" }, credibility: { tier: "serious", text: "有严重问题", reasons: ["事实审计有 1 条严重问题"], cited_share: 0.5 } },
      { id: "b", title: "价格那一篇", status: "DRAFT", column_key: "flash", published: false, created_at: "2026-10-20T09:30:00+0800", updated_at: "2026-10-20T10:00:00+0800", length_check: null, topic: { id: null, title: "价格" }, credibility: { tier: "trusted", text: "有出处可信", reasons: [], cited_share: 1 } },
    ];
    const base = (fetch as unknown as ReturnType<typeof vi.fn>).getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      String(url).endsWith("/api/video/scripts?compact=true") ? jsonResponse(scripts) : base(url, init)));
    render(<MemoryRouter initialEntries={["/video"]}><VideoScriptsPage /></MemoryRouter>);
    expect(await screen.findByText("缓存那一篇")).toBeTruthy();
    expect(screen.getByText(/01-02 09:30|2020-01-02 09:30/)).toBeTruthy(); // the made-at time is written out, not only "x 天前"
    expect(screen.getByText("显示 2 / 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /筛选/ }));
    fireEvent.change(screen.getByLabelText("可信度"), { target: { value: "serious" } });
    await waitFor(() => expect(screen.queryByText("价格那一篇")).toBeNull());
    expect(screen.getByText("显示 1 / 2")).toBeTruthy();
    fireEvent.click(screen.getByText("清除筛选"));
    expect(await screen.findByText("价格那一篇")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索"), { target: { value: "价格" } });
    await waitFor(() => expect(screen.queryByText("缓存那一篇")).toBeNull());
    fireEvent.change(screen.getByLabelText("来源"), { target: { value: "auto" } });
    await waitFor(() => expect(screen.queryByText("价格那一篇")).toBeNull());
  });
});


describe("Script list scope and independent loading", () => {
  const rows = [
    { id: "a", title: "矩阵甲的文稿", status: "DRAFT", matrix: "a", matrix_name: "矩阵甲", column_key: "flash", column_label: "快讯", current_version: 4, has_previous_video: true, created_at: "2026-10-23T10:00:00+0800" },
    { id: "b", title: "矩阵乙的文稿", status: "APPROVED", matrix: "b", matrix_name: "矩阵乙", column_key: "flash", column_label: "快讯", current_version: 1, created_at: "2026-10-23T09:00:00+0800" },
  ];
  it("keeps an explicit all-status link instead of applying the unpublished default", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([
      ...rows, { ...rows[0], id: "published", title: "已经发布的文稿", published: true },
    ])));
    render(<MemoryRouter initialEntries={["/video?status=all"]}><VideoScriptsPage /></MemoryRouter>);
    expect(await screen.findByText("已经发布的文稿")).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部 3" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "从未发布到任何平台 2" }));
    expect(screen.queryByText("已经发布的文稿")).toBeNull();
  });
  it("filters unpublished current versions across stages and keeps matrix counts", async () => {
    const scripts = [...rows,
      { ...rows[0], id: "c", title: "矩阵甲已发布", published: true },
      { ...rows[0], id: "d", title: "矩阵甲成片未发布", status: "APPROVED", published: false, render: { status: "COMPLETED" } },
    ];
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(scripts)));
    render(<MemoryRouter initialEntries={["/video?status=unpublished"]}><VideoScriptsPage /></MemoryRouter>);
    const unpublished = await screen.findByRole("button", { name: "从未发布到任何平台 3" });
    expect(unpublished.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("矩阵甲的文稿")).toBeTruthy();
    expect(screen.getByText("矩阵甲成片未发布")).toBeTruthy();
    expect(screen.queryByText("矩阵甲已发布")).toBeNull();
    fireEvent.change(screen.getByLabelText("文稿矩阵"), { target: { value: "a" } });
    expect(screen.getByRole("button", { name: "从未发布到任何平台 2" })).toBeTruthy();
    expect(screen.queryByText("矩阵乙的文稿")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "已发布 1" }));
    expect(screen.getByText("矩阵甲已发布")).toBeTruthy();
    expect(screen.queryByText("矩阵甲成片未发布")).toBeNull();
  });
  it("loads existing scripts without loading authoring catalogs, and scopes counts by matrix", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push({ url: String(url), method: "GET" });
      return String(url).endsWith("/api/video/scripts?compact=true") ? jsonResponse(rows) : jsonResponse({ error: { message: "配方读取失败", code: "catalog_failed" } }, 400);
    }));
    renderPage();
    expect(await screen.findByText("矩阵甲的文稿")).toBeTruthy();
    expect(calls.map((c) => c.url)).toEqual(["/api/video/scripts?compact=true"]);
    fireEvent.change(screen.getByLabelText("文稿矩阵"), { target: { value: "a" } });
    expect(screen.queryByText("矩阵乙的文稿")).toBeNull();
    expect(screen.getByRole("button", { name: "待审稿 1" })).toBeTruthy();
    fireEvent.click(screen.getByText("＋ 新建文稿"));
    expect(await screen.findByText(/配方读取失败/)).toBeTruthy();
    expect(screen.getByText("矩阵甲的文稿")).toBeTruthy();
  });

  it("uses the selected topic source and report date from the topics page", async () => {
    render(<MemoryRouter initialEntries={["/video?cell=ai-developer-roundup-long&topic=2026-10-01/F1&topic_source=history&topic_round=2026-10-01"]}><VideoScriptsPage /></MemoryRouter>);
    await screen.findByLabelText("格子");
    await waitFor(() => expect(calls.some((c) => c.url.includes("source=history") && c.url.includes("round=2026-10-01"))).toBe(true));
  });
});
