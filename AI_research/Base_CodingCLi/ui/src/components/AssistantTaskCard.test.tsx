import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AssistantTask } from '@/api/assistant-workspace';
import { AssistantTaskCard } from './AssistantTaskCard';
import { AssistantWorkspacePanel } from './AssistantWorkspace';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
function task(): AssistantTask {
  return { id: 'request', message: '沿用原报告重新生成文稿和视频', created: 1, status: 'running', active: true,
    changes: [{ id: 'config', label: '保存配方', status: 'completed', fields: ['writing.style'], impact: '后续生成生效', href: '/video-recipes/flash' }],
    videos: [{ script_id: 'one', title: '本次样片', href: '/video/one', status: 'rendering', label: '视频制作中', active: true,
      before_version: 1, target_version: 2, current_version: 2, script_ready: true, render_requested: true,
      render: { id: 'new', version: 2, status: 'RUNNING' }, previous_video_version: 1,
      report: { path: 'output/source.md', href: '/reports/view?path=output%2Fsource.md' },
      review: { state: 'not_requested', label: '按需审校，尚未运行', verdict: null },
      duration: { estimated_seconds: 123, target_minutes: 1.25, binding: false },
      script_href: '/video/one?tab=draft&version=2', video_href: '/video/one?tab=video&version=2&render=new' }] };
}

it('shows publication recovery and only prefills resuming the exact original task', () => {
  const value = task(); value.videos = []; value.changes = []; value.active = false; value.status = 'waiting';
  value.publications = [{ platform: 'xiaohongshu', id: 'original-upload', title: '当前作品', version: 2,
    account_uid: 'owner', account_name: '原账号', status: 'SUBMITTED', state: 'waiting', active: false,
    href: '/platforms?platform=xiaohongshu&task=original-upload', label: '回查暂不可用',
    recovery: {kind: 'connection', label: '检查平台连接', href: '/platforms?platform=xiaohongshu&task=original-upload',
      message: '处理原账号登录后继续核对。', resumable: true} }];
  const prompt = vi.fn();
  render(<MemoryRouter><AssistantTaskCard task={value} onPrompt={prompt} /></MemoryRouter>);
  expect(screen.getByText('小红书 · 回查暂不可用 · 文稿 v2')).toBeTruthy();
  expect(screen.getByRole('link', {name: '检查平台连接'}).getAttribute('href')).toContain('task=original-upload');
  expect(prompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '处理后继续原任务'}));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('original-upload'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('manage_publishing resume'));
});

it('pins a research run and prefills cancellation and report reuse without starting them', () => {
  const value = task(); value.videos = []; value.changes = [];
  value.runs = [{ source: 'scheduled', run_id: 'exact-run', job_name: 'research/memory', status: 'running', state: 'running',
    active: true, label: '研究中', href: '/scheduler?plan=one' }];
  const prompt = vi.fn();
  const view = render(<MemoryRouter><AssistantTaskCard task={value} onPrompt={prompt} /></MemoryRouter>);
  fireEvent.click(screen.getByText('取消本次研究'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('exact-run（source=scheduled）'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('不暂停后续周期'));
  value.runs[0] = { ...value.runs[0], active: false, status: 'success', state: 'completed', label: '研究完成',
    report: { path: 'first.md', href: '/reports/view?path=first.md', verified_file: true } };
  view.rerender(<MemoryRouter><AssistantTaskCard task={value} onPrompt={prompt} /></MemoryRouter>);
  expect(screen.queryByText('取消本次研究')).toBeNull();
  expect(screen.getByRole('link', { name: '查看本次报告' }).getAttribute('href')).toBe('/reports/view?path=first.md');
  fireEvent.click(screen.getByText('从报告制作视频'));
  expect(prompt).toHaveBeenLastCalledWith(expect.stringContaining('first.md'));
});

it('connects report, changes, pinned versions and soft review meanings in one card', () => {
  render(<MemoryRouter><AssistantTaskCard task={task()} onPrompt={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText('本次文稿：v2 · 已生成（原 v1）')).toBeTruthy();
  expect(screen.getByText('本次视频：文稿 v2 · 视频制作中')).toBeTruthy();
  expect(screen.getByText(/已有成片对应文稿 v1/)).toBeTruthy();
  expect(screen.getByText(/按需审校，尚未运行 · 暂无审校结论/)).toBeTruthy();
  expect(screen.getByText(/口播预估 123 秒.*仅供参考/)).toBeTruthy();
  expect(screen.getByRole('link', { name: /原始报告/ }).getAttribute('href')).toContain('output%2Fsource.md');
  expect(screen.getByText('修改字段：writing.style')).toBeTruthy();
  expect(screen.queryByText('查看本次成片')).toBeNull();
});

it('offers only the failed step without submitting or replaying successful changes', () => {
  const value = task(); value.status = 'failed'; value.active = false;
  Object.assign(value.videos[0], { status: 'failed', label: '需要处理', active: false, error: '配音服务暂不可用', retry_step: 'render' });
  const prompt = vi.fn();
  render(<MemoryRouter><AssistantTaskCard task={value} onPrompt={prompt} /></MemoryRouter>);
  expect(screen.getByText('配音服务暂不可用')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '重试生成视频' }));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('重试「生成视频」这一步'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('沿用已完成的结果'));
});

it('shows configuration scope, recovered conflict and a precise before/after link', () => {
  const value = task(); value.status = 'completed'; value.active = false;
  value.changes.push({ id: 'conflict', label: '第一次保存', status: 'failed', error: '版本冲突', recovered_by: 'config' });
  value.changes[0].configuration = { level: 'recipe', label: '文稿配方', scope: '所有使用此配方的后续生成', next: '重新生成文稿，再生成视频',
    existing_outputs: '历史产物保留', used_by: [], changes: [{ field: 'writing.style', before: '旧表达方向', after: '新表达方向' }] };
  value.videos[0].compare_href = '/video/one?tab=compare&compare=1&version=2&before_render=old&render=new';
  render(<MemoryRouter><AssistantTaskCard task={value} onPrompt={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/修改层级：文稿配方/)).toBeTruthy();
  expect(screen.getByText('旧表达方向')).toBeTruthy();
  expect(screen.getByText(/同一变更随后已保存/)).toBeTruthy();
  expect(screen.getByRole('link', { name: '对比前后' }).getAttribute('href')).toContain('before_render=old&render=new');
});

it('polls live progress to completion without calling the chat/model endpoint', async () => {
  vi.useFakeTimers();
  const value = task();
  let calls = 0;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (!String(input).includes('/api/assistant/workspace')) throw new Error('Unexpected endpoint');
    calls++;
    if (calls > 1) {
      value.status = 'completed'; value.active = false;
      Object.assign(value.videos[0], { status: 'completed', label: '本次生成已完成', active: false });
      value.videos[0].render!.status = 'COMPLETED';
    }
    return { ok: true, status: 200, text: async () => JSON.stringify({ active: value.active, objects: [], operations: [], tasks: [value] }) };
  });
  vi.stubGlobal('fetch', fetcher);
  await act(async () => { render(<MemoryRouter><AssistantWorkspacePanel conversationId="conversation" context={null} revision={0} onPrompt={vi.fn()} /></MemoryRouter>); });
  expect(screen.queryByText('查看本次成片')).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByRole('link', { name: '查看本次成片' }).getAttribute('href')).toBe('/video/one?tab=video&version=2&render=new');
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls.every((args) => String(args[0]).includes('/api/assistant/workspace'))).toBe(true);
});

it('shows prepared exact-version publishing fields and only prefills a user request', () => {
  const value = task(); value.status = 'prepared'; value.active = false; value.videos = []; value.changes = [];
  value.preparations = [{ platform: 'bilibili', render_id: 'render-two', script_id: 's', version: 2, account_uid: 'owner', account_name: '频道账号',
    fields: { title: '待发标题', description: '简介', tags: ['开发者'] }, warnings: ['请检查封面'], href: '/video/s?tab=publish&version=2' }];
  const prompt = vi.fn();
  render(<MemoryRouter><AssistantTaskCard task={value} onPrompt={prompt} /></MemoryRouter>);
  expect(screen.getByText(/发布包已准备，未上传/)).toBeTruthy();
  expect(screen.getByText('B站 · 发布包 · 文稿 v2')).toBeTruthy();
  expect(screen.getByText('账号：频道账号')).toBeTruthy();
  fireEvent.click(screen.getByText('让助手发布'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('render-two'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('owner'));
});

it('keeps submitted uploads pending and offers diagnosis without replaying them', () => {
  const value = task(); value.status = 'waiting'; value.active = false; value.videos = [];
  value.publications = [{ platform: 'douyin', id: 'upload', title: '已提交样片', version: 3, status: 'SUBMITTED',
    state: 'waiting', active: false, href: '/platforms?platform=douyin&task=upload' }];
  const prompt = vi.fn();
  render(<MemoryRouter><AssistantTaskCard task={value} onPrompt={prompt} /></MemoryRouter>);
  expect(screen.getByText('抖音 · 已提交，待公开确认 · 文稿 v3')).toBeTruthy();
  expect(screen.queryByText('抖音 · 已发布')).toBeNull();
  fireEvent.click(screen.getByText('诊断与恢复'));
  expect(prompt).toHaveBeenCalledWith(expect.stringContaining('不重复投稿'));
});
