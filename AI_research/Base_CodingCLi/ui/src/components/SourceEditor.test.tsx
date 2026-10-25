import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SourceEditor } from './SourceEditor';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('registers a reusable source with the shown revision and no implicit task changes', async () => {
  const fetcher = vi.fn(async (_url: string, _options: RequestInit) => ({ ok: true, status: 201, text: async () => '{}' })); vi.stubGlobal('fetch', fetcher);
  const saved = vi.fn();
  render(<SourceEditor revision="original" onClose={vi.fn()} onSaved={saved} />);
  fireEvent.change(screen.getByLabelText('来源 ID'), { target: { value: 'public' } });
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Public' } });
  fireEvent.change(screen.getByLabelText('来源地址'), { target: { value: 'github:org/repo' } });
  fireEvent.change(screen.getByLabelText('来源标签（逗号分隔）'), { target: { value: 'dev, 中文, dev' } });
  fireEvent.click(screen.getByText('保存信息源'));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe('/api/sources');
  expect(JSON.parse(String(options.body))).toEqual({ id: 'public', expected_revision: 'original', fields: { name: 'Public', spec: 'github:org/repo', tags: ['dev', '中文'], note: '' } });
});

it('shows affected jobs and retains edits after a conflict', async () => {
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => options.method === 'GET'
    ? { ok: true, status: 200, text: async () => JSON.stringify({ id: 'public', name: 'Public', spec: 'github:org/repo', tags: ['dev'], note: '', revision: 'current', used_by: [{ job: 'research/a', name: '研究 A' }] }) }
    : { ok: false, status: 409, text: async () => JSON.stringify({ error: { code: 'conflict', message: '信息源已被修改' } }) });
  vi.stubGlobal('fetch', fetcher);
  const saved = vi.fn();
  render(<SourceEditor id="public" revision="old" onClose={vi.fn()} onSaved={saved} />);
  await screen.findByDisplayValue('Public');
  expect(screen.getByText(/影响 1 个引用任务/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: 'keep this edit' } });
  fireEvent.click(screen.getByText('保存信息源'));
  expect((await screen.findByRole('alert')).textContent).toContain('信息源已被修改');
  expect(screen.getByDisplayValue('keep this edit')).toBeTruthy();
  expect(saved).not.toHaveBeenCalled();
  expect(JSON.parse(fetcher.mock.calls[1][1].body as string).expected_revision).toBe('current');
});
