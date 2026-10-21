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
  split_name: split === "some" ? "挑几条合一期" : "每条一期", reverify, auto: 0, audience: "开发者", target_minutes: 1.25, label: name, scripts: 0,
});
const CONFIGS = [config("flash", "快讯 · 开发者", "each", true), config("rnd", "盘点 · 开发者", "some", false),
  { ...config("other", "别的矩阵的", "each", true), job: "medical/flash_discovery" }];
const PENDING = { report_date: "2026-10-19", report_path: "output/flash/x.md", items: [
  { id: "2026-10-19/F1", code: "F1", title: "第一条", fields: {}, written: true, dismissed: false, scripts: [{ id: "s1", status: "DRAFT" }] },
  { id: "2026-10-19/F2", code: "F2", title: "第二条", fields: {}, written: false, dismissed: false, scripts: [] },
  { id: "2026-10-19/F3", code: "F3", title: "第三条", fields: {}, written: false, dismissed: false, scripts: [] },
] };

const cell = (base: string, key: string, audience: string, name: string, type: string, kind: string, range: number[] = [], optional = false) => ({
  id: key, key, status: "enabled", reason: "", usable: true, label: `${type} · ${name} · B 站横屏短`, recipe: key, base, audience,
  parts: { domain: { key: "ai", name: "AI" }, audience: { key: audience, name }, type: { key: type, name: type }, format: { key: "short", name: "B 站横屏短" } },
  column: { topic_kind: kind, target_minutes: 4 }, topic_sources: ["flash"], minutes_range: range, research_optional: optional,
});
const CELLS = [
  cell("flash", "flash", "developer", "开发者", "快讯", "single", [], true),
  cell("flash", "ai-non_tech-quick_take-short", "non_tech", "普通用户", "快讯", "single", [], true),
  cell("rnd", "rnd", "developer", "开发者", "盘点", "roundup", [3, 6]),
];

let posts: { url: string; body: unknown }[] = [];

beforeEach(() => {
  posts = [];
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "POST" || init?.method === "PATCH") posts.push({ url: u, body: JSON.parse(String(init.body)) });
    const body = u.includes("/write") ? [{ id: "new" }] : u.includes("/pending") ? PENDING : u.includes("/api/video/cells") ? CELLS : CONFIGS;
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

  it("adds a config by picking the Job, the recipe, then who it is for", async () => {
    show();
    await screen.findByLabelText("配置 快讯 · 开发者");
    const recipe = screen.getByLabelText("加配置用的配方") as HTMLSelectElement;
    const audience = screen.getByLabelText("加配置给谁看") as HTMLSelectElement;
    expect(recipe.disabled).toBe(true);
    expect(audience.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("加配置的 Job"), { target: { value: "monitoring/ai_flash_discovery" } });
    expect([...recipe.options].map((o) => o.text)).toEqual(["用哪个配方…", "快讯 · B 站横屏短", "盘点 · B 站横屏短"]);  // one per file
    fireEvent.change(recipe, { target: { value: "flash" } });
    expect(audience.disabled).toBe(false);
    expect([...audience.options].map((o) => o.text)).toEqual(["给开发者看（已挂了一套）", "给普通用户看"]);  // the developer one is on this Job already
    expect(audience.value).toBe("developer");  // the recipe's own audience is the default
    fireEvent.change(audience, { target: { value: "non_tech" } });
    fireEvent.click(screen.getByText("加一套配置"));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({ url: "/api/production", body: { job: "monitoring/ai_flash_discovery", recipe: "flash", audience: "non_tech" } });
  });

  it("asks for a length only where the recipe allows one, and sends it when filled in", async () => {
    show();
    await screen.findByLabelText("配置 快讯 · 开发者");
    fireEvent.change(screen.getByLabelText("加配置的 Job"), { target: { value: "monitoring/ai_flash_discovery" } });
    fireEvent.change(screen.getByLabelText("加配置用的配方"), { target: { value: "flash" } });
    expect(screen.queryByLabelText("加配置的分钟数")).toBeNull();  // 快讯 is 60–90 秒 by its own words
    fireEvent.change(screen.getByLabelText("加配置用的配方"), { target: { value: "rnd" } });
    const input = screen.getByLabelText("加配置的分钟数") as HTMLInputElement;
    expect(input.placeholder).toBe("4");
    expect(screen.getByText(/3–6，不填用配方的/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByText("加一套配置"));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toEqual({ job: "monitoring/ai_flash_discovery", recipe: "rnd", audience: "developer", minutes: 5 });
  });

  it("offers to skip the research only where the recipe says it is optional, and sends reverify false when it is turned off", async () => {
    show();
    await screen.findByLabelText("配置 快讯 · 开发者");
    fireEvent.change(screen.getByLabelText("加配置的 Job"), { target: { value: "monitoring/ai_flash_discovery" } });
    fireEvent.change(screen.getByLabelText("加配置用的配方"), { target: { value: "rnd" } });
    expect(screen.queryByLabelText("重新调研一遍")).toBeNull();  // a roundup does not research anyway
    fireEvent.change(screen.getByLabelText("加配置用的配方"), { target: { value: "flash" } });
    const box = screen.getByLabelText("重新调研一遍") as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    fireEvent.click(screen.getByText("加一套配置"));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toEqual({ job: "monitoring/ai_flash_discovery", recipe: "flash", audience: "developer", reverify: false });
  });

  it("leaves reverify out when the research is kept", async () => {
    show();
    await screen.findByLabelText("配置 快讯 · 开发者");
    fireEvent.change(screen.getByLabelText("加配置的 Job"), { target: { value: "monitoring/ai_flash_discovery" } });
    fireEvent.change(screen.getByLabelText("加配置用的配方"), { target: { value: "flash" } });
    fireEvent.click(screen.getByText("加一套配置"));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toEqual({ job: "monitoring/ai_flash_discovery", recipe: "flash", audience: "developer" });
  });

  it("turns auto on for a recipe that writes one video per item, after saying what it spends, and off again", async () => {
    show();
    await screen.findByLabelText("配置 快讯 · 开发者");
    const boxes = screen.getAllByLabelText("自动写") as HTMLInputElement[];
    expect(boxes.length).toBe(1);  // 盘点 is one video from several items: not written unattended
    fireEvent.change(screen.getByLabelText("自动每次写几条"), { target: { value: "2" } });
    fireEvent.click(boxes[0]);
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({ url: "/api/production/flash", body: { auto: 2 } });
    const said = String((window.confirm as unknown as { mock: { calls: string[][] } }).mock.calls[0][0]);
    expect(said).toContain("每篇约 4–20 次搜索");
    expect(said).toContain("只写打开之后出的报告");
  });

  it("says when a Job with auto has no schedule, so nothing will start it", async () => {
    CONFIGS[0] = { ...CONFIGS[0], auto: 3 };
    show();
    expect(await screen.findByText(/这个 Job 没设周期/)).toBeTruthy();
    expect(screen.getByText(/自动：报告一出来写最多 3 条/)).toBeTruthy();
    CONFIGS[0] = { ...CONFIGS[0], auto: 0 };
  });

  it("on one Job's own page the Job is already chosen", async () => {
    render(<ToastProvider><MemoryRouter><ProductionConfigs members={[MEMBERS[0]]} canEdit={true} /></MemoryRouter></ToastProvider>);
    await screen.findByLabelText("配置 快讯 · 开发者");
    expect(screen.queryByLabelText("加配置的 Job")).toBeNull();
    const recipe = screen.getByLabelText("加配置用的配方") as HTMLSelectElement;
    expect(recipe.disabled).toBe(false);  // nothing to pick first
    fireEvent.change(recipe, { target: { value: "flash" } });
    fireEvent.click(screen.getByText("加一套配置"));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toMatchObject({ job: "monitoring/ai_flash_discovery", recipe: "flash" });
    expect((screen.getByLabelText("加配置用的配方") as HTMLSelectElement).disabled).toBe(false);  // still this Job after adding
  });

  it("is read-only for a viewer", async () => {
    show(false);
    fireEvent.click((await screen.findAllByText("挑条目写"))[0]);
    expect(await screen.findByText(/第二条/)).toBeTruthy();
    expect(screen.queryByLabelText("选 F2")).toBeNull();
    expect(screen.queryByText("删除这套")).toBeNull();
    expect(screen.queryByText("加一套配置")).toBeNull();
    expect(screen.queryByLabelText("自动写")).toBeNull();
  });
});
