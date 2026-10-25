import type { AssistantContext } from '@/api/assistant-workspace';

export const CONTEXT_LABELS: Record<AssistantContext['kind'], string> = {
  video: '视频文稿', job: '研究任务', report: '报告', matrix: '矩阵', recipe: '配方', part: '共享提示词/部件', platform: '平台',
};
export function assistantContext(pathname: string, search: string): AssistantContext | null {
  const params = new URLSearchParams(search);
  if (pathname === '/ask') {
    for (const kind of ['video', 'job', 'report', 'matrix', 'recipe', 'part', 'platform'] as const) {
      const id = params.get(kind); if (id) return { kind, id };
    }
  }
  const parse = (text: string) => { try { return decodeURIComponent(text); } catch { return text; } };
  if (pathname.startsWith('/video/')) return { kind: 'video', id: parse(pathname.slice(7)) };
  if (pathname.startsWith('/jobs/')) return { kind: 'job', id: parse(pathname.slice(6)) };
  if (pathname === '/reports/view' && params.get('path')) return { kind: 'report', id: params.get('path')! };
  if (pathname.startsWith('/matrices/')) {
    const rest = pathname.slice(10).split('/video/');
    return rest[1] ? { kind: 'job', id: parse(rest[1]) } : { kind: 'matrix', id: parse(rest[0]) };
  }
  if (pathname.startsWith('/video-recipes/parts/')) return { kind: 'part', id: parse(pathname.slice(21)) };
  if (pathname.startsWith('/video-recipes/')) return { kind: 'recipe', id: parse(pathname.slice(15)) };
  if (pathname === '/platforms') return { kind: 'platform', id: params.get('platform') || 'bilibili' };
  return null;
}
export function contextHref(context: AssistantContext) {
  return '/ask?' + new URLSearchParams({ [context.kind]: context.id }).toString();
}
