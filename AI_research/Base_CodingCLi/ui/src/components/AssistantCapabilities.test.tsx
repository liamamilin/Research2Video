import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AssistantCapabilities } from './AssistantCapabilities';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows whole application areas and only prefills a selected goal', async () => {
  const fetcher = vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({
    areas: [{ key: 'sources', title: '信息源', detail: '管理共享来源与任务引用', example: '查看失败的信息源' },
      { key: 'platforms', title: '发布与运营', detail: '发布、数据与评论', example: '查看发布情况' }], boundaries: ['平台登录在页面完成'] }) }));
  vi.stubGlobal('fetch', fetcher); const prompt = vi.fn();
  render(<AssistantCapabilities onPrompt={prompt} />);
  await screen.findByText('发布与运营');
  fireEvent.click(screen.getByText('查看发布情况 ↗'));
  expect(prompt).toHaveBeenCalledWith('查看发布情况');
  expect(fetcher).toHaveBeenCalledOnce();
});
