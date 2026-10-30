import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/lib/toast";
import { ThemeWorkbench } from "./ThemeWorkbench";
import type { ThemeWorkbenchData } from "@/api/themes";

const DATA: ThemeWorkbenchData = {
  today: "2026-10-27", discovery_running: false,
  feedback: { topics: 0 },
  configuration: { time: "09:00", timezone: "Asia/Shanghai", platforms: ["bilibili", "zhihu"] },
  schedule: null, scheduler: { online: true, detail: "执行器在线" }, runs: [],
  library: { examples: [{ id: "ref", category: "value", title: "一份真实的免费与付费比较", url: "https://example.org/reference", summary: "按任务比较" }],
    patterns: [{ id: "format-one", category: "value", name: "任务与升级判断", title_pattern: "为了{任务}值得升级吗？", outline: ["场景", "比较", "决策"], example_ids: ["ref"] }], runs: [] },
  categories: [
    { key: "value", name: "T06 套餐价值", promise: "我是否需要付费", audience: "普通用户", notes: "", production_ids: [] },
    { key: "how", name: "T03 使用教程", promise: "怎样使用", audience: "新用户", notes: "", production_ids: [] },
  ],
  themes: [
    { id: "one", matrix: "demo", category: "value", title: "免费版用了一个月，应该升级吗？", angle: "从具体任务判断", audience: "普通用户", notes: "",
      origin: "discovered", day: "2026-10-27", reason: "公开问题中出现具体的升级疑问", status: "recommended", script_id: "", auto_video: false, created_at: 1, updated_at: 1,
      signals: [{ id: "s1", category: "value", title: "相关作品", url: "https://www.bilibili.com/video/BV1test", excerpt: "问题", platform: "B站", collected_at: 1793100000,
        metrics: { view: 12345, like: 123 }, signal_kind: "public_metrics" }] },
    { id: "two", matrix: "demo", category: "how", title: "怎样把一份资料问清楚？", angle: "", audience: "新用户", notes: "", origin: "discovered", day: "2026-10-27", reason: "教程线索",
      status: "recommended", script_id: "", auto_video: false, created_at: 1, updated_at: 1,
      signals: [{ id: "s2", category: "how", title: "一篇教程", url: "https://example.org/tutorial", excerpt: "公开内容", platform: "知乎", collected_at: 1793100000,
        metrics: {}, signal_kind: "indexed_content" }] },
  ],
};
let data: ThemeWorkbenchData;
let calls: { url: string; method: string; body: Record<string, unknown> }[];
beforeEach(() => {
  data = structuredClone(DATA); calls = [];
  data.themes.forEach((theme) => { theme.used_at ??= 0; });
  vi.stubGlobal("fetch", vi.fn(async (url, init) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(init.body) : {};
    calls.push({ url: String(url), method, body });
    if (method === "POST" && String(url).endsWith("/start")) Object.assign(data.themes[0], { script_id: "script-one", used_at: 1793100000 });
    if (method === "POST" && String(url).endsWith("/feedback")) {
      Object.assign(data.themes[0], { feedback: body, status: body.action === "dismissed" ? "dismissed" : "ready" });
    }
    if (method === "POST" && String(url).endsWith("/used")) {
      Object.assign(data.themes[0], { status: "used", used_at: 1793100000 }); data.used_count = 1;
    }
    const result = method === "GET" && String(url).includes("/history") ? { themes: data.themes.filter((t) => t.used_at), total: 1, next_offset: null }
      : method === "GET" ? data : { id: "new", status: "running" };
    return { ok: true, status: 200, text: async () => JSON.stringify(result) } as Response;
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function show(canEdit = true) { return render(<MemoryRouter><ToastProvider><ThemeWorkbench matrix="demo" canEdit={canEdit} /></ToastProvider></MemoryRouter>); }

describe("matrix topic workbench", () => {
  it('shows queued and quota states without offering a second production start for the queued theme', async () => {
    data.themes[0].status = 'queued';
    data.themes[0].automatic_production = { state: 'queued', note: '等待制作名额' };
    data.themes[1].automatic_production = { state: 'quota', note: '本栏目本周推荐配额已用完' };
    show();
    const queued = await screen.findByRole('article', { name: `主题 ${data.themes[0].title}` });
    expect(within(queued).getByText('已排队，等待制作')).toBeTruthy();
    expect(within(queued).queryByRole('button', { name: '研究并制作视频' })).toBeNull();
    expect(screen.getByText('本栏目本周推荐配额已用完')).toBeTruthy();
    expect(calls.some(call => call.method === 'POST')).toBe(false);
  });
  it("filters a larger reference library by subject", async () => {
    data.library!.patterns.push({ id: "format-two", category: "how", name: "连续任务演示", title_pattern: "怎样完成{任务}？", outline: ["输入", "操作", "结果"], example_ids: [] });
    show();
    fireEvent.click(await screen.findByRole("button", { name: /参考库 ·/ }));
    expect(screen.getByRole("article", { name: "题型 任务与升级判断" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("参考库分类"), { target: { value: "how" } });
    expect(screen.queryByRole("article", { name: "题型 任务与升级判断" })).toBeNull();
    expect(screen.getByRole("article", { name: "题型 连续任务演示" })).toBeTruthy();
  });

  it("keeps earlier categories visible and lets their retained formats generate again", async () => {
    data.legacy_categories = [{ ...data.categories[0], key: "old", name: "原有每日观察", legacy: true }];
    data.themes[0].category = "old";
    data.library!.examples[0].category = "old";
    data.library!.patterns[0].category = "old";
    show();
    const theme = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(within(theme).getByText("原有每日观察")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /参考库 ·/ }));
    expect(screen.getByRole("option", { name: "原有每日观察 · 早期分类" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "参考这个题型" }));
    expect((screen.getByLabelText("出题栏目") as HTMLSelectElement).value).toBe("old");
    expect((screen.getByLabelText("参考题型") as HTMLSelectElement).value).toBe("format-one");
    fireEvent.click(screen.getByRole("button", { name: "生成候选题目" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/generate") && c.body.category_key === "old" && c.body.pattern_id === "format-one")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "帮我出一批题" }));
    fireEvent.change(screen.getByLabelText("参考题型"), { target: { value: "" } });
    expect((screen.getByLabelText("出题栏目") as HTMLSelectElement).value).toBe("");
  });

  it("keeps platform discovery as an explicit supplementary action", async () => {
    data.configuration.mode = "ideas";
    show();
    fireEvent.click(await screen.findByRole("button", { name: "联网补充参考" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/discover") && c.method === "POST")).toBe(true));
    expect(calls.some((c) => c.url.endsWith("/schedule"))).toBe(false);
  });

  it("keeps an older backend usable during a graceful rollout", async () => {
    delete data.feedback;
    data.configuration.mode = "ideas";
    show();
    await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(screen.queryByRole("button", { name: /^感兴趣$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "查看阶段用量" })).toBeNull();
    expect((screen.getByRole("button", { name: "设置每日推荐" }) as HTMLButtonElement).disabled).toBe(true);
    const card = screen.getByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    fireEvent.click(within(card).getByRole("button", { name: "暂不感兴趣" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/dismiss"))).toBe(true));
    expect(calls.some((c) => c.url.endsWith("/feedback"))).toBe(false);
  });

  it("keeps the legacy discovery schedule payload compatible", async () => {
    delete data.feedback;
    show();
    fireEvent.click(await screen.findByRole("button", { name: "设置每日推荐" }));
    expect(screen.queryByLabelText("每日推荐方式")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存并开启" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/schedule") && !('mode' in c.body) && !('direction' in c.body))).toBe(true));
  });
  it("saves reference generation and a permanent direction on the daily plan", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "设置每日推荐" }));
    fireEvent.change(screen.getByLabelText("每日推荐方式"), { target: { value: "ideas" } });
    fireEvent.change(screen.getByLabelText("长期出题方向"), { target: { value: "面向新手，使用具体办公任务" } });
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存并开启" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/schedule") && c.body.mode === "ideas" && c.body.direction === "面向新手，使用具体办公任务")).toBe(true));
    expect(calls.some((c) => c.url.endsWith("/generate") || c.url.endsWith("/start"))).toBe(false);
  });

  it("records a specific ignore reason and restores the retained topic", async () => {
    show();
    const card = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    fireEvent.click(within(card).getByRole("button", { name: "暂不感兴趣" }));
    fireEvent.change(screen.getByLabelText("忽略原因"), { target: { value: "hard_to_show" } });
    fireEvent.change(screen.getByLabelText("补充说明（选填）"), { target: { value: "给出可见成果" } });
    fireEvent.click(screen.getByRole("button", { name: "保存忽略" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" })).toBeNull());
    expect(calls.some((c) => c.url.endsWith("/feedback") && c.body.reason === "hard_to_show" && c.body.note === "给出可见成果")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "已忽略" }));
    const retained = screen.getByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(within(retained).getByText(/不容易用画面讲清楚/)).toBeTruthy();
    fireEvent.click(within(retained).getByRole("button", { name: "恢复待选" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/feedback") && c.body.action === "clear")).toBe(true));
  });
  it("generates a batch from the matrix without requiring a topic or extra direction", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "帮我出一批题" }));
    fireEvent.click(screen.getByRole("button", { name: "生成候选题目" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/generate") && c.body.brief === "" && c.body.category_key === "" && c.body.pattern_id === "")).toBe(true));
    expect(calls.some((c) => c.url.endsWith("/start"))).toBe(false);
  });

  it("retains library examples and lets a user reuse a format with optional direction", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "参考库 · 1 个例题" }));
    const card = screen.getByRole("article", { name: "题型 任务与升级判断" });
    expect(within(card).getByRole("link", { name: "一份真实的免费与付费比较" }).getAttribute("href")).toBe("https://example.org/reference");
    fireEvent.click(within(card).getByRole("button", { name: "参考这个题型" }));
    expect((screen.getByLabelText("参考题型") as HTMLSelectElement).value).toBe("format-one");
    fireEvent.change(screen.getByLabelText("补充出题方向（选填）"), { target: { value: "多讲英语练习" } });
    fireEvent.click(screen.getByRole("button", { name: "生成候选题目" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/generate") && c.body.pattern_id === "format-one" && c.body.category_key === "value" && c.body.brief === "多讲英语练习")).toBe(true));
  });

  it("keeps used topics in history and keeps their format available for new questions", async () => {
    Object.assign(data.themes[0], { origin: "generated", day: "2024-01-01", inspiration: { question: "是否升级", topic_key: "upgrade", pattern_id: "format-one",
      pattern_name: "任务与升级判断", title_pattern: "为了{任务}值得升级吗？", outline: ["场景", "比较", "决策"], example_ids: [], basis: "reference_library" } });
    data.themes[0].day = data.today;
    data.themes[0].signals = [];
    show();
    const card = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(within(card).getByText("参考题型推荐")).toBeTruthy();
    expect(within(card).queryByLabelText("主题热度依据")).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "标记已用" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" })).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "使用记录 · 1" }));
    const kept = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(within(kept).getByText("已使用")).toBeTruthy();
    expect(within(kept).queryByRole("button", { name: "研究并制作视频" })).toBeNull();
    fireEvent.click(within(kept).getByRole("button", { name: "沿用题型出新题" }));
    expect(screen.getByRole("form", { name: "参考题型出题" })).toBeTruthy();
  });

  it("shows different categories and distinguishes actual counters from public content clues", async () => {
    show();
    const first = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    const second = screen.getByRole("article", { name: "主题 怎样把一份资料问清楚？" });
    expect(within(first).queryByText("0", { exact: true })).toBeNull();
    fireEvent.click(within(first).getByText("查看 1 条推荐线索"));
    expect(within(first).getByText(/播放 12,345/)).toBeTruthy();
    fireEvent.click(within(second).getByText("查看 1 条推荐线索"));
    expect(within(second).getByText("公开内容线索，未取得互动指标")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("筛选主题栏目"), { target: { value: "how" } });
    expect(screen.queryByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" })).toBeNull();
    expect(screen.getByRole("article", { name: "主题 怎样把一份资料问清楚？" })).toBeTruthy();
  });

  it("sorts categories using real reference counters and keeps observed zeroes visible", async () => {
    data.themes.forEach((theme, index) => {
      theme.ranking = { score: index ? 80 : 50, relevance: 90, measured_signals: 1, basis: "真实计数", formula: "排序公式",
        primary: { heat_score: 70, signal_id: theme.signals[0].id, observed_at: 1793100000, status: "fresh",
          metrics: { view: index ? 9000 : 30000, like: 0, favorite: index ? 500 : 100 }, views_per_day: 500, speed_kind: "lifetime_average" } };
    });
    show();
    await screen.findAllByLabelText("主题热度依据");
    expect(screen.getAllByRole("article")[0].getAttribute("aria-label")).toBe("主题 怎样把一份资料问清楚？");
    expect(screen.getAllByText(/并非近期增量/).length).toBe(2);
    fireEvent.change(screen.getByLabelText("主题排序"), { target: { value: "view" } });
    expect(screen.getAllByRole("article")[0].getAttribute("aria-label")).toBe("主题 免费版用了一个月，应该升级吗？");
    fireEvent.change(screen.getByLabelText("主题排序"), { target: { value: "favorite" } });
    expect(screen.getAllByRole("article")[0].getAttribute("aria-label")).toBe("主题 怎样把一份资料问清楚？");
  });

  it("creates a manual theme with a category, audience and optional supplied material", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "自己写主题" }));
    fireEvent.change(screen.getByLabelText("主题标题"), { target: { value: "我自己的一个具体选题是什么？" } });
    fireEvent.change(screen.getByLabelText("补充材料"), { target: { value: "我提供的使用经历" } });
    fireEvent.click(screen.getByRole("button", { name: "保存主题" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.body.title === "我自己的一个具体选题是什么？" && c.body.notes === "我提供的使用经历" && c.body.category === "value")).toBe(true));
  });

  it("starts a chosen theme through research and video and shows its script link", async () => {
    show();
    const card = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    fireEvent.click(within(card).getByRole("button", { name: "研究并制作视频" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/one/start") && c.body.auto_video === true && c.body.minutes === 4)).toBe(true));
    expect(await screen.findByRole("link", { name: "查看文稿与视频" })).toBeTruthy();
  });

  it("lets viewers inspect themes while hiding mutation controls", async () => {
    show(false);
    await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(screen.queryByRole("button", { name: "自己写主题" })).toBeNull();
    expect(screen.queryByRole("button", { name: "研究并制作视频" })).toBeNull();
    expect(screen.queryByText("设置每日推荐")).toBeNull();
    expect(screen.queryByRole("button", { name: "帮我出一批题" })).toBeNull();
  });

  it("shows failed production with a link to its existing retry controls", async () => {
    Object.assign(data.themes[0], { script_id: "failed-script", auto_video: true, status: "attention",
      script_status: "APPROVED", renders: [{ id: "render-one", status: "FAILED" }] });
    show();
    const card = await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" });
    expect(within(card).getByText("视频需要重试")).toBeTruthy();
    expect(within(card).getByRole("link", { name: "查看文稿与视频" }).getAttribute("href")).toBe("/video/failed-script");
    expect(within(card).queryByRole("button", { name: "研究并制作视频" })).toBeNull();
  });

  it("saves a daily schedule without starting theme production", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "设置每日推荐" }));
    fireEvent.change(screen.getByLabelText("每天的时间"), { target: { value: "10:15" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并开启" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/schedule") && c.body.time === "10:15" && c.body.enabled === true)).toBe(true));
    expect(calls.some((c) => c.url.endsWith("/start"))).toBe(false);
  });

  it("recovers a failed read without losing the manual topic entry", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));
    show();
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(await screen.findByRole("article", { name: "主题 免费版用了一个月，应该升级吗？" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "自己写主题" })).toBeTruthy();
  });
});
