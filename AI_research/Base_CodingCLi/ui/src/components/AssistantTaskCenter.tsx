import { useCallback, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { getAssistantTasks, getApplicationBatches } from '@/api/assistant-workspace';
import { usePageResource } from '@/lib/use-page-resource';
import { AssistantTaskCard } from './AssistantTaskCard';
import { ApplicationBatchCard } from './ApplicationBatchCard';

const FILTERS = { all: '全部', running: '进行中', attention: '待处理', completed: '已完成' } as const;
type Filter = keyof typeof FILTERS;

export function AssistantTaskCenter({ revision, busy, onOpenConversation }: {
  revision: number; busy: boolean; onOpenConversation: (id: string, prompt?: string) => void;
}) {
  const [limit, setLimit] = useState(20);
  const [active, setActive] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const location = useLocation();
  const focusedBatch = new URLSearchParams(location.search).get('batch');
  const [batchesActive, setBatchesActive] = useState(false);
  const loadBatches = useCallback(async (signal: AbortSignal) => {
    void revision;
    const data = await getApplicationBatches(signal);
    if (!signal.aborted) setBatchesActive(data.items.some(b => b.active));
    return data;
  }, [revision]);
  const batchResource = usePageResource(loadBatches, batchesActive ? 5000 : 0);
  const load = useCallback(async (signal: AbortSignal) => {
    void revision;
    const data = await getAssistantTasks(limit, signal);
    if (!signal.aborted) setActive(data.active);
    return data;
  }, [limit, revision]);
  const resource = usePageResource(load, active ? 5000 : 0);
  const loadedTasks = resource.data?.tasks || [];
  const tasks = focusedBatch ? loadedTasks.filter(t => t.batches?.some(b => b.id === focusedBatch)) : loadedTasks;
  const matches = (status: string, selected: Filter) => selected === 'all' || (selected === 'attention'
    ? ['waiting', 'failed', 'prepared', 'submitted'].includes(status) : status === selected);
  const shown = tasks.filter(t => matches(t.status, filter) && t.message.toLowerCase().includes(search.trim().toLowerCase()));
  return <section className="space-y-4" aria-label="助手任务中心">
    <div className="flex justify-between items-center gap-2"><h2 className="text-sm font-medium">任务中心</h2>
      <button className="btn text-xs" aria-label="刷新任务中心" disabled={resource.loading || batchResource.loading} onClick={() => { void resource.reload(); void batchResource.reload(); }}><RefreshCw className="w-3.5 h-3.5" /></button></div>
    <p className="text-xs text-text-muted">汇总当前账号通过助手处理的工作，跨对话跟踪；查看状态不会重新执行操作。</p>
    {batchResource.error && <p role="alert" className="text-xs text-warning">操作批次暂未更新：{batchResource.error}</p>}
    {!!batchResource.data?.items.length && <section className="space-y-3" aria-label="跨阶段与批量操作">
      <h3 className="text-xs font-medium">跨阶段与批量操作 · {batchResource.data.items.length}</h3>
      {batchResource.data.items.filter(b => !focusedBatch || b.id === focusedBatch).map(batch => <ApplicationBatchCard key={batch.id} batch={batch}
        focused={batch.id === focusedBatch} reload={() => void batchResource.reload()} />)}
      {focusedBatch && <a className="text-xs text-accent" href="/ask?tasks=1">查看全部操作批次</a>}
    </section>}
    {resource.error && <p role="alert" className="text-xs text-warning">任务中心暂未更新：{resource.error}。已有任务继续执行，可刷新重试。</p>}
    {resource.loading && !resource.data && <p className="text-xs text-text-muted">正在读取任务…</p>}
    <div className="flex flex-wrap gap-2">{(Object.keys(FILTERS) as Filter[]).map(key => <button className={`btn text-xs ${filter === key ? 'text-accent border-accent' : ''}`}
      key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{FILTERS[key]} {tasks.filter(t => matches(t.status, key)).length}</button>)}</div>
    <input className="input text-xs" aria-label="搜索助手任务" placeholder="搜索请求内容" value={search} onChange={e => setSearch(e.target.value)} />
    {resource.data && <p className="text-xs text-text-muted">{focusedBatch ? `当前批次关联 ${tasks.length} 条已载入操作请求。` : `已载入最近 ${tasks.length} 条操作请求，共 ${resource.data.total} 条；筛选范围为已载入请求。`}</p>}
    {busy && <p className="text-xs text-text-muted">当前回复进行中，结束后可切换对话；后台状态继续更新。</p>}
    {!resource.loading && !resource.error && !shown.length && <p className="card p-4 text-xs text-text-muted">{tasks.length ? '当前筛选下没有任务。' : '暂无助手操作任务。启动研究、制作视频或准备发布后，会显示在这里。'}</p>}
    {shown.map(task => <div key={task.id} className="space-y-2">
      <fieldset disabled={busy} className="min-w-0"><AssistantTaskCard compact task={task} onPrompt={text => { if (!busy && task.conversation_id) onOpenConversation(task.conversation_id, text); }} /></fieldset>
      {task.conversation_id && <button className="btn text-xs" disabled={busy} onClick={() => onOpenConversation(task.conversation_id!)}>返回原对话</button>}
    </div>)}
    {resource.data && resource.data.total > limit && <div className="text-xs text-text-muted">{limit < 100
      ? <button className="btn text-xs" disabled={resource.loading} onClick={() => setLimit(n => Math.min(100, n + 20))}>载入更早任务</button>
      : <p>已载入最近 100 条，更早记录可从历史对话查看。</p>}</div>}
    {resource.data?.active && <p className="text-xs text-text-muted">进行中的任务每 5 秒自动更新。</p>}
  </section>;
}
