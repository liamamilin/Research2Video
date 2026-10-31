import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AssistantOverview } from './AssistantOverview';
import { getOperationsOverview, type OperationsOverview } from '@/api/assistant-overview';

vi.mock('@/api/assistant-overview', () => ({ getOperationsOverview: vi.fn() }));
const read = vi.mocked(getOperationsOverview);
export const overviewData = (): OperationsOverview => ({
  at: '2026-10-26T08:30:00+08:00', date: '2026-10-26', timezone: 'CST', matrix: '', platform: 'xiaohongshu',
  active: false, href: '/ask?overview=1', scope: '投稿仅针对所选平台当前账号。', availability: {}, warnings: [],
  platforms: [{ id: 'xiaohongshu', name: '小红书', account: { connected: true, name: '当前账号', uid: 'owner' } },
    { id: 'bilibili', name: '哔哩哔哩', account: { connected: false } }],
  matrices: [{ key: 'ai', name: 'AI 矩阵', href: '/matrices/ai', jobs: 5, attention: 20, working: 1, publish: 3 }],
  summary: { attention: 20, working: 1, publish: 3, jobs: 5, enabled_jobs: 4, today_success: 2, today_failed: 1 },
  schedules: { available: true, enabled: 3, paused: 1, next: null, href: '/scheduler', note: '' },
  items: { view: 'attention', offset: 0, next_offset: 15, total: 20, complete: true, rows: [{
    id: 'video:a', kind: 'video', title: '当前视频', label: '待定稿', detail: '当前文稿 v3；另有旧版成片',
    matrix: 'ai', matrix_name: 'AI 矩阵', version: 3, href: '/video/a?version=3', prompt: '查看文稿 a v3 和当前生成配置。',
  }] },
});
beforeEach(() => { read.mockReset(); read.mockResolvedValue(overviewData()); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
function mount(url = '/ask?overview=1', props = {}) {
  return render(<MemoryRouter initialEntries={[url]}><AssistantOverview onPrompt={vi.fn()} {...props} /></MemoryRouter>);
}

it('shows complete totals, versions and exact native links; handing off only prefills', async () => {
  const prompt = vi.fn(); mount(undefined, { onPrompt: prompt });
  const title = await screen.findByText('当前视频');
  expect(title.getAttribute('href')).toBe('/video/a?version=3');
  expect(screen.getByText('当前文稿 v3；另有旧版成片')).toBeTruthy();
  expect(screen.getByRole('button', { name: /需要处理\s*20/ })).toBeTruthy();
  expect(screen.getByText('20 项')).toBeTruthy();
  fireEvent.click(screen.getByText('交给助手'));
  expect(prompt).toHaveBeenCalledWith('查看文稿 a v3 和当前生成配置。');
  expect(read).toHaveBeenCalledTimes(1);
});

it('pages the full scope and resets pagination on matrix/platform/view changes', async () => {
  mount(); await screen.findByText('当前视频');
  fireEvent.click(screen.getByLabelText('总览下一页'));
  await waitFor(() => expect(read.mock.calls.at(-1)?.[0].offset).toBe(15));
  fireEvent.change(await screen.findByLabelText('总览矩阵'), { target: { value: 'ai' } });
  await waitFor(() => expect(read.mock.calls.at(-1)?.[0]).toEqual({ matrix: 'ai', platform: '', view: 'attention', offset: 0 }));
  fireEvent.click(await screen.findByRole('button', { name: /可发布\s*3/ }));
  await waitFor(() => expect(read.mock.calls.at(-1)?.[0].view).toBe('publish'));
  fireEvent.change(await screen.findByLabelText('总览发布平台'), { target: { value: 'bilibili' } });
  await waitFor(() => expect(read.mock.calls.at(-1)?.[0].platform).toBe('bilibili'));
});

it('does not show the previous scope while the new matrix is loading and cancels stale requests', async () => {
  let finish: (data: OperationsOverview) => void = () => {};
  read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  mount();
  const signal = read.mock.calls[0][1];
  cleanup(); expect(signal?.aborted).toBe(true);
  await act(async () => finish(overviewData()));
  expect(screen.queryByText('当前视频')).toBeNull();
});

it('shows partial errors and unknown totals without pretending that no work remains', async () => {
  const data = overviewData(); data.summary.publish = null; data.warnings = ['可发布视频暂时无法读取'];
  data.items.complete = false;
  read.mockResolvedValue(data); mount();
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '可发布视频暂时无法读取');
  expect(screen.getByRole('button', { name: /可发布\s*—/ })).toBeTruthy();
  expect(screen.getByText('已知 20 项')).toBeTruthy();
});

it('handles a failed initial request with retry and disconnected platform with a direct entry', async () => {
  read.mockRejectedValueOnce(new Error('服务暂时不可用'));
  const data = overviewData(); data.platform = 'bilibili'; data.items.rows = []; data.summary.publish = null;
  read.mockResolvedValue(data); mount('/ask?overview=1&view=publish&platform=bilibili');
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '服务暂时不可用重试');
  fireEvent.click(screen.getByText('重试'));
  expect(await screen.findByText('连接所选平台的账号后，可查看针对该账号的发布候选。')).toBeTruthy();
  expect(screen.getByText('连接平台').getAttribute('href')).toBe('/platforms?platform=bilibili');
});

it('polls local state only while work is active, then stops', async () => {
  vi.useFakeTimers();
  read.mockResolvedValueOnce({ ...overviewData(), active: true });
  await act(async () => { mount(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(45000); });
  expect(read).toHaveBeenCalledTimes(2);
});

it('keeps read-only requests analytical and blocks handoff while a chat is running', async () => {
  const prompt = vi.fn(); const view = mount(undefined, { onPrompt: prompt, readOnly: true });
  fireEvent.click(await screen.findByText('交给助手'));
  expect(prompt.mock.calls[0][0]).toContain('只查询并给出建议');
  view.unmount(); mount(undefined, { onPrompt: prompt, disabled: true });
  expect(await screen.findByText('交给助手')).toHaveProperty('disabled', true);
});
