import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AssistantTaskCenter } from './AssistantTaskCenter';
import type { AssistantTask } from '@/api/assistant-workspace';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const task = (id: string, status: string): AssistantTask => ({ id, status, active: status === 'running', created: 1,
  conversation_id: `${id}-conversation`, message: `${id}研究`, changes: [], videos: [],
  runs: [{ source: 'manual', run_id: `${id}-run`, job_name: 'research/memory', label: status === 'running' ? '研究中' : '研究失败',
    status, state: status, active: status === 'running', href: '/jobs/research%2Fmemory' }] });
function server(tasks: AssistantTask[], total = tasks.length) {
  const fetcher = vi.fn(async (url: string) => {
    if (url === '/api/assistant/batches') return { ok: true, status: 200, text: async () => JSON.stringify({ items: [] }) };
    if (!url.startsWith('/api/assistant/tasks')) throw new Error('Unexpected endpoint');
    return { ok: true, status: 200, text: async () => JSON.stringify({ tasks, total, active: tasks.some(t => t.active) }) };
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}

it('filters across conversations and opens the original conversation without executing anything', async () => {
  const fetcher = server([task('first', 'running'), task('second', 'failed')]);
  const open = vi.fn();
  render(<MemoryRouter><AssistantTaskCenter revision={0} busy={false} onOpenConversation={open} /></MemoryRouter>);
  await screen.findByText('first研究');
  fireEvent.click(screen.getByRole('button', { name: '待处理 1' }));
  expect(screen.queryByText('first研究')).toBeNull();
  expect(screen.getByText('second研究')).toBeTruthy();
  fireEvent.click(screen.getByText('检查本次运行'));
  expect(open).toHaveBeenCalledWith('second-conversation', expect.stringContaining('second-run'));
  expect(fetcher.mock.calls.filter(([url]) => url.startsWith('/api/assistant/tasks'))).toHaveLength(1);
  fireEvent.click(screen.getByText('返回原对话'));
  expect(open).toHaveBeenLastCalledWith('second-conversation');
});

it('preserves a pending chat and disables cross-conversation actions while busy', async () => {
  server([task('first', 'running')]);
  const open = vi.fn();
  render(<MemoryRouter><AssistantTaskCenter revision={0} busy onOpenConversation={open} /></MemoryRouter>);
  await screen.findByText('first研究');
  expect((screen.getByText('返回原对话') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('取消本次研究'));
  expect(open).not.toHaveBeenCalled();
});

it('loads earlier work explicitly and searches only the visible scope', async () => {
  const fetcher = server([task('first', 'failed')], 25);
  render(<MemoryRouter><AssistantTaskCenter revision={0} busy={false} onOpenConversation={vi.fn()} /></MemoryRouter>);
  await screen.findByText('first研究');
  fireEvent.change(screen.getByLabelText('搜索助手任务'), { target: { value: '不存在' } });
  expect(screen.getByText('当前筛选下没有任务。')).toBeTruthy();
  fireEvent.click(screen.getByText('载入更早任务'));
  await screen.findByText(/已载入最近 1 条操作请求，共 25 条/);
  expect(fetcher.mock.calls.some(([url]) => url.includes('limit=40'))).toBe(true);
});

it('refreshes the native projection until completion without polling a model', async () => {
  vi.useFakeTimers();
  const value = task('first', 'running');
  const fetcher = server([value]);
  await act(async () => { render(<MemoryRouter><AssistantTaskCenter revision={0} busy={false} onOpenConversation={vi.fn()} /></MemoryRouter>); });
  value.status = 'completed'; value.active = false; value.runs![0].status = 'success'; value.runs![0].active = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(fetcher.mock.calls.filter(([url]) => url.startsWith('/api/assistant/tasks'))).toHaveLength(2);
});
