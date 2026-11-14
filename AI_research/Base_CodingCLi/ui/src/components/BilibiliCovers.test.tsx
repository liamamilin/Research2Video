import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { BilibiliCovers } from './BilibiliCovers';

vi.mock('@/api/client', () => ({ api: vi.fn() }));
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

it('uploads each selected file to its own slot without submitting a video', async () => {
  vi.mocked(api).mockResolvedValue({});
  render(<BilibiliCovers renderId="render" accountUid="123" busy={false} run={async fn => { await fn(); }} />);
  await screen.findByLabelText('首页推荐封面 · 4:3');
  const file = new File(['png'], 'cover43.png', { type: 'image/png' });
  fireEvent.change(screen.getByLabelText('首页推荐封面 · 4:3'), { target: { files: [file] } });
  await screen.findByRole('status');
  const sent = vi.mocked(api).mock.calls.find(([path, options]) => path.endsWith('/4x3') && options?.method === 'POST');
  expect(sent?.[1]?.body).toBeInstanceOf(FormData);
  expect((sent?.[1]?.body as FormData).get('file')).toBe(file);
  expect((sent?.[1]?.body as FormData).get('account_uid')).toBe('123');
  expect(vi.mocked(api).mock.calls.some(([path]) => path.includes('/uploads'))).toBe(false);
});

it('updates an existing task only after both separate covers are present', async () => {
  vi.mocked(api).mockImplementation(async path => path.endsWith('/uploads/task/covers') ? { status: 'CONFIRMED' } : {
    '4x3': { name: 'a.png', url: '/a.png' }, '16x9': { name: 'b.png', url: '/b.png' },
  });
  render(<BilibiliCovers renderId="render" accountUid="123" taskId="task" busy={false} run={async fn => { await fn(); }} />);
  const button = screen.getByRole('button', { name: '更新这条稿件的双比例封面' });
  await waitFor(() => expect(button).toHaveProperty('disabled', false));
  fireEvent.click(button);
  expect((await screen.findByRole('status')).textContent).toContain('B 站接口确认');
  expect(api).toHaveBeenCalledWith('/api/platforms/bilibili/uploads/task/covers', expect.objectContaining({ body: { account_uid: '123' } }));
});
