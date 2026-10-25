import { useState } from 'react';
import { Link } from 'react-router-dom';
import { manageApplicationBatch, type ApplicationBatch } from '@/api/assistant-workspace';
import { useAuthStore } from '@/lib/auth-store';

const LABELS: Record<string, string> = { prepared: '已准备，未执行', running: '后台处理中', completed: '已完成', failed: '部分未完成',
  waiting: '等待处理', cancelled: '已停止后续派发', pending: '等待开始', active: '处理中', dispatching: '正在提交',
  unknown: '提交结果待核对', blocked: '需要处理', skipped: '已跳过',
  PUBLISHED: '已公开', SUBMITTED: '已投稿，等待公开确认', QUEUED: '等待上传', RUNNING: '正在处理', FAILED: '未完成', CANCELLED: '已取消' };

export function ApplicationBatchCard({ batch, reload, focused = false }: { batch: ApplicationBatch; reload: () => void; focused?: boolean }) {
  const role = useAuthStore(s => s.user?.role);
  const editable = batch.kind === 'schedules' ? role === 'admin' : role === 'admin' || role === 'editor';
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const act = async (action: 'start' | 'retry' | 'cancel') => {
    setSaving(true); setError('');
    try { await manageApplicationBatch(batch.id, action); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : '操作未完成'); }
    finally { setSaving(false); }
  };
  return <section className={`card p-4 text-xs space-y-3 ${focused ? 'border-accent' : ''}`} aria-label="应用操作批次" data-batch-id={batch.id}>
    <div className="flex items-start gap-2"><div className="flex-1 min-w-0"><h3 className="font-medium text-sm">{batch.title}</h3>
      <p className="text-text-muted mt-1">{LABELS[batch.status] || batch.status} · {batch.items.length} 项 · {new Date(batch.created * 1000).toLocaleString('zh-CN')}</p></div>
      {editable && batch.status === 'prepared' && <button className="btn-primary text-xs" disabled={saving} onClick={() => void act('start')}>执行此批次</button>}
      {editable && ['failed', 'waiting'].includes(batch.status) && batch.items.some(i => ['failed', 'blocked'].includes(i.state)) && <button className="btn text-xs" disabled={saving} onClick={() => void act('retry')}>继续未完成项</button>}
      {editable && batch.active && <button className="btn text-xs" disabled={saving} onClick={() => void act('cancel')}>停止后续派发</button>}
    </div>
    <p className="text-text-muted">{Object.entries(batch.counts).map(([key, value]) => `${LABELS[key] || key} ${value}`).join(' · ') || '范围内暂无符合条件的对象'}</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <details open={focused || undefined}><summary className="cursor-pointer">固定范围与逐项结果</summary>
      <ol className="mt-3 space-y-3">{batch.items.map(item => <li key={item.id} className="border-l-2 border-border pl-3 space-y-1">
        <p className="font-medium break-words">{item.title} <span className="text-text-muted">· {LABELS[item.state] || item.state}</span></p>
        {item.stage && <p className="text-text-muted">{LABELS[item.stage] || item.stage}</p>}
        {item.error && <p className="text-warning whitespace-pre-wrap break-words">{item.error}</p>}
        {item.result.reason && <p className="text-text-muted">{item.result.reason}</p>}
        {item.result.recovery && <div className="text-warning space-y-1"><p>{item.result.recovery.message}</p>
          <Link className="btn text-xs inline-block" to={item.result.recovery.href}>{item.result.recovery.label} ↗</Link></div>}
        {item.state === 'completed' && item.result.publication?.url && <a className="text-accent block" href={item.result.publication.url} target="_blank" rel="noreferrer">查看平台作品 ↗</a>}
        {item.result.report && <Link className="text-accent block" to={item.result.report.href}>查看本次报告 ↗</Link>}
        {item.result.href && <Link className="text-accent block" to={item.result.href}>查看对象与结果 ↗</Link>}
        {item.result.scripts?.map(sid => <Link className="text-accent block" key={sid} to={'/video/' + sid + '?tab=draft' + (item.result.script_versions?.[sid] ? '&version=' + item.result.script_versions[sid] : '')}>查看本次文稿{item.result.script_versions?.[sid] ? ` v${item.result.script_versions[sid]}` : ''} ↗</Link>)}
        {item.result.renders?.map(render => <Link className="text-accent block" key={render.id} to={`/video/${render.script_id}?tab=video&render=${render.id}${render.version ? '&version=' + render.version : ''}`}>查看本次成片{render.version ? ` v${render.version}` : ''} ↗</Link>)}
      </li>)}</ol>
    </details>
    {batch.status === 'cancelled' && <p className="text-text-muted">已提交的研究、视频和投稿由原执行器继续；取消具体运行请到对应任务。</p>}
    {batch.items.some(i => i.state === 'unknown') && <p className="text-warning">结果未知的提交不会自动重放，请打开原生任务核对。</p>}
  </section>;
}
