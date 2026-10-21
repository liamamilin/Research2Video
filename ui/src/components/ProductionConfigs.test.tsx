import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MatrixMember } from "@/api/matrices";
import { ToastProvider } from "@/lib/toast";
import { ProductionConfigs } from "./ProductionConfigs";

const member = (job: string, name: string, topics: string): MatrixMember =>
  ({ job, name, exists: true, copy_as: "", copyable: false, topics, schedule: null }) as MatrixMember;
const MEMBERS = [
  member("monitoring/ai_flash_discovery", "AI Flash Discovery", "flash"),
  member("practical_ai_intelligence/07_product_content_opportunities", "Opportunities", "opportunities"),
  member("research/deep", "AI 深度", ""),
];
const config = (key: string, name: string, split: string, reverify: boolean) => ({
  key, name, job: "monitoring/ai_flash_discovery", recipe: key, errors: [], source: "flash", split,
  split_name: split === "some" ? "挑几条合一期" : "每条一期", reverify, audience: "开发者", target_minutes: 1.25, label: name, scripts: 0,
});
const CONFIGS = [config("flash", "快讯 · 开发者", "each", true), config("rnd", "盘点 · 开发者", "some", false),
  { ...config("other", "别的矩阵的", "each", true), job: "medical/flash_discovery" }];
const PENDING = { report_date: "2026-10-19", report_path: "output/flash/x.md", items: [
  { id: "2026-10-19/F1", code: "F1", title: "第一条", fields: {}, written: true, dismissed: false, scripts: [{ id: "s1", status: "DRAFT" }] },
  { id: "2026-10-19/F2", code: "F2", title: "第二条", fields: {}, written: false, dismissed: false, scripts: [] },
  { id: "2026-10-19/F3", code: "F3", title: "第三条", fields: {}, written: false, dismissed: false, scripts: [] },
] };

let posts: { url: string; body: unknown }[] = [];

beforeEach(() => {
  posts = [];
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "POST") posts.push({ url: u, body: JSON.parse(String(init.body)) });
    const body = u.includes("/write") ? [{ id: "new" }] : u.includes("/pending") ? PENDING : u.includes("/api/video/cells") ? [] : CONFIGS;
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const show = (canEdit = true) =>
  render(<ToastProvider><MemoryRouter><ProductionConfigs members={MEMBERS} canEdit={canEdit} /></MemoryRouter></ToastProvider>);

describe("ProductionConfigs", () => {
  it("lists each source Job's configs, says which Job has none, and leaves out other matrices'", async () => {
    show();
    expect(await screen.findByLabelText("配置 快讯 · 开发者")).toBeTruthy();
    expect(screen.getByText(/每条一期 · 挑中的条目会重新调研一遍/)).toBeTruthy();
    expect(screen.getByText(/挑几条合一期 · 用报告里的条目，不调研/)).toBeTruthy();
    expect(screen.getByText("这个 Job 还没配视频，报告只存着，不会写稿。")).toBeTruthy();  // the opportunities Job
    expect(screen.queryByText("AI 深度")).toBeNull();  // its reports are not item lists
    expect(screen.getByText(/另外 1 个 Job 的报告是整份的/)).toBeTruthy();
    expect(screen.queryByText("别的矩阵的")).toBeNull();
  });

  it("writes the picked items one script each, after saying it researches each", async () => {
    show();
    fireEvent.click((await screen.findAllByText("挑条目写"))[0]);
    expect(((await screen.findByLabelText("选 F1")) as HTMLInputElement).disabled).toBe(true);  // written already
    fireEvent.click(screen.getByLabelText("选 F2"));
    fireEvent.click(screen.getByText("写选中的 1 条，每条一期"));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({ url: "/api/production/flash/write", body: { topic_ids: ["2026-10-19/F2"] } });
    expect(String((window.confirm as unknown as { mock: { calls: string[][] } }).mock.calls[0][0])).toContain("每篇约 4–20 次搜索");
  });

  it("a roundup config needs at least two items", async () => {
    show();
    fireEvent.click((await screen.findAllByText("挑条目写"))[1]);
    fireEvent.click(await screen.findByLabelText("选 F2"));
    const button = screen.getByText(/合成一期（已选 1 条/) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("选 F3"));
    expect((screen.getByText(/合成一期（已选 2 条/) as HTMLButtonElement).disabled).toBe(false);
  });

  it("is read-only for a viewer", async () => {
    show(false);
    fireEvent.click((await screen.findAllByText("挑条目写"))[0]);
    expect(await screen.findByText(/第二条/)).toBeTruthy();
    expect(screen.queryByLabelText("选 F2")).toBeNull();
    expect(screen.queryByText("删除这套")).toBeNull();
    expect(screen.queryByText("加一套配置")).toBeNull();
  });
});
