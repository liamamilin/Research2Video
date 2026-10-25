import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AssistantWorkspacePanel } from './AssistantWorkspace';
import { assistantContext } from '@/lib/assistant-context';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({
    active: false, operations: [{ id: 'op', status: 'completed', label: '提交出片', submitted: true, created: 1 }],
    objects: [{ kind: 'video', id: 'v', title: '待打磨的视频', href: '/video/v', status: 'DRAFT', version: 5, video_version: 4,
      links: [{ label: '文稿配方与提示词', href: '/video-recipes/flash', scope: '该配方的后续写稿', next: '保存后重新生成文稿' }] }],
  }) })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows manuscript and video versions and fills an action instead of submitting it', async () => {
  const prompt = vi.fn();
  render(<MemoryRouter><AssistantWorkspacePanel conversationId={'a'.repeat(32)} context={null} revision={0} onPrompt={prompt} /></MemoryRouter>);
  expect(await screen.findByText('当前文稿 v5')).toBeTruthy();
  expect(screen.getByText('视频对应：文稿 v4')).toBeTruthy();
  expect(screen.getByText('视频仍使用旧版文稿')).toBeTruthy();
  expect(screen.getByText(/已提交，任务进度见上方/)).toBeTruthy();
  fireEvent.click(screen.getByText('重新生成文稿'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('沿用已有研究报告'));
  expect(vi.mocked(fetch).mock.calls).toHaveLength(1);
});

it('derives references from all primary detail pages', () => {
  expect(assistantContext('/video/v1', '')).toEqual({ kind: 'video', id: 'v1' });
  expect(assistantContext('/matrices/ai/video/research%2Fmemory', '')).toEqual({ kind: 'job', id: 'research/memory' });
  expect(assistantContext('/video-recipes/parts/writing/common', '')).toEqual({ kind: 'part', id: 'writing/common' });
  expect(assistantContext('/video-recipes/flash', '')).toEqual({ kind: 'recipe', id: 'flash' });
  expect(assistantContext('/reports/view', '?path=one%2Freport.md')).toEqual({ kind: 'report', id: 'one/report.md' });
  expect(assistantContext('/platforms', '?platform=douyin')).toEqual({ kind: 'platform', id: 'douyin' });
});

it('offers matrix-wide management and publish preparation without sending requests', async () => {
  vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify({ active: false, operations: [],
    objects: [{ kind: 'matrix', id: 'tools', title: '工具矩阵', href: '/matrices/tools', status: '矩阵', render: { video_speed: 1.25 }, links: [],
      members: [{ job: 'daily', name: '每日工具', enabled: true, href: '/jobs/daily', schedule: null }] }] }) } as Response);
  const prompt = vi.fn();
  render(<MemoryRouter><AssistantWorkspacePanel conversationId="c" context={null} revision={0} onPrompt={prompt} /></MemoryRouter>);
  expect(await screen.findByText(/矩阵默认倍速：1.25/)).toBeTruthy();
  fireEvent.click(screen.getByText('准备发布'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('先查询，不上传'));
  expect(fetch).toHaveBeenCalledTimes(1);
});
