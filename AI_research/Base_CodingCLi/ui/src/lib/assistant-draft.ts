import { useCallback, useSyncExternalStore } from 'react';
import { uploadAssistantMaterial, type AssistantMaterial } from '@/api';

type Entry = { id: string; name: string; text?: string; fileKey?: string;
  state: 'pending' | 'uploading' | 'failed' | 'ready'; error?: string; material?: AssistantMaterial };
type Draft = { text: string; entries: Entry[] };
const cache = new Map<string, { raw: string | null; value: Draft }>();
const listeners = new Map<string, Set<() => void>>();
const files = new Map<string, File>();
const uploading = new Set<string>();
const entryScopes = new Map<string, string>();

export const assistantDraftKey = (user: string, conversation?: string) => `assistant-draft:${user}:${conversation || 'new'}`;

function read(key: string, seed = ''): Draft {
  try {
    const raw = sessionStorage.getItem(key);
    const prior = cache.get(key);
    if (prior && prior.raw === raw) return prior.value;
    const saved = raw ? JSON.parse(raw) as Draft : null;
    const value: Draft = saved && typeof saved.text === 'string' && Array.isArray(saved.entries)
      ? { text: saved.text, entries: saved.entries.map(entry => entry.state === 'uploading'
        ? { ...entry, state: 'failed', error: '上传中断，材料已暂存，可重试' } : entry) }
      : { text: seed, entries: [] };
    cache.set(key, { raw, value });
    return value;
  } catch {
    if (!cache.has(key)) cache.set(key, { raw: null, value: { text: seed, entries: [] } });
    return cache.get(key)!.value;
  }
}

function write(key: string, value: Draft) {
  const raw = JSON.stringify(value);
  try { sessionStorage.setItem(key, raw); } catch { /* Retain the complete draft in this page's memory. */ }
  cache.set(key, { raw: (() => { try { return sessionStorage.getItem(key); } catch { return null; } })(), value });
  listeners.get(key)?.forEach(listener => listener());
}
function change(key: string, transform: (draft: Draft) => Draft) { write(key, transform(read(key))); }

async function fileOperation(key: string, value?: File, remove = false): Promise<File | undefined> {
  if (value) files.set(key, value);
  if (remove) files.delete(key);
  if (typeof indexedDB === 'undefined') return files.get(key);
  try {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('research-assistant-drafts', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('files');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('草稿文件暂存被其他页面占用'));
    });
    try {
      return await new Promise<File | undefined>((resolve, reject) => {
        const transaction = db.transaction('files', value || remove ? 'readwrite' : 'readonly');
        const objectStore = transaction.objectStore('files');
        const request = remove ? objectStore.delete(key) : value ? objectStore.put(value, key) : objectStore.get(key);
        transaction.oncomplete = () => resolve(value || (remove ? undefined : request.result as File | undefined));
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    } finally { db.close(); }
  } catch { return files.get(key); }
}

async function upload(key: string, entryId: string) {
  key = entryScopes.get(entryId) || key;
  const entry = read(key).entries.find(item => item.id === entryId);
  if (!entry || entry.state === 'ready' || uploading.has(entryId)) return;
  uploading.add(entryId);
  change(key, draft => ({ ...draft, entries: draft.entries.map(item => item.id === entryId ? { ...item, state: 'uploading', error: '' } : item) }));
  try {
    const input = entry.text ?? (entry.fileKey ? await fileOperation(entry.fileKey) : undefined);
    if (input === undefined) throw new Error('请重新选择原文件；其他草稿内容已保留');
    const material = await uploadAssistantMaterial(input);
    key = entryScopes.get(entryId) || key;
    change(key, draft => ({ ...draft, entries: draft.entries.map(item => item.id === entryId
      ? { id: item.id, name: material.name, state: 'ready', material } : item) }));
    if (entry.fileKey) await fileOperation(entry.fileKey, undefined, true);
  } catch (error) {
    key = entryScopes.get(entryId) || key;
    change(key, draft => ({ ...draft, entries: draft.entries.map(item => item.id === entryId
      ? { ...item, state: 'failed', error: error instanceof Error ? error.message : '上传失败，可重试' } : item) }));
  } finally { uploading.delete(entryId); }
}

export function moveAssistantDraft(user: string, from: string | undefined, to: string) {
  const source = assistantDraftKey(user, from), target = assistantDraftKey(user, to);
  const a = read(source), b = read(target);
  if ((!b.text && !b.entries.length) && (a.text || a.entries.length)) {
    a.entries.forEach(entry => entryScopes.set(entry.id, target));
    write(target, a); write(source, { text: '', entries: [] });
  }
}

export function clearAssistantDraft(user: string, conversation: string) {
  write(assistantDraftKey(user, conversation), { text: '', entries: [] });
}

export function setAssistantDraftText(user: string, conversation: string, text: string) {
  change(assistantDraftKey(user, conversation), draft => ({ ...draft, text }));
}

export function useAssistantDraft(user: string, conversation?: string, seed = '') {
  const key = assistantDraftKey(user, conversation);
  const subscribe = useCallback((listener: () => void) => {
    if (!listeners.has(key)) listeners.set(key, new Set());
    listeners.get(key)!.add(listener);
    return () => { listeners.get(key)?.delete(listener); };
  }, [key]);
  const snapshot = useCallback(() => read(key, seed), [key, seed]);
  const draft = useSyncExternalStore(subscribe, snapshot);
  const setText = (text: string) => change(key, value => ({ ...value, text }));
  return { text: draft.text, entries: draft.entries, setText,
    materials: draft.entries.flatMap(entry => entry.material ? [entry.material] : []),
    busy: draft.entries.some(entry => entry.state === 'uploading'),
    incomplete: draft.entries.some(entry => entry.state !== 'ready'),
    async add(values: (File | string)[]) {
      if (read(key).entries.length + values.length > 12) throw new Error('一次最多提供 12 份材料');
      const entries: Entry[] = values.map(value => {
        const id = crypto.randomUUID();
        return typeof value === 'string' ? { id, name: '粘贴的正文', state: 'pending', text: value }
          : { id, name: value.name, state: 'pending', fileKey: key + ':' + id };
      });
      change(key, value => ({ ...value, entries: [...value.entries, ...entries] }));
      entries.forEach(entry => entryScopes.set(entry.id, key));
      for (let i = 0; i < entries.length; i++) {
        if (typeof values[i] !== 'string') await fileOperation(entries[i].fileKey!, values[i] as File);
        await upload(key, entries[i].id);
      }
    },
    async retry(id: string) { await upload(key, id); },
    remove(id: string) {
      const entry = read(key).entries.find(item => item.id === id);
      change(key, value => ({ ...value, entries: value.entries.filter(item => item.id !== id) }));
      if (entry?.fileKey) void fileOperation(entry.fileKey, undefined, true);
    },
    clear() { write(key, { text: '', entries: [] }); },
  };
}
