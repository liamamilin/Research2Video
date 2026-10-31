import { useCallback, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ArrowUpRight, ChevronLeft, ChevronRight, RefreshCw, AlertCircle, Layers, Activity, Send } from 'lucide-react';
import { getOperationsOverview, type OverviewView } from '@/api/assistant-overview';
import { usePageResource } from '@/lib/use-page-resource';

const count = (value: number | null | undefined) => value == null ? '—' : value;
const VIEWS = [
  { key: 'attention' as const, label: '需要处理', detail: '研究失败、待定稿与待出片', icon: AlertCircle },
  { key: 'working' as const, label: '进行中', detail: '研究、写稿、出片与投稿', icon: Activity },
  { key: 'publish' as const, label: '可发布', detail: '当前定稿版本，成片可用', icon: Send },
];

export function AssistantOverview({ onPrompt, disabled = false, readOnly = false }: {
  onPrompt: (text: string) => void; disabled?: boolean; readOnly?: boolean;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const matrix = params.get('matrix') || '';
  const platform = params.get('platform') || '';
  const selectedView = params.get('view') || 'attention';
  const view: OverviewView = selectedView === 'working' || selectedView === 'publish' ? selectedView : 'attention';
  const [page, setPage] = useState({ scope: '', offset: 0 });
  const scope = `${matrix}:${platform}:${view}`;
  const offset = page.scope === scope ? page.offset : 0;
  const load = useCallback((signal: AbortSignal) => getOperationsOverview({ matrix, platform, view, offset }, signal), [matrix, platform, view, offset]);
  const [active, setActive] = useState(false);
  const resource = usePageResource(useCallback(async (signal: AbortSignal) => {
    const result = await load(signal);
    if (!signal.aborted) setActive(result.active);
    return result;
  }, [load]), active ? 15000 : 0);
  const data = resource.data;
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(location.search);
    next.set('overview', '1');
    if (value) next.set(key, value); else next.delete(key);
    navigate({ pathname: location.pathname, search: '?' + next.toString() }, { replace: true });
  };
  const ask = (text: string) => onPrompt(readOnly ? text + ' 只查询并给出建议。' : text);
  const currentMatrix = data?.matrices.find(m => m.key === matrix);
  const profile = data?.platforms.find(p => p.id === data.platform);
  const matrixName = currentMatrix?.name || (matrix ? '所选矩阵' : '全部矩阵');

  return <section className="flex-1 min-h-0 overflow-y-auto space-y-5 pr-1 pb-4" aria-label="全局运营总览">
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex-1"><h2 className="font-semibold text-lg">全局总览</h2><p className="text-xs text-text-muted mt-1">先看运营全貌，再进入具体工作。</p></div>
      <span className="text-xs text-text-muted">{data ? `更新 ${new Date(data.at).toLocaleTimeString('zh-CN')}` : ''}</span>
      <button className="btn text-xs" disabled={resource.loading} onClick={() => void resource.reload()}><RefreshCw className={`w-3.5 h-3.5 ${resource.loading ? 'animate-spin' : ''}`} />刷新总览</button>
    </div>
    {resource.error && <div role="alert" className="card p-4 text-sm text-danger">{resource.error}<button className="btn text-xs ml-3" onClick={() => void resource.reload()}>重试</button>{data && <p className="text-xs mt-2">当前保留上次成功读取的数据。</p>}</div>}
    {!data && resource.loading && <p role="status" className="text-sm text-text-muted">正在汇总各矩阵的工作…</p>}
    {data && <>
      {!!data.warnings.length && <div role="alert" className="card p-4 text-sm text-warning space-y-1">{data.warnings.map(w => <p key={w}>{w}</p>)}</div>}
      <div className="flex flex-wrap gap-3 items-center">
        <label className="text-xs text-text-muted">矩阵<select className="input ml-2 !w-auto max-w-[14rem]" aria-label="总览矩阵" value={matrix} onChange={e => change('matrix', e.target.value)}><option value="">全部矩阵</option>{data.matrices.map(m => <option key={m.key} value={m.key}>{m.name}</option>)}</select></label>
        <label className="text-xs text-text-muted">发布到<select className="input ml-2 !w-auto" aria-label="总览发布平台" value={data.platform} onChange={e => change('platform', e.target.value)}>{data.platforms.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <span className="text-xs text-text-muted">{profile?.account.connected ? `当前账号：${profile.account.name || profile.account.uid}` : '尚未连接账号'}<Link className="text-accent ml-2" to={`/platforms?platform=${data.platform}`}>{profile?.account.connected ? '平台管理' : '连接平台'}<ArrowUpRight className="inline w-3 h-3" /></Link></span>
      </div>
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {VIEWS.map(({ key, label, detail, icon: Icon }) => <button key={key} className={`card p-4 text-left ${view === key ? '!border-accent bg-accent/5' : 'hover:bg-bg-hover'}`} aria-pressed={view === key} onClick={() => change('view', key)}>
          <div className="flex justify-between text-sm text-text-muted">{label}<Icon className={`w-4 h-4 ${view === key ? 'text-accent' : ''}`} /></div>
          <div className="text-3xl font-semibold my-2">{count(data.summary[key])}</div><p className="text-xs text-text-muted">{detail}</p>
        </button>)}
        <Link className="card p-4 hover:bg-bg-hover" to={currentMatrix?.href || '/jobs'}><div className="flex justify-between text-sm text-text-muted">研究任务<Layers className="w-4 h-4" /></div><div className="text-3xl font-semibold my-2">{count(data.summary.jobs)}</div><p className="text-xs text-text-muted">其中 {data.summary.enabled_jobs} 个启用</p></Link>
      </div>
      <div className="grid lg:grid-cols-[14rem_minmax(0,1fr)] gap-5">
        <aside className="hidden lg:block card p-3 self-start" aria-label="矩阵工作分布">
          <p className="text-xs text-text-muted px-2 py-2">矩阵 · 待处理 / 进行中 / 可发布</p>
          <button className={`w-full p-2 text-sm text-left rounded ${!matrix ? 'bg-bg-hover text-accent' : ''}`} onClick={() => change('matrix', '')}>全部矩阵</button>
          {data.matrices.map(m => <button key={m.key} className={`w-full p-2 text-sm text-left rounded hover:bg-bg-hover ${matrix === m.key ? 'bg-bg-hover text-accent' : ''}`} onClick={() => change('matrix', m.key)}>
            <span className="block truncate">{m.name}</span><span className="block text-xs text-text-muted mt-1">{count(m.attention)} / {count(m.working)} / {count(m.publish)}</span>
          </button>)}
        </aside>
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap gap-3 items-center"><h3 className="font-medium flex-1">{matrixName} · {VIEWS.find(v => v.key === view)?.label}<span className="text-xs text-text-muted ml-2">{!data.items.complete ? '已知 ' : ''}{data.items.total} 项</span></h3>
            <button className="btn text-xs" disabled={disabled} onClick={() => ask(`查看全局总览${matrix ? `，限定矩阵 ${matrix}` : ''}，发布平台 ${data.platform}。分析当前需要优先处理什么，先给出建议。`)}>请助手分析</button>
          </div>
          {!profile?.account.connected && view === 'publish' ? <div className="card p-6 text-sm text-text-muted">连接所选平台的账号后，可查看针对该账号的发布候选。</div> : <>
            {!data.items.rows.length && <div className="card p-6 text-sm text-text-muted">{data.items.complete ? '当前范围没有这类事项。' : '这一部分尚未完整读取，请先查看上方提示。'}</div>}
            <div className="space-y-3">{data.items.rows.map(row => <article key={row.id} className="card p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-xs"><span className={view === 'attention' ? 'text-warning' : view === 'working' ? 'text-accent' : 'text-success'}>{row.label}</span><span className="text-text-muted">{row.matrix_name}{row.version != null ? ` · v${row.version}` : ''}</span></div>
              <Link className="block font-medium hover:text-accent" to={row.href}>{row.title}</Link>
              {row.detail && <p className="text-xs text-text-muted whitespace-pre-wrap break-words">{row.detail}</p>}
              <div className="flex flex-wrap justify-end gap-2"><Link className="btn text-xs" to={row.href}>查看{row.kind === 'publication' ? '投稿' : row.kind === 'research' ? '任务' : row.kind === 'publish' ? '发布包' : '文稿'}<ArrowUpRight className="w-3.5 h-3.5" /></Link><button className="btn text-xs" disabled={disabled} onClick={() => ask(row.prompt)}>交给助手</button></div>
            </article>)}</div>
          </>}
          {(offset > 0 || data.items.next_offset != null) && <div className="flex items-center justify-end gap-3 text-xs text-text-muted"><span>{offset + 1}–{Math.min(offset + data.items.rows.length, data.items.total)} / {data.items.total}</span><button aria-label="总览上一页" className="btn text-xs" disabled={!offset || resource.loading} onClick={() => setPage({ scope, offset: Math.max(0, offset - 15) })}><ChevronLeft className="w-4 h-4" /></button><button aria-label="总览下一页" className="btn text-xs" disabled={data.items.next_offset == null || resource.loading} onClick={() => setPage({ scope, offset: data.items.next_offset || 0 })}><ChevronRight className="w-4 h-4" /></button></div>}
          <div className="card p-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-text-muted">
            <p>今日最近结果：完成 {count(data.summary.today_success)} 个任务 · 未完成 {count(data.summary.today_failed)} 个任务</p>
            {data.schedules.available ? <p><Link className="text-accent" to={data.schedules.href}>托管周期</Link>：{count(data.schedules.enabled)} 启用 · {count(data.schedules.paused)} 暂停{data.schedules.next && <> · <Link className="text-accent" to={data.schedules.next.href}>下次 {new Date(data.schedules.next.at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</Link></>}</p> : <p>{data.schedules.note}</p>}
          </div>
          <p className="text-[11px] text-text-muted leading-relaxed">{data.scope} 查看总览不会启动任务；“交给助手”填入请求，由你发送。</p>
        </div>
      </div>
    </>}
  </section>;
}
