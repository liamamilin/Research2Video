import { useState } from 'react';
import { setThemeProduction, type ThemeProductionConfig } from '@/api/themes';
import { errorMessage } from '@/lib/toast';

export function ThemeProductionSettings({ matrix, config, canEdit, onSaved }: {
  matrix: string; config: ThemeProductionConfig; canEdit: boolean; onSaved: () => void;
}) {
  const [enabled, setEnabled] = useState(config.enabled);
  const [limit, setLimit] = useState(config.daily_limit);
  const [minutes, setMinutes] = useState(config.minutes);
  const [columns, setColumns] = useState(config.columns);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <details className="card text-xs" aria-label="推荐自动出片设置">
    <summary className="p-3 cursor-pointer">推荐自动出片 · {config.enabled ? `已开启 · 每天最多补充 ${config.daily_limit} 条` : '未开启'}{config.enabled ? ` · 今日已领取 ${config.daily_used ?? 0} 条` : ''}</summary>
    <form className="p-3 pt-0 space-y-3" onSubmit={async event => {
      event.preventDefault(); if (busy || !canEdit) return;
      setBusy(true); setError('');
      try {
        await setThemeProduction(matrix, { enabled, daily_limit: limit, minutes,
          columns: Object.fromEntries(Object.entries(columns).map(([key, value]) => [key, { period: value.period, count: value.count }])) });
        onSaved();
      } catch (e) { setError(errorMessage(e)); }
      finally { setBusy(false); }
    }}>
      <p className="text-text-muted">这里控制每日推荐的补充产量，已有报告出片配额保留。自动领取当天未使用的推荐，所有入口共享矩阵排重和制作队列。关闭后停止新增领取，已排队任务继续完成。</p>
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2"><input type="checkbox" checked={enabled} disabled={!canEdit || busy} onChange={e => setEnabled(e.target.checked)} />开启推荐自动出片</label>
        <label className="flex items-center gap-2">每天最多补充<input aria-label="推荐每日上限" className="input w-16" type="number" min={1} max={5} value={limit} disabled={!canEdit || busy} onChange={e => setLimit(Number(e.target.value))} />条</label>
        <label className="flex items-center gap-2">参考时长<input aria-label="推荐视频参考时长" className="input w-16" type="number" min={2} max={8} step={0.5} value={minutes} disabled={!canEdit || busy} onChange={e => setMinutes(Number(e.target.value))} />分钟</label>
      </div>
      <div className="overflow-x-auto"><table className="w-full text-left"><thead className="text-text-muted"><tr><th className="py-2 font-normal">栏目</th><th className="font-normal">周期</th><th className="font-normal">最多推荐出片</th></tr></thead>
        <tbody>{Object.entries(columns).map(([key, rule]) => <tr key={key} className="border-t border-border">
          <td className="py-2 pr-3">{rule.name}</td>
          <td className="pr-3"><select aria-label={`${rule.name}推荐周期`} className="input w-auto" value={rule.period} disabled={!canEdit || busy} onChange={e => setColumns(old => ({ ...old, [key]: { ...old[key], period: e.target.value as 'day' | 'week' } }))}><option value="day">每天</option><option value="week">每周</option></select></td>
          <td><input aria-label={`${rule.name}推荐配额`} className="input w-20" type="number" min={0} max={5} value={rule.count} disabled={!canEdit || busy} onChange={e => setColumns(old => ({ ...old, [key]: { ...old[key], count: Number(e.target.value) } }))} /> 条</td>
        </tr>)}</tbody></table></div>
      <p className="text-text-muted">日与周按北京时间计算，周一开始新一周。0 表示该栏目不自动领取；重复跳过可补选，技术失败在原任务重试。</p>
      {error ? <p role="alert" className="text-danger">{error}</p> : null}
      {canEdit ? <button className="btn btn-primary" disabled={busy}>{busy ? '保存中…' : '保存推荐出片设置'}</button> : null}
    </form>
  </details>;
}
