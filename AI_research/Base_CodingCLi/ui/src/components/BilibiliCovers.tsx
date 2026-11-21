import { useCallback, useState } from 'react';
import { api } from '@/api/client';
import { PLATFORM_API } from '@/api/platforms';
import { usePageResource } from '@/lib/use-page-resource';

type Covers = Partial<Record<'4x3' | '16x9', { name: string; url: string }>>;
const SLOTS = [['4x3', '首页推荐封面 · 4:3'], ['16x9', '个人空间封面 · 16:9']] as const;

export function BilibiliCovers({ renderId, accountUid, taskId, busy, run }: {
  renderId: string; accountUid: string; taskId?: string; busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const load = useCallback((signal: AbortSignal) => api<Covers>(`${PLATFORM_API}/covers/${encodeURIComponent(renderId)}`, { signal }), [renderId]);
  const { data, error, reload } = usePageResource(load);
  const [message, setMessage] = useState('');
  return <section className="space-y-2" aria-label="B站双比例封面">
    <div className="grid sm:grid-cols-2 gap-3">{SLOTS.map(([ratio, label]) => <label key={ratio} className="block text-xs space-y-2">
      <span>{label}</span>
      {data?.[ratio] ? <img className="w-full max-h-40 object-contain rounded border border-border" src={data[ratio].url} alt={label} /> : <p className="text-text-muted">未单独设置{ratio === '4x3' ? '，沿用平台默认裁切' : '，沿用成片已有封面'}</p>}
      <input aria-label={label} type="file" accept="image/png,image/jpeg" disabled={busy || !accountUid} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = '';
        if (!file) return;
        void run(async () => {
          const body = new FormData(); body.append('file', file); body.append('account_uid', accountUid);
          await api(`${PLATFORM_API}/covers/${encodeURIComponent(renderId)}/${ratio}`, { method: 'POST', body });
          setMessage(taskId ? '封面已保存，点击下方按钮更新这条 B 站稿件。' : '封面已保存，将随本次投稿提交。');
          await reload();
        });
      }} />
    </label>)}</div>
    {error ? <p role="alert" className="text-xs text-warning">{error}</p> : null}
    {message ? <p role="status" className="text-xs text-text-muted">{message}</p> : null}
    {taskId ? <button type="button" className="btn text-xs" disabled={busy || !accountUid || !data?.['4x3'] || !data?.['16x9']} onClick={() => void run(async () => {
      const result = await api<{ status: string }>(`${PLATFORM_API}/uploads/${encodeURIComponent(taskId)}/covers`, {
        method: 'POST', body: { account_uid: accountUid }, timeoutMs: 180_000,
      });
      setMessage(result.status === 'CONFIRMED' ? '两种比例封面均已由 B 站接口确认。' : '封面修改已提交，等待 B 站确认；无需重新上传视频。');
    })}>更新这条稿件的双比例封面</button> : null}
  </section>;
}
