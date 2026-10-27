import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
function server(options: { requests?: unknown[]; context?: unknown; send?: () => Response } = {}) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/assistant/conversation" || url === `/api/assistant/conversations/${cid}`) return json({ id: cid, context: options.context, requests: options.requests || [] });
    if (url.startsWith('/api/assistant/overview')) return json({ at: '2026-10-26T08:00:00+08:00', platform: 'bilibili', active: false,
      platforms: [], matrices: [], summary: { attention: 1, working: 0, publish: 0, jobs: 0 },
      schedules: { available: false, note: '仅管理员可查看周期' }, warnings: [], scope: '全部矩阵',
      items: { total: 1, offset: 0, next_offset: null, complete: true, rows: [{ id: 'v', kind: 'video', matrix_name: 'AI',
        title: '总览中的文稿', label: '待定稿', href: '/video/v?version=2', prompt: '分析文稿 v 的生成设置。' }] } });
    if (url.startsWith('/api/assistant/workspace')) return json({ objects: [], operations: [], active: false });
    if (url.startsWith('/api/assistant/tasks')) return json({ tasks: [], total: 0, active: false });
    if (url.startsWith('/api/assistant/batches')) return json({ items: [] });
    if (url === "/api/assistant/conversations") return init?.method === 'POST' ? json({ id: "b".repeat(32) }) : json([{ id: cid, title: '之前的工作', updated: 1, request_count: 1 }]);
    if (url.endsWith('/context')) return json({ ok: true });
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
  it('opens long conversations at recent messages and reveals older replies without another request', async () => {
    const fetcher = server({ requests: Array.from({ length: 15 }, (_, index) => ({ id: `r${index}`,
      message: `问题 ${index}`, status: 'completed', result: { answer: `历史回答 ${index}`, actions: [] } })) });
    await renderAsk();
    expect(screen.getByText('历史回答 14')).toBeTruthy();
    expect(screen.queryByText('历史回答 0')).toBeNull();
    const previous = fetcher.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '显示更早的对话（10 条）' }));
    expect(screen.getByText('历史回答 0')).toBeTruthy();
    expect(screen.getByText('历史回答 14')).toBeTruthy();
    expect(fetcher.mock.calls.length).toBe(previous);
  });
  it.each([
    ['/ask?tasks=1', '/api/assistant/tasks'],
    ['/ask', '/api/assistant/workspace'],
  ])('loads %s panel once after initial conversation recovery', async (path, endpoint) => {
    const base = server();
    let finish!: () => void;
    const recovery = new Promise<void>(resolve => { finish = resolve; });
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/assistant/conversation') await recovery;
      return base(url, init);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<MemoryRouter initialEntries={[path]}><AskPage /></MemoryRouter>);
    expect(await screen.findByText('正在恢复工作区…')).toBeTruthy();
    expect(fetcher.mock.calls.filter(([url]) => url.startsWith(endpoint))).toHaveLength(0);
    await act(async () => { finish(); });
    await waitFor(() => expect(screen.getByLabelText('消息')).toHaveProperty('disabled', false));
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.startsWith(endpoint))).toHaveLength(1));
    expect(screen.queryByText('正在恢复工作区…')).toBeNull();
  });
  it('opens the global overview and returns to the existing conversation without sending or losing its draft', async () => {
    const fetcher = server({ requests: [{ id: 'old', message: '旧请求', status: 'completed', result: { answer: '保留这段对话', actions: [] } }] });
    await renderAsk();
    fireEvent.change(screen.getByLabelText('消息'), { target: { value: '未发送的草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '全局总览' }));
    expect(await screen.findByRole('heading', { name: '全局总览' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '返回对话' }));
    expect(screen.getByText('保留这段对话')).toBeTruthy();
    expect(screen.getByLabelText('消息')).toHaveProperty('value', '未发送的草稿');
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/stream'))).toBe(false);
    expect(fetcher.mock.calls.some(([url]) => url === '/api/assistant/conversations')).toBe(false);
  });
  it('prefills a global action with no stale object context or duplicate conversation recovery', async () => {
    const fetcher = server({ context: { kind: 'recipe', id: 'old-recipe' } });
    render(<MemoryRouter initialEntries={['/ask']}><AskPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText('消息')).toHaveProperty('disabled', false));
    expect(screen.getByText('当前对象：配方')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '全局总览' }));
    fireEvent.click(await screen.findByText('交给助手'));
    expect(screen.getByLabelText('消息')).toHaveProperty('value', '分析文稿 v 的生成设置。');
    expect(screen.queryByText('当前对象：配方')).toBeNull();
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/conversation') && !url.includes('/context'))).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/stream'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText('已完成。');
    const sent = fetcher.mock.calls.find(([url]) => url.endsWith('/stream'));
    expect(JSON.parse(String(sent?.[1]?.body)).context).toBeNull();
  });
  it('opens the task center directly from a verified action link', async () => {
    const fetcher = server();
    render(<MemoryRouter initialEntries={['/ask?tasks=1']}><AskPage /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: '任务中心' })).toBeTruthy();
    expect(await screen.findByText(/暂无助手操作任务/)).toBeTruthy();
    expect(fetcher.mock.calls.some(([url]) => url.startsWith('/api/assistant/workspace'))).toBe(false);
  });
  it('opens a task-center conversation link instead of the tab last-used conversation', async () => {
    const target = 'd'.repeat(32);
    sessionStorage.setItem('jobs-assistant:admin:id', cid);
    const fetcher = vi.fn(async (url: string) => {
      if (url === `/api/assistant/conversations/${target}`) return json({ id: target, requests: [{ id: 'r', message: '原任务',
        status: 'completed', result: { answer: '原对话的结果', actions: [] } }] });
      if (url.startsWith('/api/assistant/workspace')) return json({ objects: [], operations: [], active: false });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<MemoryRouter initialEntries={[`/ask?conversation=${target}`]}><AskPage /></MemoryRouter>);
    expect(await screen.findByText('原对话的结果')).toBeTruthy();
    expect(fetcher.mock.calls.some(([url]) => url === `/api/assistant/conversations/${cid}`)).toBe(false);
  });

  it('continues a task in its original conversation with a prefilled request and no automatic send', async () => {
    const target = 'e'.repeat(32);
    const fetcher = vi.fn(async (url: string) => {
      if (url === '/api/assistant/conversation' || url === `/api/assistant/conversations/${cid}`) return json({ id: cid, requests: [] });
      if (url === `/api/assistant/conversations/${target}`) return json({ id: target, context: { kind: 'job', id: 'research/memory' },
        requests: [{ id: 'old', message: '研究记忆', status: 'completed', result: { answer: '旧运行的回执', actions: [] } }] });
      if (url.startsWith('/api/assistant/workspace')) return json({ objects: [], operations: [], active: false });
      if (url.startsWith('/api/assistant/tasks')) return json({ active: false, total: 1, tasks: [{ id: 'old', conversation_id: target,
        message: '研究记忆', created: 1, status: 'failed', active: false, changes: [], videos: [],
        runs: [{ source: 'manual', run_id: 'exact-old-run', job_name: 'research/memory', status: 'failed', state: 'failed', active: false,
          label: '研究失败', href: '/jobs/research%2Fmemory' }] }] });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal('fetch', fetcher);
    await renderAsk(); fireEvent.click(screen.getByRole('button', { name: '任务中心' }));
    fireEvent.click(await screen.findByText('检查本次运行'));
    await screen.findByText('旧运行的回执');
    expect((screen.getByLabelText('消息') as HTMLTextAreaElement).value).toContain('exact-old-run');
    expect(screen.getByText('当前对象：研究任务')).toBeTruthy();
    expect(fetcher.mock.calls.some(([url]) => url.includes('/stream'))).toBe(false);
  });
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
    expect(fetch.mock.calls.filter(([url]) => /\/conversations\//.test(url)).slice(-1)[0]?.[0]).toBe(`/api/assistant/conversations/${cid}`);
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


it("fills an example without submitting a model request", async () => {
  const fetch = server(); await renderAsk();
  fireEvent.click(screen.getByRole("button", { name: "看看有哪些文稿等我审" }));
  expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("看看有哪些文稿等我审");
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/stream"))).toHaveLength(0);
});

it('switches historical conversations without sending another model request', async () => {
  const fetch = server(); await renderAsk();
  fireEvent.click(screen.getByText('历史对话'));
  fireEvent.click(await screen.findByText('之前的工作'));
  await waitFor(() => expect((screen.getByLabelText('消息') as HTMLTextAreaElement).disabled).toBe(false));
  expect(fetch.mock.calls.some(([url]) => url === `/api/assistant/conversations/${cid}`)).toBe(true);
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/stream'))).toBe(false);
});

it('sends the selected page object as structured context without auto-submitting it', async () => {
  const fetch = server();
  render(<MemoryRouter initialEntries={['/ask?recipe=flash']}><AskPage /></MemoryRouter>);
  await waitFor(() => expect((screen.getByLabelText('消息') as HTMLTextAreaElement).disabled).toBe(false));
  expect(screen.getByText('当前对象：配方')).toBeTruthy();
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/stream'))).toBe(false);
  send('读一下这个配方'); await screen.findByText('已完成。');
  const request = fetch.mock.calls.find(([url]) => url.endsWith('/stream'));
  expect(JSON.parse(String(request?.[1]?.body)).context).toEqual({ kind: 'recipe', id: 'flash' });
});


it('persists removal of the current object without sending a model request', async () => {
  const fetch = server();
  render(<MemoryRouter initialEntries={['/ask?recipe=flash']}><AskPage /></MemoryRouter>);
  await waitFor(() => expect((screen.getByLabelText('消息') as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.click(screen.getByLabelText('移除当前对象'));
  await waitFor(() => expect(screen.queryByText('当前对象：配方')).toBeNull());
  const request = fetch.mock.calls.find(([url]) => url.endsWith('/context'));
  expect(JSON.parse(String(request?.[1]?.body)).context).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/stream'))).toBe(false);
});
