import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { VideoScriptDetail } from '@/api/video';
import { VideoComparisonPanel } from './VideoComparison';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const detail = { id: 'one', current_version: 2, versions: [{ version: 2 }, { version: 1 }] } as VideoScriptDetail;
function side(version: number) {
  return { version, title: `标题 ${version}`, created_at: '2026-10-25T10:00:00+08:00', sections: [],
    configuration: { recipe: 'flash', creative_direction: version === 1 ? '原表达方向' : '新表达方向', parts: {} },
    duration: { estimated_seconds: 100, binding: false }, report: null,
    render: { id: `r${version}`, status: 'COMPLETED', files: [{ name: 'video.mp4', url: `/r${version}.mp4` }] },
    renders: [{ id: `r${version}`, version, status: 'COMPLETED', created_at: '2026-10-25T10:00:00+08:00' }],
    script_href: `/video/one?tab=draft&version=${version}` };
}
const comparison = { before: side(1), after: side(2), changes: { title: true, changed_sections: 1, configuration_fields: ['creative_direction'], same_report: true },
  sections: [{ index: 0, changed: true, before: { title: '旧开场', paragraphs: [{ text: '<script>原文作为文本显示</script>' }] }, after: { title: '新开场', paragraphs: [{ text: '新开场正文' }] } },
    { index: 1, changed: false, before: { title: '共同章节', paragraphs: [{ text: '共同正文' }] }, after: { title: '共同章节', paragraphs: [{ text: '共同正文' }] } }] };

it('compares saved text, exact videos and configuration without regenerating', async () => {
  const fetcher = vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(comparison) }));
  vi.stubGlobal('fetch', fetcher);
  render(<MemoryRouter initialEntries={['/video/one?tab=compare&compare=1&version=2&before_render=r1&render=r2']}><VideoComparisonPanel detail={detail} /></MemoryRouter>);
  expect(await screen.findByText('新开场正文')).toBeTruthy();
  expect(screen.getByText('<script>原文作为文本显示</script>')).toBeTruthy();
  expect(screen.getByText('两版使用同一研究报告。', { exact: false })).toBeTruthy();
  expect(screen.getByLabelText('基准视频').getAttribute('src')).toBe('/r1.mp4');
  expect(screen.getByLabelText('对比视频').getAttribute('src')).toBe('/r2.mp4');
  expect(screen.getByLabelText('基准视频').getAttribute('preload')).toBe('none');
  expect(screen.getByRole('link', { name: /让助手分析/ }).getAttribute('href')).toContain('prompt=');
  fireEvent.click(screen.getByRole('checkbox', { name: '只看有变化的章节' }));
  expect(screen.queryByText('共同章节')).toBeNull();
  expect(fetcher).toHaveBeenCalledOnce();
});

it('shows read errors and retains comparison controls for recovery', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 422, text: async () => JSON.stringify({ detail: '成片不属于对应版本' }) })));
  render(<MemoryRouter><VideoComparisonPanel detail={detail} /></MemoryRouter>);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByRole('combobox', { name: '基准文稿版本' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '重试读取' })).toBeTruthy();
});
