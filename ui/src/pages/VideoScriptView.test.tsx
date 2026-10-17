import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { VideoScriptViewPage } from "./VideoScriptView";

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
}

const SECTIONS = [
  {
    title: "开场",
    paragraphs: [
      { text: "一个时间戳就能让缓存折扣失效。", citations: [], evidence: "UNCITED" },
      { text: "缓存读取按九折之一计价。", citations: ["https://docs.example.com/caching"], evidence: "CITED" },
    ],
  },
  {
    title: "还不确定的",
    paragraphs: [{ text: "命中率的口径没有官方定义。", citations: [], evidence: "NEEDS_REVIEW", note: "去掉了 1 个不在报告可信来源里的引用" }],
  },
];

const DETAIL = {
  id: "s1",
  column_key: "deep_dive",
  topic: { id: "2026-09-29/C4", title: "缓存前缀纪律" },
  status: "DRAFT",
  research_job: "video/deep_dive_s1",
  report_path: "output/video/r.md",
  current_version: 1,
  approved_version: null,
  error: null,
  created_by: "editor",
  created_at: "2026-10-17T14:00:00+0800",
  updated_at: "2026-10-17T14:05:00+0800",
  title: "缓存前缀纪律",
  length_check: null,
  column: { key: "deep_dive", name: "深度解读", description: "", target_minutes: 6, video_form: "auto" },
  version_shown: {
    script_id: "s1",
    version: 1,
    title: "一个时间戳让缓存折扣失效",
    sections: SECTIONS,
    length_check: {
      spoken_length: 1500, estimated_seconds: 349, target_minutes: 6, budget: 1548, ratio: 0.97, status: "OK",
      sections: [{ title: "开场", spoken_length: 800, budget: 774 }, { title: "还不确定的", spoken_length: 700, budget: 774 }],
      dropped_citations: 1,
    },
    edit_type: "GENERATED",
    meta: { problems: [] },
    created_by: "editor",
    created_at: "2026-10-17T14:05:00+0800",
  },
  versions: [],
  renders: [],
};

const CATALOG = {
  depths: [
    { key: "intro", name: "入门", version: 1, audience: "" },
    { key: "intermediate", name: "进阶", version: 1, audience: "" },
  ],
  tones: [
    { key: "explainer", name: "讲解通俗", version: 1, guidance: "" },
    { key: "playful", name: "风趣口语", version: 1, guidance: "" },
    { key: "news", name: "新闻播报", version: 1, guidance: "" },
  ],
};

const ADAPTED = {
  ...DETAIL,
  column: { ...DETAIL.column, default_style: { depth: "intermediate", tone: "explainer" }, allowed_tones: ["explainer", "playful"] },
  version_shown: {
    ...DETAIL.version_shown,
    sections: [
      {
        title: "开场",
        paragraphs: [
          { text: "开了缓存，账单反而更贵。", citations: [], evidence: "CITED", facts: ["F3"], introduces: [], recap: false },
        ],
      },
    ],
    meta: {
      problems: [],
      writer: "adapt",
      fact_sheet_id: "sheet1",
      style: { depth: "intermediate", tone: "explainer", label: "进阶 · 讲解通俗", presets: [] },
      plan: {
        audience: { knows: ["用过 API"], lacks: ["缓存怎么收费"], cares: "账单", takeaway: "检查前缀" },
        core_message: "前缀稳不稳决定缓存省不省钱",
        hook: { kind: "反常识", idea: "开了缓存反而更贵", facts: ["F3"] },
        glossary: [],
        beats: [{ title: "开场", role: "钩子", point: "一个时间戳", facts: ["F3"], terms: [], budget: 1548 }],
        left_out: [],
      },
    },
    checks: {
      rules: [{ rule: "C4", severity: "must", section: 0, paragraph: 0, detail: "一句话里有 4 个数字" }],
      newcomer: {
        paragraphs: { S1P1: { questions: ["缓存是什么？"], lost: [] } },
        retell: "前缀要稳",
        scores: { hook: 5, clarity: 3 },
        comments: "",
      },
      fidelity: { issues: [], core_message_match: true, core_message_note: "" },
      summary: { must: 1, warn: 0, questions: 1, lost: 0, fidelity: 0, low_scores: ["clarity"], core_message_mismatch: false, passed: false },
      rounds: 2,
    },
  },
};

let calls: { url: string; method: string; body?: unknown }[] = [];

function mockApi(detail: unknown = DETAIL) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (String(url).includes("/api/video/styles")) return jsonResponse(CATALOG);
    if (String(url).includes("/facts")) {
      return jsonResponse({ id: "sheet1", report_path: "", question: "", answer: "", concepts: [], facts: [
        { id: "F3", claim: "前缀每次被打断时比不开缓存贵 25%", confidence: "推导", sources: [], caveat: "", derived_from: [], formula: "1.25 − 1" },
      ] });
    }
    if (String(url).includes("/restyle")) return jsonResponse({ ...DETAIL, status: "WRITING", stage: "排队" }, 202);
    if (String(url).includes("/approve")) return jsonResponse({ ...DETAIL, status: "APPROVED", approved_version: 1 });
    if (method === "PUT") {
      const body = JSON.parse(String(init?.body));
      return jsonResponse({ ...DETAIL, current_version: 2, version_shown: { ...DETAIL.version_shown, version: 2, sections: body.sections, edit_type: "EDITED" } });
    }
    if (String(url).includes("/api/video/autovideo")) {
      return jsonResponse({ base_url: "x", reachable: true, ready: true, blockers: [], voices: [], error: null });
    }
    return jsonResponse(detail);
  }));
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/video/s1"]}>
      <Routes>
        <Route path="/video/:id" element={<VideoScriptViewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  mockApi();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Video script review", () => {
  it("shows each paragraph's evidence, citations and the length check", async () => {
    renderPage();
    await screen.findByText("一个时间戳让缓存折扣失效");
    expect(screen.getByText("有出处")).toBeTruthy();
    expect(screen.getByText("无引用")).toBeTruthy();
    expect(screen.getByText("需核实")).toBeTruthy();
    expect(screen.getByText("docs.example.com")).toBeTruthy();
    expect(screen.getByText("时长达标")).toBeTruthy();
    expect(screen.getByText("去掉了 1 个不可信引用")).toBeTruthy();
  });

  it("saves an edit as a new version", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("编辑"));
    const box = screen.getByDisplayValue("一个时间戳就能让缓存折扣失效。");
    fireEvent.change(box, { target: { value: "改过的开场。" } });
    fireEvent.click(screen.getByText("保存为新版本"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect((put.body as { sections: typeof SECTIONS }).sections[0].paragraphs[0].text).toBe("改过的开场。");
  });

  it("approves the version on screen", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("定稿 v1"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/approve"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/approve"))!.body).toEqual({ version: 1 });
    expect(await screen.findByText("用定稿 v1 出片")).toBeTruthy();
  });

  it("shows the style, the review results and restyles from the same research", async () => {
    mockApi(ADAPTED);
    renderPage();
    expect(await screen.findByText("进阶 · 讲解通俗")).toBeTruthy();
    expect(screen.getByText("未通过")).toBeTruthy();
    expect(screen.getByText("一句话里有 4 个数字")).toBeTruthy();
    expect(screen.getByText("新观众会问：缓存是什么？")).toBeTruthy();
    expect(screen.getByText("自动修改 2 轮")).toBeTruthy();
    expect(screen.getByText("前缀稳不稳决定缓存省不省钱")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("F3").getAttribute("title")).toContain("比不开缓存贵 25%"));

    const tone = await screen.findByLabelText("语气");
    expect(Array.from((tone as HTMLSelectElement).options).map((o) => o.value)).toEqual(["explainer", "playful"]);
    fireEvent.change(tone, { target: { value: "playful" } });
    fireEvent.click(screen.getByText("用这个风格重写"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/restyle"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/restyle"))!.body).toEqual({ depth: "intermediate", tone: "playful" });
  });
});
