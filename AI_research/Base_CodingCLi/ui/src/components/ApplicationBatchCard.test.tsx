import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ApplicationBatch } from '@/api/assistant-workspace';
import { useAuthStore } from '@/lib/auth-store';
import { ApplicationBatchCard } from './ApplicationBatchCard';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useAuthStore.setState({ user: null }); });
const batch = (status = 'prepared'): ApplicationBatch => ({ id: 'one', title: '研究并制作视频', kind: 'produce', status, active: status === 'running',
  created: 1, updated: 1, href: '/ask?tasks=1&batch=one', scope: { matrices: ['dev'] }, counts: { completed: 1, failed: 1 },
  items: [{ id: 'a', target: 'research:a', title: '研究 A', stage: '报告完成', state: 'completed', error: '',
    result: { report: { path: 'output/a.md', href: '/reports/view?path=output/a.md' } } },
  { id: 'b', target: 'produce', title: '视频 B', stage: '出片', state: 'failed', error: '额度不足', result: { href: '/video/b' } }] });

it('shows actionable publication recovery alongside preserved completed results', () => {
  const value = batch('waiting'); value.kind = 'publish';
  value.items[1].result.recovery = {kind:'connection',label:'检查平台连接',message:'登录恢复后继续原投稿。',
    href:'/platforms?platform=xiaohongshu&task=exact',resumable:true};
  value.items[0].result.publication = {label:'已公开',state:'completed',active:false,needs_attention:false,url:'https://www.xiaohongshu.com/explore/exact'};
  render(<MemoryRouter><ApplicationBatchCard batch={value} reload={vi.fn()} focused /></MemoryRouter>);
  expect(screen.getByRole('link', {name:'检查平台连接 ↗'}).getAttribute('href')).toContain('task=exact');
  expect(screen.getByRole('link', {name:'查看平台作品 ↗'}).getAttribute('href')).toContain('/explore/exact');
});

it('shows per-item progress and an explicit start without starting on render', async () => {
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin', created_at: null, last_login_at: null } });
  const fetcher = vi.fn(async (_url: string, _options: RequestInit) => ({ ok: true, status: 200, text: async () => '{}' })); vi.stubGlobal('fetch', fetcher);
  const reload = vi.fn();
  render(<MemoryRouter><ApplicationBatchCard batch={batch()} reload={reload} focused /></MemoryRouter>);
  expect(screen.getByText('额度不足')).toBeTruthy();
  expect(screen.getByRole('link', { name: '查看本次报告 ↗' }).getAttribute('href')).toBe('/reports/view?path=output/a.md');
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('执行此批次'));
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(JSON.parse(String(fetcher.mock.calls[0][1].body)).action).toBe('start');
});

it('preserves successful items when explicitly continuing and hides writes from viewers', async () => {
  const fetcher = vi.fn(async (_url: string, _options: RequestInit) => ({ ok: true, status: 200, text: async () => '{}' })); vi.stubGlobal('fetch', fetcher);
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin', created_at: null, last_login_at: null } });
  const { rerender } = render(<MemoryRouter><ApplicationBatchCard batch={batch('failed')} reload={vi.fn()} /></MemoryRouter>);
  fireEvent.click(screen.getByText('继续未完成项'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(JSON.parse(String(fetcher.mock.calls[0][1].body)).action).toBe('retry');
  useAuthStore.setState({ user: { id: 2, username: 'viewer', role: 'viewer', created_at: null, last_login_at: null } });
  rerender(<MemoryRouter><ApplicationBatchCard batch={batch('failed')} reload={vi.fn()} /></MemoryRouter>);
  expect(screen.queryByText('继续未完成项')).toBeNull();
});

it('keeps a failed API response visible without claiming completion', async () => {
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin', created_at: null, last_login_at: null } });
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 409, text: async () => JSON.stringify({ error: { code: 'conflict', message: '范围已变化' } }) })));
  const reload = vi.fn();
  render(<MemoryRouter><ApplicationBatchCard batch={batch()} reload={reload} /></MemoryRouter>);
  fireEvent.click(screen.getByText('执行此批次'));
  expect((await screen.findByRole('alert')).textContent).toContain('范围已变化');
  expect(reload).not.toHaveBeenCalled();
});
