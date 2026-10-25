import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, ArrowUpRight } from 'lucide-react';
import { getAssistantWorkspace, type AssistantContext } from '@/api/assistant-workspace';
import { usePageResource } from '@/lib/use-page-resource';
import { CONTEXT_LABELS } from '@/lib/assistant-context';
import { AssistantTaskCard } from './AssistantTaskCard';

const STATUS: Record<string, string> = { RESEARCHING: '调研中', WRITING: '写稿中', DRAFT: '草稿', APPROVED: '已定稿', PLAN_READY: '待确认方案',
  FAILED: '失败', COMPLETE: '成片完成', COMPLETED: '成片完成', DONE: '已结束', HELD: '需要处理', WORKING: '进行中', CANCELLED: '已取消', PENDING: '等待中', QUEUED: '排队中', RUNNING: '进行中', SUBMITTING: '提交中', DOWNLOADING: '回传中' };
export function AssistantWorkspacePanel({ conversationId, context, revision, onPrompt }: {
  conversationId?: string; context: AssistantContext | null; revision: number; onPrompt: (text: string) => void;
}) {
  const [active, setActive] = useState(false);
  const load = useCallback(async (signal: AbortSignal) => {
    void revision; // A completed chat refreshes the business projection once.
    const value = conversationId ? await getAssistantWorkspace(conversationId, context, signal) : null;
    if (!signal.aborted) setActive(!!value?.active);
    return value;
  }, [conversationId, context, revision]);
  // Poll only the business projection. It never calls a model or replays an operation.
  const resource = usePageResource(load, active ? 5000 : 0);
  const data = resource.data;
  return <div className="space-y-4" aria-label="助手工作区">
    <div className="flex items-center justify-between"><h2 className="font-medium text-sm">工作区</h2>
      <button className="btn text-xs" aria-label="刷新工作区" disabled={resource.loading} onClick={() => void resource.reload()}><RefreshCw className="w-3.5 h-3.5" /></button></div>
    <p className="text-xs text-text-muted">对象状态直接来自应用，离开对话后已启动的工作仍会继续。</p>
    {resource.error && <div className="card p-3 text-xs text-warning">工作区暂未更新：{resource.error}。可刷新重试，已有操作不会重放。</div>}
    {resource.loading && !data && <p className="text-xs text-text-muted">正在读取对象状态…</p>}
    {!!data?.tasks?.length && <section className="space-y-3" aria-label="本次请求与后台任务"><h2 className="font-medium text-sm">请求与后台任务</h2>
      {data.tasks.map((task) => <AssistantTaskCard key={task.id} task={task} onPrompt={onPrompt} />)}
    </section>}
    {!resource.loading && !resource.error && !data?.objects.length && !data?.tasks?.length && <div className="card p-4 text-xs text-text-muted">从详情页打开助手，或让助手查找要处理的任务、文稿和配方，相关对象会显示在这里。</div>}
    {!!data?.objects.length && <h2 className="font-medium text-sm">相关对象</h2>}
    {data?.objects.map((item) => <section key={`${item.kind}:${item.id}`} className="card p-4 space-y-3">
      <div className="text-xs text-text-muted">{CONTEXT_LABELS[item.kind as AssistantContext['kind']] || item.kind} · {STATUS[item.status] || item.status}</div>
      <Link to={item.href} className="text-sm font-medium flex items-start gap-2 hover:text-accent"><span className="min-w-0 break-words">{item.title}</span><ArrowUpRight className="w-3.5 h-3.5 shrink-0 mt-1" /></Link>
      {item.version != null && <div className="text-xs space-y-1"><p>当前文稿 v{item.version}</p><p>视频对应：{item.video_version != null ? `文稿 v${item.video_version}` : '暂无完成的成片'}</p>
        {item.video_version != null && item.video_version !== item.version && <p className="text-warning">视频仍使用旧版文稿</p>}</div>}
      {item.video_version != null && item.video_version !== item.version && <button className="btn text-xs" onClick={() => onPrompt(`请比较文稿 ${item.id} 的当前 v${item.version} 与视频所用 v${item.video_version}，说明主要变化。`)}>比较两个版本</button>}
      {item.detail && <p className="text-xs text-text-muted">{item.detail}</p>}
      {item.error && <p className="text-xs text-danger">{item.error}</p>}
      {item.kind === 'matrix' && <div className="space-y-3 text-xs">
        {item.render?.video_speed != null && <p>矩阵默认倍速：{String(item.render.video_speed)}× · 任务可单独覆盖</p>}
        {!!item.members?.length && <details><summary className="cursor-pointer text-text-muted">研究任务与周期（{item.members.length}）</summary><div className="mt-2 space-y-2">{item.members.map(m => <div key={m.job}>
          <Link className="text-accent" to={m.href}>{m.name}</Link><p className="text-text-muted">{m.enabled ? '任务启用' : '任务停用'} · {m.schedule?.enabled ? `周期已启用${m.schedule.next_runs?.[0] ? ` · 下次 ${new Date(m.schedule.next_runs[0]).toLocaleString('zh-CN')}` : ''}` : '未启用周期'}</p>
        </div>)}</div></details>}
        <div className="flex flex-wrap gap-2">
          <button className="btn text-xs" onClick={() => onPrompt(`请查看矩阵 ${item.id} 的现有研究任务，帮我添加研究任务并关联视频配方。先说明需要我补充什么。`)}>管理研究任务</button>
          <button className="btn text-xs" onClick={() => onPrompt(`请读取矩阵 ${item.id} 的视频共用设置和任务覆盖，帮我调整生成配置。`)}>共用视频设置</button>
          <button className="btn text-xs" onClick={() => onPrompt(`请查看矩阵 ${item.id} 的全部周期计划，帮我调整运行安排。`)}>安排周期</button>
          <button className="btn text-xs" onClick={() => onPrompt(`请查询矩阵 ${item.id} 当前定稿且未投稿的视频，列出平台账号和可发布成片。先查询，不上传。`)}>准备发布</button>
        </div>
      </div>}
      {item.kind === 'platform' && <div className="flex flex-wrap gap-2">
        <button className="btn text-xs" onClick={() => onPrompt(`请查看 ${item.id} 当前平台账号和未投稿的定稿视频，帮我准备发布包，先不要上传。`)}>准备发布</button>
        <button className="btn text-xs" onClick={() => onPrompt(`请查询 ${item.id} 的发布队列，汇总失败和需要核对的任务。先查询。`)}>检查发布队列</button>
      </div>}
      {item.renders?.map((r) => <Link className="block text-xs text-accent" key={r.id} to={`${item.href}?tab=video&version=${r.version}&render=${r.id}`}>文稿 v{r.version} · {STATUS[r.status] || r.status}{r.error_message ? ` · ${r.error_message}` : ''}</Link>)}
      {item.tasks?.map((task) => <p className="text-xs" key={task.id}>{task.title || '发布任务'} · {STATUS[task.status] || task.status}{task.error ? ` · ${task.error}` : ''}</p>)}
      {item.kind === 'video' && <div className="flex flex-wrap gap-2">
        <button className="btn text-xs" onClick={() => onPrompt(`请为视频文稿 ${item.id} 重新生成文稿，沿用已有研究报告。`)}>重新生成文稿</button>
        <button className="btn text-xs" onClick={() => onPrompt(`请用视频文稿 ${item.id} 的当前版本重新生成视频。`)}>重新生成视频</button>
        <button className="btn text-xs" onClick={() => onPrompt(`请读取视频文稿 ${item.id} 及其生成配置，帮我调整生成方式。`)}>调整生成方式</button>
      </div>}
      {!!item.links?.length && <details className="text-xs"><summary className="cursor-pointer text-accent">相关配置与影响范围</summary><div className="space-y-3 mt-3">{item.links.map((link) => <div key={link.href}>
        <Link to={link.href} className="text-accent hover:underline">{link.label} ↗</Link><p className="text-text-muted mt-1">{link.scope} · {link.next}</p>
      </div>)}</div></details>}
    </section>)}
    {!!data?.operations.length && <section className="space-y-2"><h3 className="text-sm font-medium">最近操作</h3>{data.operations.map((op) => <div key={op.id} className="card p-3 text-xs space-y-1">
      <p className={op.status === 'failed' ? 'text-danger' : ''}>{op.label}</p>
      <p className="text-text-muted">{op.status === 'failed' ? '未成功' : op.status === 'started' ? '结果待核验' : op.submitted ? '已提交，任务进度见上方' : '操作已执行'} · {new Date(op.created * 1000).toLocaleTimeString('zh-CN')}</p>
      {op.impact && <p className="text-text-muted">{op.impact}</p>}
      {op.error && <p className="text-danger">{op.error}</p>}{op.href && <Link className="text-accent" to={op.href}>查看结果 ↗</Link>}
    </div>)}</section>}
  </div>;
}
