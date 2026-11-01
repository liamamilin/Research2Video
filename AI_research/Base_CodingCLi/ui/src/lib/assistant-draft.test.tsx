import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { uploadAssistantMaterial, type AssistantMaterial } from '@/api';
import { assistantDraftKey, moveAssistantDraft, useAssistantDraft } from './assistant-draft';

vi.mock('@/api', () => ({ uploadAssistantMaterial: vi.fn() }));
const uploaded = (id: string): AssistantMaterial => ({ id, name: id, kind: 'text', status: 'ready', chars: 100, file_url: '/materials/' + id });
beforeEach(() => { sessionStorage.clear(); vi.mocked(uploadAssistantMaterial).mockReset(); });
afterEach(cleanup);

it('restores an unsent attachment and text without uploading it again', async () => {
  vi.mocked(uploadAssistantMaterial).mockResolvedValue(uploaded('saved'));
  const first = renderHook(() => useAssistantDraft('draft-user', 'a'));
  act(() => first.result.current.setText('这段要求还未发送'));
  await act(() => first.result.current.add(['完整材料正文']));
  first.unmount();
  const second = renderHook(() => useAssistantDraft('draft-user', 'a'));
  expect(second.result.current.text).toBe('这段要求还未发送');
  expect(second.result.current.materials.map(row => row.id)).toEqual(['saved']);
  expect(uploadAssistantMaterial).toHaveBeenCalledTimes(1);
});

it('keeps a full failed paste through a reload and retries exactly that content', async () => {
  const body = '长文不可丢失。'.repeat(3000);
  sessionStorage.setItem(assistantDraftKey('draft-user', 'b'), JSON.stringify({ text: '要求', entries: [
    { id: 'interrupted', name: '粘贴的正文', text: body, state: 'uploading' },
  ] }));
  vi.mocked(uploadAssistantMaterial).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(uploaded('retried'));
  const draft = renderHook(() => useAssistantDraft('draft-user', 'b'));
  expect(draft.result.current.entries[0].state).toBe('failed');
  await act(() => draft.result.current.retry('interrupted'));
  expect(draft.result.current.incomplete).toBe(true);
  expect(draft.result.current.entries[0].text).toBe(body);
  await act(() => draft.result.current.retry('interrupted'));
  expect(vi.mocked(uploadAssistantMaterial).mock.calls).toEqual([[body], [body]]);
  expect(draft.result.current.materials.map(row => row.id)).toEqual(['retried']);
  expect(draft.result.current.incomplete).toBe(false);
});

it('isolates conversation drafts including a late upload response', async () => {
  let complete!: (value: AssistantMaterial) => void;
  vi.mocked(uploadAssistantMaterial).mockReturnValue(new Promise(resolve => { complete = resolve; }));
  const draft = renderHook(({ conversation }) => useAssistantDraft('draft-user', conversation), { initialProps: { conversation: 'c' } });
  let pending!: Promise<void>;
  act(() => { draft.result.current.setText('C的要求'); pending = draft.result.current.add(['C的材料']); });
  await waitFor(() => expect(uploadAssistantMaterial).toHaveBeenCalledTimes(1));
  draft.rerender({ conversation: 'd' });
  expect(draft.result.current.text).toBe('');
  expect(draft.result.current.entries).toHaveLength(0);
  act(() => draft.result.current.setText('D的要求'));
  await act(async () => { complete(uploaded('C')); await pending; });
  expect(draft.result.current.materials).toHaveLength(0);
  draft.rerender({ conversation: 'c' });
  expect(draft.result.current.text).toBe('C的要求');
  expect(draft.result.current.materials.map(row => row.id)).toEqual(['C']);
  act(() => draft.result.current.clear());
  draft.rerender({ conversation: 'd' });
  expect(draft.result.current.text).toBe('D的要求');
});

it('keeps pending uploads with a draft when its first conversation is resolved', async () => {
  let complete!: (value: AssistantMaterial) => void;
  vi.mocked(uploadAssistantMaterial).mockReturnValue(new Promise(resolve => { complete = resolve; }));
  const first = renderHook(() => useAssistantDraft('draft-user'));
  let pending!: Promise<void>;
  act(() => { pending = first.result.current.add(['尚未绑定对话的材料']); });
  act(() => moveAssistantDraft('draft-user', undefined, 'resolved'));
  const target = renderHook(() => useAssistantDraft('draft-user', 'resolved'));
  await act(async () => { complete(uploaded('resolved-material')); await pending; });
  expect(first.result.current.entries).toHaveLength(0);
  expect(target.result.current.materials.map(row => row.id)).toEqual(['resolved-material']);
});

it('retries a failed file without asking the user to select it again and preserves successful siblings', async () => {
  const image = new File(['original image bytes'], '截图.png', { type: 'image/png' });
  vi.mocked(uploadAssistantMaterial).mockResolvedValueOnce(uploaded('text')).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(uploaded('image'));
  const draft = renderHook(() => useAssistantDraft('draft-user', 'files'));
  await act(() => draft.result.current.add(['正文', image]));
  expect(draft.result.current.materials.map(row => row.id)).toEqual(['text']);
  expect(draft.result.current.incomplete).toBe(true);
  await act(() => draft.result.current.retry(draft.result.current.entries[1].id));
  expect(vi.mocked(uploadAssistantMaterial).mock.calls[2][0]).toBe(image);
  expect(draft.result.current.materials.map(row => row.id)).toEqual(['text', 'image']);
});
