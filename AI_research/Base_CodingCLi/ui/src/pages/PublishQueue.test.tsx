import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { PublishQueuePage } from "./PublishQueue";

const ROWS = [
  { script_id: "s1", title: "Gemini 4 Argon 上线", column_key: "flash", approved_version: 3, created_at: "2026-10-20T09:00:00+0800", credibility: { tier: "serious", text: "有严重问题", reasons: ["事实审计有 1 条严重问题"], cited_share: 0.8 }, auto: true, render: { id: "r1", status: "COMPLETED", version: 3, error: null, completed_at: "2026-10-20T10:30:00+0800" }, video_url: "/api/video/renders/r1/files/video.mp4", publications: [], warnings: ["快讯超过 24 小时建议复核"] },
  { script_id: "s2", title: "已发的一期", column_key: "flash", approved_version: 1, created_at: "2026-10-19T09:00:00+0800", credibility: { tier: "trusted", text: "有出处可信", reasons: [], cited_share: 1 }, render: null, video_url: null, publications: [{ id: "p1", url: "https://b23.tv/x", published_on: "2026-10-19" }], warnings: [] },
];

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(ROWS) }) as Response));
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null });
  vi.unstubAllGlobals();
});

describe("Publish queue", () => {
  it("bounds initial cards, reveals the rest, and searches the entire list", async () => {
    const many = Array.from({ length: 53 }, (_, index) => ({ ...ROWS[1], script_id: `s${index}`, title: `成片第${index}期` }));
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(many) }) as Response));
    const { container } = render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    await screen.findByText("成片第0期");
    expect(container.querySelectorAll("li")).toHaveLength(24);
    fireEvent.click(screen.getByRole("button", { name: "显示更多成片" }));
    expect(container.querySelectorAll("li")).toHaveLength(48);
    fireEvent.change(screen.getByPlaceholderText("搜索视频或来源任务"), { target: { value: "成片第52期" } });
    expect(await screen.findByText("成片第52期")).toBeTruthy();
    expect(container.querySelectorAll("li")).toHaveLength(1);
  });
  it("shows this login's last list immediately on return, then replaces it with fresh statuses", async () => {
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    await screen.findByText("Gemini 4 Argon 上线");
    cleanup();
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    expect(screen.getByText("Gemini 4 Argon 上线")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("正在更新成片状态…");
    finish({ ok: true, status: 200, text: async () => JSON.stringify([{ ...ROWS[0], title: "最新成片" }]) } as Response);
    expect(await screen.findByText("最新成片")).toBeTruthy();
    expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull();
    expect(screen.queryByText("正在更新成片状态…")).toBeNull();
  });

  it("does not reuse the previous login's list for another login", async () => {
    useAuthStore.setState({ user: { username: "first", role: "editor" } as never });
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    await screen.findByText("Gemini 4 Argon 上线");
    cleanup();
    useAuthStore.setState({ user: { username: "second", role: "viewer" } as never });
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull();
    expect(screen.getByText("正在读取成片…")).toBeTruthy();
  });

  it("shows what is ready to publish, what is published and the freshness warnings", async () => {
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    expect(await screen.findByText("成片完成", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("已发布至 1 个平台", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("有严重问题", { selector: "span" })).toBeTruthy();
    expect(screen.getByText(/成片完成 .*10-20 10:30/)).toBeTruthy();
    expect(screen.getByText("快讯超过 24 小时建议复核")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "看成片" }));
    expect(document.querySelector("video")?.getAttribute("src")).toBe("/api/video/renders/r1/files/video.mp4");
    expect(screen.getByText("各平台发布与记录 →").closest("a")?.getAttribute("href")).toContain("/video/s1?tab=publish&publish_version=3&render=r1");
  });

  it("filters by credibility label, so a video with a serious finding is easy to pick out", async () => {
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    await screen.findByText("Gemini 4 Argon 上线");
    fireEvent.click(screen.getByRole("button", { name: "筛选" }));
    fireEvent.change(screen.getByLabelText("可信度"), { target: { value: "trusted" } });
    await waitFor(() => expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull());
    expect(screen.getByText("已发的一期")).toBeTruthy();
    expect(screen.getByText("显示 1 / 2")).toBeTruthy();
  });

  it("lets an editor open the folder of a finished video, and nobody else", async () => {
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    await screen.findByText("Gemini 4 Argon 上线");
    expect(screen.queryByRole("button", { name: "在访达中显示" })).toBeNull(); // not signed in as an editor
    cleanup();
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    expect(await screen.findAllByRole("button", { name: "在访达中显示" })).toHaveLength(1); // only the finished one
  });
});

it("finds a Bilibili-published asset under Douyin unpublished and retains its distribution entry", async () => {
  const target = (platform: string, status: string) => ({ platform, status, current_account: true, account_uid: platform, publications: [] });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify([
    { ...ROWS[0], title: "已发B站可发抖音", distributions: [target("bilibili", "PUBLISHED"), target("douyin", "UNPUBLISHED")] },
    { ...ROWS[0], script_id: "both", title: "两个平台已发布", distributions: [target("bilibili", "PUBLISHED"), target("douyin", "PUBLISHED")] },
  ]) }) as Response));
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
  await screen.findByText("已发B站可发抖音");
  fireEvent.change(screen.getByLabelText("发布平台"), { target: { value: "douyin" } });
  fireEvent.change(screen.getByLabelText("平台发布状态"), { target: { value: "UNPUBLISHED" } });
  expect(screen.getByText("已发B站可发抖音")).toBeTruthy();
  expect(screen.queryByText("两个平台已发布")).toBeNull();
  expect(screen.getByRole("link", { name: "各平台发布与记录 →" }).getAttribute("href")).toContain("/video/s1?tab=publish&publish_version=3&render=r1");
});

it("counts only prepared targets when no platform is selected", async () => {
  const { matchesTodo } = await import("./PublishQueue");
  const row = { ...ROWS[0], distributions: [
    { platform:"bilibili",current_account:true,status:"PUBLISHED" },
    { platform:"douyin",current_account:true,status:"UNPUBLISHED" },
  ] } as unknown as import("@/api/video").Shipment;
  expect(matchesTodo(row,"已完成", "")).toBe(true);
  expect(matchesTodo(row,"部分完成", "")).toBe(false);
  expect(matchesTodo(row,"待发布", "douyin")).toBe(true);
  row.distributions![1].status="DRAFT";
  expect(matchesTodo(row,"部分完成", "")).toBe(true); expect(matchesTodo(row,"草稿", "")).toBe(true);
  row.distributions![1].status="UNKNOWN";
  expect(matchesTodo(row,"需处理", "")).toBe(true); expect(matchesTodo(row,"发布中", "")).toBe(false);
});
