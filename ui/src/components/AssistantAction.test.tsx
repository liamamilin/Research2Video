import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AssistantAction, AssistantConfigRecovery } from "./AssistantAction";
const json = (value: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify(value) });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });
it("submits secrets directly, clears inputs, and keeps a new token out of session storage", async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => init?.method === "POST" ? json({ token: "air_display_once" }) : json({ status: "pending" }));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><AssistantAction action={{ tool: "request_protected_input", ok: true, message: "准备输入", input_card: {
    id: "protected-id", title: "创建凭据", description: "秘密不发送给模型", fields: [{ name: "password", label: "密码", type: "password" }],
  } }} /></MemoryRouter>);
  const input = await screen.findByLabelText("密码");
  fireEvent.change(input, { target: { value: "never-send-to-the-model" } });
  fireEvent.click(screen.getByText("提交此操作"));
  expect(await screen.findByText("air_display_once")).toBeTruthy();
  expect(screen.queryByLabelText("密码")).toBeNull();
  expect(fetch.mock.calls[1][0]).toBe("/api/assistant/inputs/protected-id");
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ password: "never-send-to-the-model" });
  expect(fetch.mock.calls.every(([url]) => !url.endsWith("/stream"))).toBe(true);
  expect(JSON.stringify(sessionStorage)).not.toContain("air_display_once");
  fireEvent.click(screen.getByText("隐藏 Token")); expect(screen.queryByText("air_display_once")).toBeNull();
});
it("restores completed protected forms without allowing another submission", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ status: "completed" })));
  render(<MemoryRouter><AssistantAction action={{ tool: "request_protected_input", ok: true, message: "准备输入", input_card: {
    id: "old-id", title: "旧操作", description: "已提交", fields: [],
  } }} /></MemoryRouter>);
  expect(await screen.findByText("操作已完成")).toBeTruthy(); expect(screen.queryByText("提交此操作")).toBeNull();
});
it("rolls back configuration directly without a model request", async () => {
  const fetch = vi.fn(async (_url: string) => json({ ok: true })); vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><AssistantAction action={{ tool: "manage_configuration", ok: true, message: "配置已保存", rollback_id: "backup-id" }} /></MemoryRouter>);
  fireEvent.click(screen.getByText("恢复变更前配置"));
  expect(await screen.findByText("已恢复变更前配置")).toBeTruthy(); expect(fetch.mock.calls[0][0]).toBe("/api/assistant/rollback/backup-id");
});
it('verifies an interrupted protected input and revokes a selected token without replay', async () => {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/verify')) return json({ status: 'interrupted', message: '仅核验元数据', tokens: [{ id: 7, name: 'CI', created_at: '2026-10-16', revoked: false }] });
    if (url === '/api/auth/tokens/7' && init?.method === 'DELETE') return json({ ok: true });
    return json({ status: 'interrupted' });
  });
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><AssistantAction action={{ tool: 'request_protected_input', ok: true, message: '已提交', input_card: { id: 'unknown', title: '创建 Token', description: '独立输入', fields: [] } }} /></MemoryRouter>);
  fireEvent.click(await screen.findByText('核验实际结果'));
  expect(await screen.findByText('仅核验元数据')).toBeTruthy();
  fireEvent.click(screen.getByText('撤销这个 Token'));
  expect(await screen.findByText(/CI · 2026-10-16 · 已撤销/)).toBeTruthy();
  expect(fetch.mock.calls.some(([_, init]) => init?.method === 'POST')).toBe(false);
});
it('discovers configuration recovery independently of conversation history', async () => {
  const fetch = vi.fn(async (url: string) => json(url.endsWith('/recovery') ? { rollback_id: 'saved' } : { ok: true }));
  vi.stubGlobal('fetch', fetch);
  render(<AssistantConfigRecovery />);
  fireEvent.click(await screen.findByText('恢复变更前配置'));
  expect(await screen.findByText('已恢复变更前配置')).toBeTruthy();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/assistant/configuration/recovery', '/api/assistant/rollback/saved']);
});
it('shows the latest run and labels paused future times as a preview', () => {
  render(<MemoryRouter><AssistantAction action={{ tool: 'manage_schedule', ok: true, message: '已查询', detail: {
    enabled: false, timezone: 'Asia/Shanghai', schedule: '0 9 * * 1', next_runs: ['2026-10-19T09:00:00+08:00'],
    last_run: { id: 'run', status: 'success', report: { href: '/reports/view?path=latest.md' } },
  } }} /></MemoryRouter>);
  expect(screen.getByText(/启用后的时间预览/)).toBeTruthy();
  expect(screen.getByText(/最近运行：成功/)).toBeTruthy();
  expect(screen.getByText('打开报告').getAttribute('href')).toBe('/reports/view?path=latest.md');
});
