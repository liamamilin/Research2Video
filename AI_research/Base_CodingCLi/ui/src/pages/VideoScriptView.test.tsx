import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  column: { ...DETAIL.column, writer: "adapt", default_style: { depth: "intermediate", tone: "explainer" }, allowed_tones: ["explainer", "playful"] },
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
      version: 2,
      issues: [
        { audit: "communication", rule: "C4", severity: "must", section: 0, paragraph: 0, quote: "一句话里的原句",
          problem: "一句话里有 4 个数字", why: "听觉记不住", suggestion: "拆开", source: "rule" },
        { audit: "comprehension", rule: "N1", severity: "must", section: 0, paragraph: 0, quote: "",
          problem: "缓存是什么？", why: "", suggestion: "", source: "model" },
        { audit: "fact", rule: "D1", severity: "serious", section: 0, paragraph: 0, quote: "凌晨三点的翻车现场",
          problem: "把假设场景说成真事", why: "观众会当真", suggestion: "删掉", source: "model",
          unhandled: "开场段不自动删除，请人工处理" },
      ],
      quiz: {
        items: [
          { id: "G1", question: "为什么前缀要稳定？", expected: "写入加价", answer: "因为会重写", correct: true, note: "" },
          { id: "G2", question: "断点放在哪？", expected: "稳定和易变的交界", answer: "没听出来", correct: false, note: "没答出" },
        ],
        score: 0.5,
      },
      newcomer: { retell: "前缀要稳", scores: { hook: 5, clarity: 3 }, comments: "" },
      core_message_match: true,
      core_message_note: "",
      model_ran: true,
      summary: {
        serious: 1, must: 2, warn: 0, rule_must: 1, questions: 1, lost: 0, quiz_score: 0.5, low_scores: ["clarity"],
        by_audit: {
          fact: { serious: 1, must: 0, warn: 0 },
          comprehension: { serious: 0, must: 1, warn: 0 },
          communication: { serious: 0, must: 1, warn: 0 },
        },
        passed: false,
      },
      rounds: 2,
      deletions: [{ section: 1, paragraph: 0, quote: "批处理跑了两万次调用", before: "", rule: "D1", reason: "编造的次数" }],
      initial: [],
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
    if (String(url).includes("/publish")) {
      return jsonResponse({ title: "缓存前缀纪律", description: "", chapters: [], chapter_note: "", tags: [],
        cover: { headline: "缓存前缀纪律", badge: "深度解读" }, references: [], warnings: [],
        version: Number(new URL(String(url), "http://localhost").searchParams.get("version")) || 1 });
    }
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
    if (method === "POST" && String(url).endsWith("/render")) {
      return jsonResponse({ id: "fast-render", script_id: "s1", version: 1, status: "SUBMITTING", title: "快速卡片", artifacts: [],
        created_at: "2026-10-23T17:00:00+0800", updated_at: "2026-10-23T17:00:00+0800", autovideo_url: null });
    }
    if (String(url).includes("/api/video/autovideo")) {
      return jsonResponse({ base_url: "x", reachable: true, ready: true, blockers: [], voices: [], error: null });
    }
    return jsonResponse(detail);
  }));
}

function renderPage(path = "/video/s1") {
  render(
    <MemoryRouter initialEntries={[path]}>
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
  it("shows an archived duplicate as a skip with original links instead of a failure", async () => {
    mockApi({ ...DETAIL, status: "FAILED", archived: 1, current_version: 0, version_shown: null, versions: [],
      error: "主事件已经讲过", selection_skip: { kind: "duplicate", reason: "主事件已经讲过", originals: [
        { id: "original", title: "已完成的视频", video_url: "/original.mp4", render_state: "done" },
      ] } });
    renderPage("/video/s1?return_to=%2Ftopics%3Ftask%3Dskipped");
    await screen.findByText("重复选题，已跳过");
    expect(screen.queryByText("没有完成")).toBeNull();
    expect(screen.getByRole("link", { name: "已有文稿：已完成的视频" }).getAttribute("href")).toContain("/video/original?");
    expect(screen.getByRole("link", { name: "查看已有成片" }).getAttribute("href")).toContain("tab=video");
    expect(screen.getByLabelText("下一步").textContent).toContain("重复选题已跳过");
    expect(calls.some(c => c.method === "POST")).toBe(false);
  });
  it("keeps the topic return destination while changing tabs and rejects unrelated return links", async () => {
    const back = "/topics?cell=flash&source=flash&round=2026-10-19&task=draft#topic-F1";
    renderPage(`/video/s1?return_to=${encodeURIComponent(back)}`);
    const link = await screen.findByRole("link", { name: "← 返回选题，继续处理" });
    expect(link.getAttribute("href")).toBe(back);
    fireEvent.click(screen.getByRole("tab", { name: "审校" }));
    expect(screen.getByRole("link", { name: "← 返回选题，继续处理" }).getAttribute("href")).toBe(back);
    cleanup();
    renderPage("/video/s1?return_to=https%3A%2F%2Fevil.test");
    expect((await screen.findByRole("link", { name: "← 视频文稿" })).getAttribute("href")).toBe("/video");
  });
  it("refreshes an idle draft and follows automatic approval without a page reload", async () => {
    const timers = vi.spyOn(window, "setInterval");
    try {
      renderPage();
      await screen.findByText("一个时间戳让缓存折扣失效");
      const refresh = timers.mock.calls.find(([, ms]) => ms === 30000)?.[0];
      expect(typeof refresh).toBe("function");
      mockApi({ ...DETAIL, status: "APPROVED", approved_version: 1,
        renders: [{ id: "auto-render", version: 1, status: "RUNNING", created_at: DETAIL.created_at, files: [] }] });
      await act(async () => { if (typeof refresh === "function") refresh(); });
      await screen.findByRole("tab", { name: "成片" });
      await waitFor(() => expect(timers.mock.calls.some(([, ms]) => ms === 5000)).toBe(true));
      expect(calls.every(call => call.method === "GET")).toBe(true);
    } finally { timers.mockRestore(); }
  });

  it('shows an imported local video with playback and publishing, without generation or AutoVideo dependency', async () => {
    mockApi({ ...DETAIL, status: 'APPROVED', approved_version: 1, column: null,
      topic: { title: '演示视频', id: null, local_video: { file_name: '演示视频.mp4', duration: 12, width: 1920, height: 1080, size: 2000 } },
      version_shown: { ...DETAIL.version_shown, sections: [], meta: { local_video: true } },
      renders: [{ id: 'r-local', version: 1, status: 'COMPLETED', created_at: '2026-10-27T09:00:00+08:00',
        files: [{ name: 'video.mp4', url: '/api/video/renders/r-local/files/video.mp4', size: 2000 }] }] });
    renderPage('/video/s1?tab=video');
    expect(await screen.findByRole('heading', { name: '本地导入视频' })).toBeTruthy();
    expect(screen.getByText('演示视频.mp4')).toBeTruthy();
    expect(screen.getByRole('link', { name: '下载成片' }).getAttribute('href')).toContain('r-local');
    expect(screen.queryByRole('tab', { name: '稿件' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '审校' })).toBeNull();
    expect(screen.queryByText('重新生成文稿')).toBeNull();
    expect(screen.queryByText('快速卡片出片')).toBeNull();
    expect(calls.some(c => c.url.includes('/autovideo'))).toBe(false);
    expect(calls.every(c => c.method === 'GET')).toBe(true);
  });
  it("keeps polling a social UNKNOWN task and receives its automatic publication record", async () => {
    const distribution={platform:"kuaishou",script_id:"s1",version:1,account_uid:"owner",account_name:"测试作者",connected:true,current_account:true,status:"UNKNOWN",can_prepare:false,blocked_reason:"",draft:null,publications:[],task:{id:"task",status:"UNKNOWN",error:"",work_id:"",render_id:"r",updated_at:1}};
    mockApi({...DETAIL,distributions:[distribution]});
    const timers=vi.spyOn(window,"setInterval");
    try {
      renderPage();
      await screen.findByText("一个时间戳让缓存折扣失效");
      await waitFor(() => expect(timers.mock.calls.some(([,milliseconds]) => milliseconds === 5000)).toBe(true));
      const refresh=timers.mock.calls.find(([,milliseconds]) => milliseconds===5000)?.[0];
      expect(typeof refresh).toBe("function");
      mockApi({...DETAIL,version_shown:{...DETAIL.version_shown,title:"已收到自动发布回执"},distributions:[{...distribution,status:"PUBLISHED",task:{...distribution.task,status:"PUBLISHED",work_id:"3xexactwork"},publications:[{id:"pub",version:1,platform:"kuaishou",account_uid:"owner",url:"https://www.kuaishou.com/short-video/3xexactwork",confirmed_at:"2026-10-25T17:00:00+0800"}]}]});
      await act(async () => {if(typeof refresh==="function") refresh();});
      await screen.findByText("已收到自动发布回执");
      expect(calls.every(call => call.method==="GET")).toBe(true);
    } finally {timers.mockRestore();}
  });
  it("starts rewriting from the prominent entry only after its scope is accepted", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    renderPage();
    const button = await screen.findByRole("button", { name: "重新生成文稿" });
    fireEvent.click(button);
    expect(calls.some(c => c.method === "POST")).toBe(false);
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(button);
    await waitFor(() => expect(calls.some(c => c.url.endsWith("/steps/write/rerun") && c.method === "POST")).toBe(true));
    expect(calls.some(c => c.url.endsWith("/render") && c.method === "POST")).toBe(false);
  });
  it("submits the approved manuscript as fast cards only when explicitly selected", async () => {
    mockApi({ ...DETAIL, status: "APPROVED", approved_version: 1 });
    renderPage("/video/s1?tab=render");
    const button = await screen.findByRole("button", { name: "快速卡片出片" });
    fireEvent.click(button);
    await waitFor(() => expect(calls.some(c => c.url.endsWith("/render") && c.method === "POST")).toBe(true));
    expect(calls.find(c => c.url.endsWith("/render") && c.method === "POST")?.body).toEqual({ production_mode: "fast" });
  });

  it("shows each paragraph's evidence, citations and the length check", async () => {
    renderPage();
    await screen.findByText("一个时间戳让缓存折扣失效");
    expect(screen.getByText("有出处")).toBeTruthy();
    expect(screen.getByText("无引用")).toBeTruthy();
    expect(screen.getByText("需核实")).toBeTruthy();
    expect(screen.getByText("docs.example.com")).toBeTruthy();
    expect(screen.getByText("接近参考时长（比目标短 3%）")).toBeTruthy();
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
    expect(screen.getByLabelText("下一步").textContent).toContain("定稿");
    expect(screen.getByText("一句话里有 4 个数字")).toBeTruthy();
    expect(screen.getByText("缓存是什么？")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("F3").getAttribute("title")).toContain("比不开缓存贵 25%"));
    const tone = await screen.findByLabelText("语气");
    expect(Array.from((tone as HTMLSelectElement).options).map((o) => o.value)).toEqual(["explainer", "playful"]);
    fireEvent.change(tone, { target: { value: "playful" } });
    fireEvent.click(screen.getByText("用这个风格重写"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/restyle"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/restyle"))!.body).toEqual({ depth: "intermediate", tone: "playful" });
    fireEvent.click(screen.getByRole("tab", { name: "审校" }));
    expect(screen.getByText("未通过")).toBeTruthy();
    expect(screen.getByText("自动修改 2 轮")).toBeTruthy();
    expect(screen.getByText("需要你处理的严重问题")).toBeTruthy();
    expect(screen.getAllByText("开场段不自动删除，请人工处理").length).toBeGreaterThan(0);
    expect(screen.getByText("批处理跑了两万次调用")).toBeTruthy();
    expect(screen.getByText("测验 1/2")).toBeTruthy();
    expect(screen.getByText("前缀稳不稳决定缓存省不省钱")).toBeTruthy();
  });

  it("loads draft-only styles and fact labels when entering the draft tab", async () => {
    mockApi(ADAPTED);
    renderPage("/video/s1?tab=review");
    await screen.findByRole("tab", { name: "审校", selected: true });
    expect(calls.some(c => /\/styles|\/facts/.test(c.url))).toBe(false);
    fireEvent.click(screen.getByRole("tab", { name: "稿件" }));
    await screen.findByLabelText("语气");
    await waitFor(() => expect(screen.getByText("F3").getAttribute("title")).toContain("比不开缓存贵 25%"));
    expect(calls.filter(c => c.url.includes("/styles"))).toHaveLength(1);
    expect(calls.filter(c => c.url.includes("/facts"))).toHaveLength(1);
  });
});


describe("Script version context", () => {
  const oldRender = { id: "old", version: 1, status: "COMPLETED", title: "旧版标题",
    files: [{ name: "video.mp4", url: "/old.mp4", bytes: 1000 }], created_at: DETAIL.created_at };

  it("opens the precise render linked by an assistant task card", async () => {
    mockApi({ ...DETAIL, status: "APPROVED", approved_version: 1,
      renders: [{ ...oldRender, id: "new-attempt", files: [{ name: "video.mp4", url: "/new.mp4" }] }, oldRender] });
    renderPage("/video/s1?tab=video&version=1&render=old");
    expect(await screen.findByLabelText("查看成片记录")).toHaveProperty("value", "old");
    expect(screen.getByRole("link", { name: "下载成片" }).getAttribute("href")).toBe("/old.mp4");
  });

  it("shows the published label and automatically written publication on the document", async () => {
    mockApi({ ...DETAIL, status: "APPROVED", approved_version: 1, renders: [oldRender], publications: [
      { id: "p1", version: 1, url: "https://www.bilibili.com/video/BV1xx411c7mD", published_on: "2026-10-24", recorded_by: "platform-manager" },
    ] });
    renderPage("/video/s1?tab=publish");
    expect(await screen.findByText("已发布至 1 个平台")).toBeTruthy();
    expect(await screen.findByText("平台自动确认")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "投稿到哔哩哔哩" })).toBeNull();
  });

  it("restores the selected historical publish version from the platform return link", async () => {
    mockApi({ ...DETAIL, current_version: 2, version_shown: { ...DETAIL.version_shown, version: 2 },
      renders: [{ ...oldRender, id: "new", version: 2 }, oldRender] });
    renderPage("/video/s1?tab=publish&publish_version=1");
    expect(await screen.findByLabelText("选择发布版本")).toHaveProperty("value", "1");
    expect(screen.getByRole("link", { name: "前往平台管理" }).getAttribute("href")).toContain("render=old");
  });

  it.each(["DRAFT", "WRITING", "FAILED"])("keeps publication available for an older video while current status is %s", async (status) => {
    mockApi({ ...DETAIL, status, stage: "改写后重新审计", current_version: 2, approved_version: 1,
      version_shown: { ...DETAIL.version_shown, version: 2 }, renders: [oldRender] });
    renderPage("/video/s1?tab=publish");
    expect(await screen.findByRole("tab", { name: "发布" })).toBeTruthy();
    expect(await screen.findByText("这是旧版 v1 的成片和发布包；当前文稿 v2 尚未用于此视频。")).toBeTruthy();
    expect(screen.getByText("旧版标题")).toBeTruthy();
    expect(screen.getByRole("button", { name: "记录发布" })).toBeTruthy();
    expect(calls.some(c => c.url.includes("/publish?version=1"))).toBe(true);
    expect(calls.some(c => c.url.includes("/publish?version=2"))).toBe(false);
    fireEvent.change(screen.getByLabelText("补录视频链接"), { target: { value: "https://b23.tv/abcdef" } });
    fireEvent.click(screen.getByRole("button", { name: "记录发布" }));
    await waitFor(() => expect(calls.find(c => c.url.endsWith("/publications") && c.method === "POST")?.body)
      .toEqual({ url: "https://b23.tv/abcdef", version: 1, render_id: "old", platform: "bilibili", account_uid: "" }));
  });

  it("opens the publication pack from the selected completed video and switches versions explicitly", async () => {
    mockApi({ ...DETAIL, current_version: 3, approved_version: 2,
      version_shown: { ...DETAIL.version_shown, version: 3 },
      renders: [{ ...oldRender, id: "newer", version: 2, title: "第二版标题" }, oldRender] });
    renderPage("/video/s1?tab=video");
    fireEvent.change(await screen.findByLabelText("查看成片记录"), { target: { value: "old" } });
    fireEvent.click(screen.getByRole("button", { name: "发布 v1：查看发布包与记录链接" }));
    expect(await screen.findByLabelText("选择发布版本")).toHaveProperty("value", "1");
    await screen.findByRole("button", { name: "记录发布" });
    fireEvent.change(screen.getByLabelText("选择发布版本"), { target: { value: "2" } });
    await waitFor(() => expect(calls.some(c => c.url.includes("/publish?version=2"))).toBe(true));
    expect(screen.getByText("第二版标题")).toBeTruthy();
  });

  it("does not offer a publish tab for a draft whose only render failed", async () => {
    mockApi({ ...DETAIL, renders: [{ ...oldRender, status: "FAILED" }] });
    renderPage("/video/s1?tab=video");
    await screen.findByRole("tab", { name: "成片" });
    expect(screen.queryByRole("tab", { name: "发布" })).toBeNull();
  });

  it("shows measured total and copy time for a completed render", async () => {
    mockApi({ ...DETAIL, status: "APPROVED", approved_version: 1,
      renders: [{ id: "measured", version: 1, status: "COMPLETED", files: [{ name: "video.mp4", url: "/measured.mp4" }],
        created_at: DETAIL.created_at, timings: { elapsed_seconds: 314, download_seconds: .107 } }] });
    renderPage("/video/s1?tab=video");
    expect(await screen.findByText(/总耗时 314 秒/)).toBeTruthy();
    expect(screen.getByText(/文件回传 0.11 秒/)).toBeTruthy();
  });

  it("shows historical versions read-only with an explicit return to current", async () => {
    mockApi({ ...DETAIL, current_version: 2, versions: [{ ...DETAIL.version_shown, version: 2 }, DETAIL.version_shown] });
    renderPage("/video/s1?version=1");
    expect(await screen.findByText("历史版本（只读）")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "定稿 v1" })).toBeNull();
    expect(screen.getByRole("button", { name: "返回当前 v2" })).toBeTruthy();
  });

  it("labels an older video and does not call it the current draft's progress", async () => {
    mockApi({ ...DETAIL, current_version: 2, approved_version: 1,
      version_shown: { ...DETAIL.version_shown, version: 2 },
      renders: [{ id: "old", version: 1, status: "COMPLETED", files: [{ name: "video.mp4", url: "/old.mp4" }], created_at: DETAIL.created_at }] });
    renderPage("/video/s1?tab=video");
    expect(await screen.findByText("旧版 v1 成片，当前文稿为 v2")).toBeTruthy();
    expect(screen.getByLabelText("下一步").textContent).not.toContain("成片完成");
  });
});
