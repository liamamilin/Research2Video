import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { AutopilotCard } from "./AutopilotCard";
import type { BoardItem } from "@/api/video";

const BODY = {
  plans: [{ id: "p1", name: "每周一批", expression: "0 5 * * 2", next_fire: 1792444800, enabled: 1 }],
  runs: [{
    id: "r1", status: "RUNNING", stage: "ITEMS", note: "", created_at: "2026-10-20T05:00:00+0800",
    items: [
      { id: "i1", slot: 0, column_key: "flash", topic: { id: "x", title: "GPT-5.5 退役" }, script_id: "s1", render_id: null,
        state: "HELD", note: "写稿失败 3 次：模型超时", gate: {} },
      { id: "i2", slot: 1, column_key: "flash", topic: { id: "y", title: "Gemini 4 上线" }, script_id: "s2", render_id: "r",
        state: "DONE", note: "成片完成", gate: { label: "serious", text: "有严重问题", reasons: ["事实审计有 1 条严重问题"], approved: "auto", ignored: { must: 2, warn: 5 } } },
    ],
  }],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useAuthStore.setState({ user: null });
});

describe("Autopilot card", () => {
  it("shows selection and links to a covered event even when a current draft has another note", async () => {
    const body = { ...BODY, runs: [{ ...BODY.runs[0], items: [{ ...BODY.runs[0].items[0], gate: {
      story_selection: { pool_id: "pool", candidates: 2, selected: "恐龙新发现", skipped: 1,
        decisions: [{ decision: "skip", title: "小蛇滚动逃跑", peer_id: "older", reason: "主事件已覆盖" }] },
    } }] }] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(body) }) as Response));
    render(<MemoryRouter><AutopilotCard /></MemoryRouter>);
    expect(await screen.findByText(/候选 2，已选「恐龙新发现」/)).toBeTruthy();
    expect(screen.getByText(/跳过「小蛇滚动逃跑」/)).toBeTruthy();
    expect(screen.getByText("查看已覆盖文稿").getAttribute("href")).toBe("/video/older");
  });

  it("uses the current draft title and audit label instead of an obsolete batch snapshot", async () => {
    const finished = { ...BODY, runs: [{ ...BODY.runs[0], status: "DONE" }] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(finished) }) as Response));
    const current: Pick<BoardItem, "script_id" | "title" | "version" | "note" | "credibility"> = { script_id: "s2", title: "重新核实后的新稿", version: 4, note: "当前草稿待定稿", credibility: { tier: "trusted", text: "有出处可信", reasons: [], cited_share: 1 } };
    render(<MemoryRouter><AutopilotCard currentItems={[current]} /></MemoryRouter>);
    expect(await screen.findByText("重新核实后的新稿")).toBeTruthy();
    expect(screen.queryByText("Gemini 4 上线")).toBeNull();
    expect(screen.queryByText("有严重问题")).toBeNull();
    expect(screen.getByText("当前 v4：当前草稿待定稿")).toBeTruthy();
    expect(screen.getByText("批次结果：成片完成")).toBeTruthy();
  });

  it("shows the weekly plan, where each video of the latest batch is, and what an automatic approval let through", async () => {
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(BODY) }) as Response));
    render(<MemoryRouter><AutopilotCard /></MemoryRouter>);
    expect(await screen.findByText(/计划「每周一批」/)).toBeTruthy();
    expect(screen.getByText("出了故障")).toBeTruthy();
    expect(screen.getByText("GPT-5.5 退役").closest("a")?.getAttribute("href")).toBe("/video/s1");
    expect(screen.getByText("有严重问题")).toBeTruthy(); // the label of the automatically approved script is next to its video
    expect(screen.queryByText(/现在开始一批/)).toBeNull(); // a batch is running
  });

  it("does not promise a next run for a plan that is switched off", async () => {
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    const off = { ...BODY, plans: [{ ...BODY.plans[0], enabled: 0 }] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(off) }) as Response));
    render(<MemoryRouter><AutopilotCard /></MemoryRouter>);
    const line = await screen.findByText(/计划「每周一批」/);
    expect(line.textContent).toContain("已停用，不会自动运行");
    expect(line.textContent).not.toContain("下次");
  });

  it("is hidden from a viewer when nothing has ever run", async () => {
    useAuthStore.setState({ user: { username: "v", role: "viewer" } as never });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify({ plans: [], runs: [] }) }) as Response));
    const { container } = render(<MemoryRouter><AutopilotCard /></MemoryRouter>);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });

  it("sets how many topics each cell writes and starts that batch", async () => {
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    const calls: { url: string; body?: string; method?: string }[] = [];
    const coverage = [
      { cell: "flash", label: "快讯", status: "enabled", sources: ["flash"], open: 5, written: 0, dismissed: 0, multi: false, autopilot: true, report_date: "2026-10-20" },
      { cell: "ai-developer-roundup-long", label: "盘点", status: "experimental", sources: ["flash"], open: 5, written: 0, dismissed: 0, multi: true, autopilot: false, report_date: null },
    ];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), body: init?.body as string | undefined, method: init?.method });
      const done = { ...BODY, runs: [{ ...BODY.runs[0], status: "DONE" }] };
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(String(url).includes("/coverage") ? coverage : done) } as Response;
    }));
    render(<MemoryRouter><AutopilotCard /></MemoryRouter>);
    fireEvent.click(await screen.findByText("设置这一批"));
    const box = await screen.findByLabelText("快讯");
    expect(screen.queryByLabelText("盘点")).toBeNull(); // a roundup is not picked by count
    fireEvent.change(box, { target: { value: "2" } });
    fireEvent.click(screen.getByText(/按这个现在开始一批/));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.includes("/autopilot/runs"))).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.includes("/autopilot/runs"))!;
    expect(JSON.parse(post.body!).spec.picks).toEqual([{ cell: "flash", count: 2 }]);
  });
});
