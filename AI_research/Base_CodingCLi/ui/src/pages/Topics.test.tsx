import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { TopicsPage } from "./Topics";

const TOPICS = {
  round: "2026-10-19",
  rounds: ["2026-10-19", "2026-10-18"],
  topics: [
    { id: "2026-10-19/F1", round: "2026-10-19", code: "F1", title: "Gemini 4 Argon 上线", fields: { 发布日期: "2026-10-14" }, score: 9, scripted: true, published: true, scripts: [{ id: "s9", column_key: "flash", status: "APPROVED", created_at: "2026-10-19T14:45:00+0800" }] },
    { id: "2026-10-19/F2", round: "2026-10-19", code: "F2", title: "GPT-5.5 从 ChatGPT 退役", fields: { 发布日期: "2026-10-10" }, score: null, scripted: false, published: false, stale: true },
    { id: "2026-10-19/F3", round: "2026-10-19", code: "F3", title: "不想写的一条", fields: {}, score: null, scripted: false, published: false, dismissed: true },
  ],
};
const CELLS = [
  { id: "rnd-cell", key: "rnd", label: "盘点", usable: true, status: "experimental", parts: { type: { key: "roundup", name: "盘点" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "roundup" } },
  { id: "cmp-cell", key: "cmp", label: "对比", usable: true, status: "experimental", parts: { type: { key: "comparison", name: "对比" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "comparison" } },
  { id: "whole-cell", key: "digest", label: "报告速览", usable: true, status: "experimental", parts: { type: { key: "digest", name: "报告速览" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "single" } },
  { id: "ai-developer-quick-take-long", key: "flash", label: "快讯", usable: true, status: "enabled", parts: { type: { key: "quick_take", name: "快讯" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "single" } },
];

let DISCOVERY = { running: false, report_date: "2026-10-19", report_at: 1792400000, searches: 20 };
const COVERAGE = [
  { cell: "flash", label: "快讯", status: "enabled", sources: ["flash"], open: 1, written: 1, dismissed: 0, multi: false, autopilot: true, report_date: "2026-10-19" },
  { cell: "rnd", label: "盘点", status: "experimental", sources: ["flash"], open: 2, written: 0, dismissed: 0, multi: true, autopilot: false, report_date: "2026-10-19" },
  { cell: "cmp", label: "对比", status: "experimental", sources: [], open: 0, written: 0, dismissed: 0, multi: false, autopilot: false, report_date: null },
];

const SOURCES = [
  { name: "flash", job: "monitoring/ai_flash_discovery", label: "AI Flash Discovery", format: "flash", prefix: "", refresh: true, error: "", reports: 2, latest: "2026-10-19", report_error: "" },
  { name: "manual", job: null, label: "手动添加", format: null, prefix: "M", refresh: false, error: "", reports: 0, latest: null, report_error: "", count: 0 },
];

function browserBody(cells = CELLS, coverage = COVERAGE) {
  return { cells, coverage, sources: SOURCES, initial: { source: "flash", ...TOPICS } };
}

function CreatedScript() {
  const location = useLocation();
  const back = new URLSearchParams(location.search).get("return_to") || "/topics";
  return <><p>已进入当前文稿</p><output aria-label="文稿地址">{location.pathname + location.search}</output><Link to={back}>返回原选题</Link></>;
}

beforeEach(() => {
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, status: 200, statusText: "ok",
    text: async () => JSON.stringify(String(url).includes("/topics/browser") ? browserBody() : String(url).includes("/topics/sources") ? SOURCES : String(url).includes("/discovery") ? DISCOVERY : String(url).includes("/coverage") ? COVERAGE : String(url).includes("/cells") ? CELLS : TOPICS),
  }) as Response));
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null });
  vi.unstubAllGlobals();
});

describe("Topics page", () => {
  it("starts the picked topic once in place and restores its context and scroll on return", async () => {
    const posts: object[] = [];
    let finish: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)));
        return new Promise<Response>(resolve => { finish = resolve; });
      }
      const u = String(url);
      const round = new URL(u, "http://local").searchParams.get("round") || TOPICS.round;
      return { ok: true, status: 200, text: async () => JSON.stringify(u.includes("/topics/browser") ? browserBody() : u.includes("/discovery") ? DISCOVERY : { ...TOPICS, round }) } as Response;
    }));
    render(<MemoryRouter initialEntries={["/topics?cell=flash&source=flash&round=2026-10-18&task=unwritten&q=GPT"]}>
      <main data-testid="topic-scroll"><Routes><Route path="/topics" element={<TopicsPage />} /><Route path="/video/:id" element={<CreatedScript />} /></Routes></main>
    </MemoryRouter>);
    const start = await screen.findByRole("button", { name: "开始写稿：GPT-5.5 从 ChatGPT 退役" });
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    const main = screen.getByTestId("topic-scroll"); main.scrollTop = 420; fireEvent.scroll(main);
    fireEvent.click(start); fireEvent.click(start);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ column: "flash", topic_id: "2026-10-19/F2" });
    expect((start as HTMLButtonElement).disabled).toBe(true);
    finish({ ok: true, status: 202, text: async () => JSON.stringify({ id: "new-script" }) } as Response);
    const address = await screen.findByLabelText("文稿地址");
    const query = new URL(address.textContent!, "http://local").searchParams;
    expect(query.get("return_to")).toContain("round=2026-10-18");
    expect(query.get("return_to")).toContain("task=unwritten");
    expect(query.get("return_to")).toContain("#topic-2026-10-19%2FF2");
    main.scrollTop = 0;
    fireEvent.click(screen.getByText("返回原选题"));
    await screen.findByRole("button", { name: "开始写稿：GPT-5.5 从 ChatGPT 退役" });
    expect((screen.getByLabelText("报告日期") as HTMLSelectElement).value).toBe("2026-10-18");
    expect((screen.getByLabelText("视频类型") as HTMLSelectElement).value).toBe("flash");
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("GPT");
    await waitFor(() => expect(main.scrollTop).toBe(420));
  });

  it("keeps a failed start visible on its card and does not automatically retry", async () => {
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { posts++; return { ok: false, status: 503, text: async () => JSON.stringify({ error: { code: "unavailable", message: "写稿服务暂不可用" } }) } as Response; }
      return { ok: true, status: 200, text: async () => JSON.stringify(String(url).includes("/topics/browser") ? browserBody() : DISCOVERY) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "开始写稿：GPT-5.5 从 ChatGPT 退役" }));
    expect((await screen.findByRole("alert")).textContent).toContain("写稿服务暂不可用");
    expect(posts).toBe(1);
    expect((screen.getByRole("button", { name: "开始写稿：GPT-5.5 从 ChatGPT 退役" }) as HTMLButtonElement).disabled).toBe(false);
  });
  function progressTopics() {
    const topic = (id: string, title: string, scripts: object[] = []) => ({ ...TOPICS.topics[1], id, title, scripts });
    const script = (id: string, extra: object = {}) => ({ id, column_key: "flash", status: "APPROVED", created_at: "2026-10-19T14:45:00+0800", version: 2, render_state: "none", ...extra });
    return [
      topic("done", "当前版本已经完成", [script("done-script", { render_state: "done", video_url: "/api/video/renders/current/files/video.mp4", render: { status: "FAILED", version: 2, id: "retry" } })]),
      topic("old", "只有旧版成片", [script("old-script", { status: "DRAFT", previous_video: { version: 1, url: "/old-video.mp4" } }), script("earlier-script", { created_at: "2026-10-18", render_state: "done", video_url: "/earlier-script.mp4" })]),
      topic("working", "当前版本正在制作", [script("running-script", { render_state: "working", render: { id: "run", version: 2, status: "RUNNING" } })]),
      topic("failed", "当前版本出片失败", [script("failed-script", { render_state: "failed", render: { id: "fail", version: 2, status: "FAILED" } })]),
      topic("draft-failed", "写稿失败也能看到", [script("draft-failed-script", { status: "FAILED", render_state: "none", version: 0 })]),
      topic("unwritten", "还没开始写稿"),
      topic("other", "其他视频类型已有成片", [script("other-script", { column_key: "rnd", render_state: "done", video_url: "/roundup.mp4" })]),
    ];
  }
  function useProgressTopics() {
    let topics = progressTopics();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const listing = { ...TOPICS, topics };
      const body = String(url).includes("/topics/browser") ? { ...browserBody(), initial: { source: "flash", ...listing } }
        : String(url).includes("/discovery") ? DISCOVERY : listing;
      return { ok: true, status: 200, text: async () => JSON.stringify(body) } as Response;
    }));
    return (next: typeof topics) => { topics = next; };
  }

  it("provides task shortcuts for actionable work and includes writing failures in exceptions", async () => {
    useProgressTopics();
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "异常 2" }));
    expect(screen.getByText("当前版本出片失败")).toBeTruthy();
    expect(screen.getByText("写稿失败也能看到")).toBeTruthy();
    expect(screen.queryByText("当前版本已经完成")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "待写 2" }));
    expect(screen.getByText("还没开始写稿")).toBeTruthy();
    expect(screen.getByText("其他视频类型已有成片")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "文稿待处理 1" }));
    expect(screen.getByRole("link", { name: "继续编辑：只有旧版成片" }).getAttribute("href")).toContain("tab=draft");
    fireEvent.click(screen.getByRole("button", { name: "出片中 1" }));
    expect(screen.getByRole("link", { name: "查看进度：当前版本正在制作" }).getAttribute("href")).toContain("tab=video");
  });

  it("separates duplicate skips from failures and opens the existing video without production requests", async () => {
    const update = useProgressTopics();
    update([...progressTopics(), { ...TOPICS.topics[1], id: "duplicate", title: "昨天已经讲过的事件", scripts: [
      { id: "skipped", column_key: "flash-nr", status: "FAILED", archived: 1, version: 0, created_at: "2026-10-30", render_state: "none",
        selection_skip: { kind: "duplicate", reason: "主事件已经讲过", originals: [
          { id: "original", title: "昨天的完整视频", column_key: "flash", status: "APPROVED", version: 1, created_at: "2026-10-29", render_state: "done", video_url: "/original-video.mp4" },
        ] } },
    ] }]);
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "已跳过 1" }));
    const progress = screen.getByLabelText("昨天已经讲过的事件的制作进度");
    expect(within(progress).getByText("重复选题，已跳过")).toBeTruthy();
    expect(within(progress).queryByText("写稿：失败")).toBeNull();
    expect(within(progress).queryByText("出片：未出片")).toBeNull();
    expect(screen.queryByRole("link", { name: "查看原因：昨天已经讲过的事件" })).toBeNull();
    expect(screen.queryByRole("button", { name: "开始写稿：昨天已经讲过的事件" })).toBeNull();
    expect(within(progress).getByRole("link", { name: "已有文稿：昨天的完整视频" }).getAttribute("href")).toContain("/video/original?");
    fireEvent.click(within(progress).getByRole("button", { name: "查看已有成片：昨天的完整视频" }));
    expect(progress.querySelector("video")?.getAttribute("src")).toBe("/original-video.mp4");
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "异常 2" }));
    expect(screen.queryByText("昨天已经讲过的事件")).toBeNull();
    expect(screen.getByText("写稿失败也能看到")).toBeTruthy();
  });

  it("starts only the selected approved script's render and immediately shows its progress", async () => {
    let posts = 0;
    const topics = [...progressTopics(), { ...TOPICS.topics[1], id: "ready", title: "已有定稿等待出片", scripts: [{ id: "approved-script", column_key: "flash", status: "APPROVED", version: 2, created_at: "2026-10-19", render_state: "none", previous_video: { version: 1, url: "/kept-old.mp4" } }] }];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts++; expect(String(url)).toBe("/api/video/scripts/approved-script/render");
        return { ok: true, status: 202, text: async () => JSON.stringify({ id: "new-render", version: 2, status: "SUBMITTING" }) } as Response;
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(String(url).includes("/topics/browser") ? { ...browserBody(), initial: { source: "flash", ...TOPICS, topics } } : DISCOVERY) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "待出片 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成成片：已有定稿等待出片" }));
    await waitFor(() => expect(posts).toBe(1));
    // The completed request leaves the ready queue without affecting the old video's record.
    await waitFor(() => expect(screen.queryByText("已有定稿等待出片")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "出片中 2" }));
    const progress = screen.getByLabelText("已有定稿等待出片的制作进度");
    expect(within(progress).getByText("有旧版成片 v1（不计入当前版本）")).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看进度：已有定稿等待出片" })).toBeTruthy();
  });

  it("shows separate draft/video states, plays current and old videos within the topic card", async () => {
    useProgressTopics();
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    const done = await screen.findByLabelText("当前版本已经完成的制作进度");
    expect(within(done).getByText("写稿：已定稿")).toBeTruthy();
    expect(within(done).getByText("出片：已出片")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看成片：当前版本已经完成" }));
    expect(within(done).getByLabelText("当前版本已经完成：当前文稿 v2 成片").getAttribute("src")).toBe("/api/video/renders/current/files/video.mp4");
    fireEvent.click(within(done).getByText("关闭成片"));
    expect(done.querySelector("video")).toBeNull();
    const old = screen.getByLabelText("只有旧版成片的制作进度");
    expect(within(old).getByText("写稿：待审稿")).toBeTruthy();
    expect(within(old).getByText("出片：未出片")).toBeTruthy();
    expect(within(old).getByText("有旧版成片 v1（不计入当前版本）")).toBeTruthy();
    fireEvent.click(within(old).getByText("查看旧版成片"));
    expect(old.querySelector("video")?.getAttribute("src")).toBe("/old-video.mp4");
    fireEvent.error(old.querySelector("video")!);
    expect(within(old).getByRole("alert").textContent).toContain("暂时无法播放");
    expect(screen.getAllByText("写稿：未写稿")).toHaveLength(2); // Other types do not claim this cell is done.
    expect(screen.getByLabelText("写稿失败也能看到的制作进度").textContent).toContain("写稿：失败");
    expect(screen.getByLabelText("写稿失败也能看到的制作进度").textContent).toContain("出片：未出片");
  });

  it("filters by the newest matching script's current version and preserves the filter in the URL", async () => {
    useProgressTopics();
    render(<MemoryRouter initialEntries={["/topics?render=done"]}><TopicsPage /></MemoryRouter>);
    await screen.findByText("当前版本已经完成");
    expect(screen.queryByText("只有旧版成片")).toBeNull();
    expect(screen.queryByText("其他视频类型已有成片")).toBeNull();
    fireEvent.change(screen.getByLabelText("出片"), { target: { value: "none" } });
    expect(screen.getByText("只有旧版成片")).toBeTruthy();
    expect(screen.getByText("还没开始写稿")).toBeTruthy();
    expect(screen.getByText("其他视频类型已有成片")).toBeTruthy();
    expect(screen.getByText("写稿失败也能看到")).toBeTruthy();
    expect(screen.queryByText("当前版本正在制作")).toBeNull();
    fireEvent.change(screen.getByLabelText("出片"), { target: { value: "working" } });
    expect(screen.getByText("当前版本正在制作")).toBeTruthy();
    expect(screen.queryByText("当前版本出片失败")).toBeNull();
    fireEvent.change(screen.getByLabelText("出片"), { target: { value: "failed" } });
    expect(screen.getByText("当前版本出片失败")).toBeTruthy();
    expect(screen.queryByText("写稿失败也能看到")).toBeNull();
    expect(screen.queryByText("还没开始写稿")).toBeNull();
  });

  it("refreshes production progress on returning to the page without another catalog request", async () => {
    const update = useProgressTopics();
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    await screen.findByText("当前版本正在制作");
    const next = progressTopics();
    next[2].scripts = [{ id: "running-script", column_key: "flash", status: "APPROVED", version: 2, created_at: "2026-10-19", render_state: "done", video_url: "/just-completed.mp4" }];
    update(next);
    fireEvent(document, new Event("visibilitychange"));
    const progress = screen.getByLabelText("当前版本正在制作的制作进度");
    await waitFor(() => expect(within(progress).getByText("出片：已出片")).toBeTruthy());
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/topics/browser"))).toHaveLength(1);
  });

  it("matches production variants and does not let an archived failed draft hide the usable current video", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const topics = [{ ...TOPICS.topics[1], title: "自动配置生成的快讯", scripts: [
        { id: "archived", column_key: "flash-nr", cell_key: "flash", status: "FAILED", archived: 1, created_at: "2026-10-20", render_state: "failed", version: 0 },
        { id: "live", column_key: "flash-nr", cell_key: "flash", status: "APPROVED", created_at: "2026-10-19", render_state: "done", version: 1, video_url: "/live.mp4" },
      ] }];
      return { ok: true, status: 200, text: async () => JSON.stringify(String(url).includes("/topics/browser")
        ? { ...browserBody(), initial: { source: "flash", ...TOPICS, topics } } : DISCOVERY) } as Response;
    }));
    render(<MemoryRouter initialEntries={["/topics?render=done"]}><TopicsPage /></MemoryRouter>);
    const progress = await screen.findByLabelText("自动配置生成的快讯的制作进度");
    expect(within(progress).getByText("出片：已出片")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看成片：自动配置生成的快讯" }));
    expect(progress.querySelector("video")?.getAttribute("src")).toBe("/live.mp4");
    fireEvent.click(within(progress).getByText("其他关联文稿（1）"));
    expect(within(progress).getByText("写稿：失败")).toBeTruthy();
  });

  it("gets the first topics with one browser request, without waiting for discovery status", async () => {
    const paths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      paths.push(String(url));
      if (String(url).includes("/discovery")) return new Promise<Response>(() => {});
      return { ok: true, status: 200, text: async () => JSON.stringify(browserBody()) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    expect(await screen.findByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
    expect(paths.filter((p) => !p.includes("/discovery"))).toEqual(["/api/video/topics/browser"]);
  });

  it("keeps the last list during a failed refresh and still lets the user change sources", async () => {
    let reads = 0;
    let failRefresh: (reason: Error) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/topics/browser") && ++reads > 1) return new Promise<Response>((_resolve, reject) => { failRefresh = reject; });
      const body = u.includes("/topics/browser") ? browserBody() : u.includes("/discovery") ? DISCOVERY
        : { round: null, rounds: [], topics: [{ ...TOPICS.topics[1], id: "manual/1", title: "手动选题新内容", source: "manual" }] };
      return { ok: true, status: 200, text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    await screen.findByText("GPT-5.5 从 ChatGPT 退役");
    fireEvent.click(screen.getByRole("button", { name: "刷新选题" }));
    expect(screen.getByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
    expect((screen.getByRole("button", { name: "刷新选题" }) as HTMLButtonElement).disabled).toBe(true);
    failRefresh(new Error("offline"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "手动添加" }));
    expect(await screen.findByText("手动选题新内容")).toBeTruthy();
  });

  it("reads fresh topics when returning to the default source and ignores an older source response", async () => {
    let finishManual: (value: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("source=manual")) return new Promise<Response>((resolve) => { finishManual = resolve; });
      const body = u.includes("/topics/browser") ? browserBody() : u.includes("/discovery") ? DISCOVERY
        : { ...TOPICS, topics: [{ ...TOPICS.topics[1], title: "默认来源的新报告条目" }] };
      return { ok: true, status: 200, text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    await screen.findByText("GPT-5.5 从 ChatGPT 退役");
    fireEvent.click(screen.getByRole("button", { name: "手动添加" }));
    await screen.findByText("正在读取选题…");
    fireEvent.click(screen.getByRole("button", { name: "AI Flash Discovery" }));
    expect(await screen.findByText("默认来源的新报告条目")).toBeTruthy();
    finishManual({ ok: true, status: 200, text: async () => JSON.stringify({ round: null, topics: [{ ...TOPICS.topics[1], title: "迟到的旧来源条目" }] }) } as Response);
    await waitFor(() => expect(screen.queryByText("迟到的旧来源条目")).toBeNull());
  });

  it("keeps publication history and offers a direct start instead of another picker", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText("已发布")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "开始写稿：GPT-5.5 从 ChatGPT 退役" })).toBeTruthy();
    expect(screen.queryByText("用这个写稿")).toBeNull();
    expect(screen.getByText(/当前视频类型待写 1 条/)).toBeTruthy();
  });

  it("shows the event date next to the report date and which script already covers a topic, and filters", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText("事件日期 2026-10-14")).toBeTruthy();
    expect(screen.getByText("已有文稿").getAttribute("href")).toContain("/video/s9?tab=draft&return_to=");
    fireEvent.change(screen.getByLabelText("写稿"), { target: { value: "no" } });
    await waitFor(() => expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull());
    expect(screen.getByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
  });

  it("has a tab per cell; a cell without a source says it is chosen by hand", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    fireEvent.click(await screen.findByRole("button", { name: "对比 开发者" }));
    expect(await screen.findByText(/还没有选题来源/)).toBeTruthy();
    expect(screen.queryByText("GPT-5.5 从 ChatGPT 退役")).toBeNull();
  });

  it("leaves out a recipe written from a whole report: it has no items to pick here", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    await screen.findByRole("button", { name: "快讯 开发者" });
    expect(screen.queryByRole("button", { name: "报告速览 开发者" })).toBeNull();
  });

  it("marks old news, hides what was marked not-writing, and disables discovery while it runs", async () => {
    DISCOVERY = { ...DISCOVERY, running: true };
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText("偏旧")).toBeTruthy();
    expect(screen.queryByText("不想写的一条")).toBeNull();
    const button = await screen.findByText("正在生成…");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    DISCOVERY = { ...DISCOVERY, running: false };
  });

  it("offers a roundup built from the ticked flash items", async () => {
    DISCOVERY = { ...DISCOVERY, running: false };
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    fireEvent.click(await screen.findByRole("button", { name: "盘点 开发者" }));
    const box = await screen.findByLabelText("选 F2");
    fireEvent.click(box);
    expect(screen.queryByText(/合成一期盘点（/)).toBeNull(); // one is not enough
    expect(screen.queryByText("用这个写稿")).toBeNull();
  });

  it("adds a topic by hand, which lands under the 手动添加 tab, and flags a source no job declares", async () => {
    const posts: { url: string; body: string }[] = [];
    const manual = { id: "manual/1", round: "", code: "M1", title: "为什么我不再用向量库", fields: {}, score: null, scripted: false, source: "manual" };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (init?.method === "POST") posts.push({ url: u, body: String(init.body) });
      const coverage = COVERAGE.map((c) => (c.cell === "flash" ? { ...c, unknown_sources: ["rss"] } : c));
      const body = u.includes("/topics/browser") ? browserBody(CELLS, coverage) : u.includes("/topics/sources") ? SOURCES : u.includes("/discovery") ? DISCOVERY : u.includes("/coverage") ? coverage
        : u.includes("/cells") ? CELLS : u.includes("source=manual") ? { round: null, rounds: [], topics: posts.length ? [manual] : [] }
        : init?.method === "POST" ? manual : TOPICS;
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByText(/没有 Job 声明的选题来源：rss/)).toBeTruthy();
    expect(await screen.findByRole("button", { name: "AI Flash Discovery" })).toBeTruthy();
    fireEvent.click(screen.getByText("手动添加选题"));
    fireEvent.change(screen.getByPlaceholderText("标题（至少 4 个字）"), { target: { value: "为什么我不再用向量库" } });
    fireEvent.click(screen.getByText("添加"));
    await waitFor(() => expect(posts.some((p) => p.url.endsWith("/api/video/topics"))).toBe(true));
    expect(JSON.parse(posts[0].body).title).toBe("为什么我不再用向量库");
    expect(await screen.findByText("为什么我不再用向量库")).toBeTruthy(); // the 手动添加 tab is shown
    expect(screen.getByRole("button", { name: "手动添加" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("gives each domain its own grid, so a second domain's recipe for the same type and audience is not hidden", async () => {
    const ai = { domain: { key: "ai", name: "AI" } };
    const cells = [
      ...CELLS.map((c) => ({ ...c, parts: { ...c.parts, ...ai } })),
      { id: "med-developer-quick-take-long", key: "med_flash", label: "医疗快讯", usable: true, status: "enabled", parts: { domain: { key: "medical", name: "医疗" }, type: { key: "quick_take", name: "快讯" }, audience: { key: "developer", name: "开发者" } }, column: { topic_kind: "single" } },
    ];
    const coverage = [...COVERAGE, { cell: "med_flash", label: "医疗快讯", status: "enabled", sources: ["medical_flash"], open: 4, written: 0, dismissed: 0, multi: false, autopilot: false, report_date: "2026-10-19" }];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      const body = u.includes("/topics/browser") ? browserBody(cells, coverage) : u.includes("/topics/sources") ? SOURCES : u.includes("/discovery") ? DISCOVERY : u.includes("/coverage") ? coverage : u.includes("/cells") ? cells : TOPICS;
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
    }));
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(screen.getByText("按领域与受众选择类型"));
    expect(await screen.findByRole("button", { name: "医疗 快讯 开发者" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "AI 快讯 开发者" }).textContent).toContain("1 条候选");
    expect(screen.getByRole("button", { name: "医疗 快讯 开发者" }).textContent).toContain("4 条候选");
    expect(screen.getByLabelText("医疗内容矩阵")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "快讯 开发者" })).toBeNull(); // with one domain the label stays as it was
  });
});
