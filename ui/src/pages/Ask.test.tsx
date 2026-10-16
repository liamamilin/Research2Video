import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AskPage } from "./Ask";
import { useAuthStore } from "@/lib/auth-store";
const cid = "a".repeat(32);
const json = (value: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify(value) }) as Response;
function stream(result: unknown) {
  return { ok: true, status: 200, body: new ReadableStream({ start(c) {
    c.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "done", result })}\n\n`)); c.close();
  } }) } as Response;
}
function server(options: { requests?: unknown[]; send?: () => Response } = {}) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/assistant/conversation" || url === `/api/assistant/conversations/${cid}`) return json({ id: cid, requests: options.requests || [] });
    if (url === "/api/assistant/conversations") return json({ id: "b".repeat(32) });
    if (url.includes("/stop")) return json({ status: "stop_requested" });
    if (url === "/api/assistant/stream") return options.send ? options.send() : stream({ answer: "已完成。", actions: [], conversation_id: cid });
    throw new Error(`Unexpected request ${url} ${init?.method}`);
  });
  vi.stubGlobal("fetch", fetch); return fetch;
}
async function renderAsk() {
  const view = render(<MemoryRouter><AskPage /></MemoryRouter>);
  await waitFor(() => expect((screen.getByLabelText("消息") as HTMLTextAreaElement).disabled).toBe(false)); return view;
}
function send(text: string) {
  fireEvent.change(screen.getByLabelText("消息"), { target: { value: text } }); fireEvent.click(screen.getByText("发送"));
}
beforeEach(() => { sessionStorage.clear(); useAuthStore.setState({ user: { username: "admin", role: "admin" } as never }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("Application assistant", () => {
  it("restores actual server receipts without replay and carries the conversation ID", async () => {
    const fetch = server({ requests: [{ id: "old", message: "创建任务", status: "completed", result: { answer: "已创建。", actions: [{ tool: "create_job", ok: true, job_name: "research/memory", message: "创建记忆任务" }] } }] });
    await renderAsk(); expect(screen.getByText("已创建。")).toBeTruthy();
    expect(screen.getByText("查看任务").getAttribute("href")).toBe("/jobs/research%2Fmemory");
    expect(fetch.mock.calls.filter(([url]) => url.endsWith("/stream"))).toHaveLength(0);
    send("运行它"); await screen.findByText("已完成。");
    const call = fetch.mock.calls.find(([url]) => url.endsWith("/stream")); const body = JSON.parse(String(call?.[1]?.body));
    expect(body.conversation_id).toBe(cid); expect(body.request_id).toMatch(/^[a-f0-9]{32}$/); expect(body.message).toBe("运行它");
  });
  it("streams before completion and preserves a pending request through navigation", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
    const fetch = server({ send: () => ({ ok: true, status: 200, body } as Response) });
    const first = await renderAsk(); send("创建任务");
    const emit = (event: unknown) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
    const encoded = new TextEncoder().encode(`data: ${JSON.stringify({ type: "delta", text: "正在创建记忆任务" })}\n\n`);
    controller.enqueue(encoded.slice(0, encoded.length-7)); controller.enqueue(encoded.slice(encoded.length-7));
    expect(await screen.findByText("正在创建记忆任务")).toBeTruthy();
    const action = { tool: "create_job", ok: true, message: "真实操作已完成", job_name: "research/memory" };
    emit({ type: "action", action }); expect(await screen.findByText("真实操作已完成")).toBeTruthy();
    first.unmount(); render(<MemoryRouter><AskPage /></MemoryRouter>);
    expect(screen.getByText("正在创建记忆任务")).toBeTruthy();
    emit({ type: "reset" }); emit({ type: "delta", text: "整理结果" });
    expect(await screen.findByText("整理结果")).toBeTruthy(); expect(screen.queryByText("正在创建记忆任务")).toBeNull();
    expect(screen.getByText("真实操作已完成")).toBeTruthy();
    emit({ type: "done", result: { answer: "已完成。", actions: [action], conversation_id: cid } }); controller.close();
    await screen.findByText("已完成。"); await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(fetch.mock.calls.filter(([url]) => url.endsWith("/stream"))).toHaveLength(1);
  });
  it("keeps partial output after disconnect without automatic replay", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const fetch = server({ send: () => ({ ok: true, status: 200, body: new ReadableStream({ start(c) { controller = c; } }) } as Response) });
    await renderAsk(); send("创建任务");
    for (const event of [{ type: "delta", text: "任务已建立" }, { type: "action", action: { tool: "create_job", ok: true, message: "已创建 research/new" } }]) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\r\n\r\n`));
    expect(await screen.findByText("任务已建立")).toBeTruthy(); controller.close();
    expect((await screen.findByRole("alert")).textContent).toContain("连接中断"); expect(screen.getByText("已创建 research/new")).toBeTruthy();
    expect(fetch.mock.calls.filter(([url]) => url.endsWith("/stream"))).toHaveLength(1);
  });
  it("stops the assistant response without cancelling research jobs", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const fetch = server({ send: () => ({ ok: true, status: 200, body: new ReadableStream({ start(c) { controller = c; } }) } as Response) });
    await renderAsk(); send("查看任务"); fireEvent.click(screen.getByText("停止响应"));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => /\/requests\/[a-f0-9]{32}\/stop$/.test(url))).toBe(true));
    controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "done", result: { answer: "响应已停止。", actions: [] } })}\n\n`)); controller.close();
    await screen.findByText("响应已停止。"); expect(fetch.mock.calls.some(([url]) => url.includes("/cancel"))).toBe(false);
  });
  it("renders timezone, next runs and report citations", async () => {
    server({ send: () => stream({ answer: "计划已保存。", actions: [{ tool: "manage_schedule", ok: true, message: "计划保存完成", href: "/scheduler", detail: { schedule: "0 9 * * 1", timezone: "Asia/Shanghai", next_runs: ["2026-10-19T09:00:00+08:00"] }, references: [{ type: "report", id: "old.md", href: "/reports/view?path=old.md", title: "旧报告", date: "2026-09-01" }] }] }) });
    await renderAsk(); send("设置周期"); expect(await screen.findByText(/Asia\/Shanghai/)).toBeTruthy(); expect(screen.getByText(/2026-10-19T09:00:00/)).toBeTruthy();
    expect(screen.getByText("2026-09-01 · 旧报告").getAttribute("href")).toBe("/reports/view?path=old.md");
  });
  it("starts a new server conversation and clears cached messages", async () => {
    const fetch = server({ requests: [{ id: "old", message: "旧问题", status: "completed", result: { answer: "旧回答", actions: [] } }] });
    await renderAsk(); fireEvent.click(screen.getByText("新对话")); await waitFor(() => expect(screen.queryByText("旧回答")).toBeNull());
    expect(sessionStorage.getItem("jobs-assistant:admin")).toBe("[]"); expect(fetch.mock.calls.some(([url, init]) => url === "/api/assistant/conversations" && init?.method === "POST")).toBe(true);
  });
  it("blocks submission until recovery succeeds and isolates accounts", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    sessionStorage.setItem("jobs-assistant:admin", JSON.stringify([{ role: "user", content: "admin private" }]));
    useAuthStore.setState({ user: { username: "reader", role: "viewer" } as never });
    render(<MemoryRouter><AskPage /></MemoryRouter>);
    expect(await screen.findByText(/offline/)).toBeTruthy(); expect((screen.getByText("发送") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/当前账号为只读权限/)).toBeTruthy(); expect(screen.queryByText("admin private")).toBeNull();
  });
  it('pins the tab to its own conversation when another conversation becomes latest', async () => {
    const fetch = server({ requests: [{ id: 'a', message: '任务 A', status: 'completed', result: { answer: 'A 的结果', actions: [] } }] });
    const first = await renderAsk();
    expect(sessionStorage.getItem('jobs-assistant:admin:id')).toBe(cid);
    first.unmount();
    fetch.mockImplementation(async (url: string) => {
      if (url === '/api/assistant/conversation') return json({ id: 'b'.repeat(32), requests: [] });
      if (url === `/api/assistant/conversations/${cid}`) return json({ id: cid, requests: [{ id: 'a', message: '任务 A', status: 'completed', result: { answer: 'A 的结果', actions: [] } }] });
      throw new Error(`Unexpected ${url}`);
    });
    await renderAsk();
    expect(screen.getByText('A 的结果')).toBeTruthy();
    expect(fetch.mock.calls[fetch.mock.calls.length - 1]?.[0]).toBe(`/api/assistant/conversations/${cid}`);
  });
  it('reads actual receipts after disconnect without a second model request', async () => {
    const fetch = server({ send: () => ({ ok: true, status: 200, body: new ReadableStream({ start(c) { c.close(); } }) } as Response) });
    await renderAsk(); send('创建任务');
    await screen.findByRole('alert');
    fetch.mockImplementation(async (url: string) => {
      if (url === `/api/assistant/conversations/${cid}`) return json({ id: cid, requests: [{ id: 'saved', message: '创建任务', status: 'interrupted', result: { answer: '已记录的真实结果', actions: [{ tool: 'create_job', ok: true, message: '任务已创建' }] } }] });
      throw new Error(`Unexpected ${url}`);
    });
    fireEvent.click(screen.getByText('核验结果'));
    expect(await screen.findByText('已记录的真实结果')).toBeTruthy();
    expect(screen.getByText('任务已创建')).toBeTruthy();
    expect(fetch.mock.calls.filter(([url]) => url.endsWith('/stream'))).toHaveLength(1);
  });
  it('restores a partial task and continues only after an explicit click', async () => {
    const fetch = server({ requests: [{ id: 'partial', message: '比较报告', status: 'completed', result: {
      answer: '本次只完成一部分', actions: [], continuation: { reason: 'step_limit', steps: 16, reports_complete: 2, remaining_report_count: 3, next_reports: ['next.md'], report_reads: [] },
    } }] });
    await renderAsk();
    expect(screen.getByText(/原请求尚未完成，进度已保存/)).toBeTruthy();
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/stream'))).toBe(false);
    fireEvent.click(screen.getByText('继续处理'));
    await screen.findByText('已完成。');
    const request = fetch.mock.calls.find(([url]) => url.endsWith('/stream'));
    expect(JSON.parse(String(request?.[1]?.body)).message).toContain('已成功的操作不要重复执行');
  });
});
