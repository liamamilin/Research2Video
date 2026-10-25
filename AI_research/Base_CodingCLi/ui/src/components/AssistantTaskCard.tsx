import { Link } from 'react-router-dom';
import { CheckCircle2, CircleAlert, Loader2 } from 'lucide-react';
import type { AssistantTask } from '@/api/assistant-workspace';

const LABELS: Record<string, string> = { running: '后台处理中', completed: '已完成', failed: '需要处理', waiting: '等待处理', submitted: '已提交，待核验', prepared: '发布包已准备，未上传' };
const PLATFORMS: Record<string, string> = { bilibili: 'B站', douyin: '抖音', kuaishou: '快手', xiaohongshu: '小红书', weixin_channels: '视频号' };
const PUBLICATION: Record<string, string> = { QUEUED: '等待上传', RUNNING: '处理中', UPLOADING: '上传中', PUBLISHING: '发布中',
  PUBLISHED: '已发布', SUBMITTED: '已提交，待公开确认', UNKNOWN: '投稿结果待核对', NEEDS_RECONCILIATION: '投稿结果待核对',
  FAILED: '失败', CANCELLED: '已取消', UNAVAILABLE: '暂不可读取' };
const STEPS: Record<string, string> = { write: '生成文稿', research: '调研', render: '生成视频', audit: '审校', workflow: '继续制作', edit: '修改文稿', approve: '定稿', recheck: '发布前复核' };

export function AssistantTaskCard({ task, onPrompt, compact = false }: { task: AssistantTask; onPrompt: (text: string) => void; compact?: boolean }) {
  return <section className="card min-w-0 p-4 space-y-4 text-xs" aria-label="请求任务卡" data-task-id={task.id}>
    <div className="flex items-start gap-2">
      {task.active ? <Loader2 aria-hidden className="w-4 h-4 text-accent animate-spin shrink-0" /> : task.status === 'completed'
        ? <CheckCircle2 aria-hidden className="w-4 h-4 text-success shrink-0" /> : <CircleAlert aria-hidden className="w-4 h-4 text-warning shrink-0" />}
      <div className="min-w-0 space-y-1"><h3 className={`font-medium text-sm break-words ${compact ? 'line-clamp-3' : ''}`} title={compact ? task.message : undefined}>{task.message}</h3>
        <p className="text-text-muted">{LABELS[task.status] || task.status} · {new Date(task.created * 1000).toLocaleString('zh-CN')}</p></div>
    </div>
    {!!task.changes.length && <details><summary className="cursor-pointer text-text-muted">本次操作与配置变更（{task.changes.length}）</summary>
      <ol className="space-y-3 mt-3">{task.changes.map((change) => <li key={change.id} className="border-l-2 border-border pl-3 space-y-1">
        <p>{change.label}</p>{change.status === 'failed' && <p className={change.recovered_by ? 'text-text-muted' : 'text-danger'}>{change.error || '操作未成功'}{change.recovered_by && ' · 同一变更随后已保存，原回执保留'}</p>}
        {!!change.fields?.length && <p className="text-text-muted break-words">修改字段：{change.fields.join('、')}</p>}
        {change.impact && <p className="text-text-muted">{change.impact}</p>}
        {change.configuration && <div className="space-y-2 text-text-muted">
          <p>修改层级：{change.configuration.label} · {change.configuration.scope}</p>
          {!!change.configuration.used_by.length && <p>引用配方：{change.configuration.used_by.join('、')}</p>}
          {change.configuration.changes.map(c => <details key={c.field}><summary className="cursor-pointer">{c.field}：前后值</summary>
            <div className="mt-2 space-y-2"><p>原值：<span className="whitespace-pre-wrap break-words">{c.before == null ? '未设置' : typeof c.before === 'string' ? c.before : JSON.stringify(c.before)}</span></p>
              <p>新值：<span className="whitespace-pre-wrap break-words">{c.after == null ? '未设置' : typeof c.after === 'string' ? c.after : JSON.stringify(c.after)}</span></p></div>
          </details>)}
          <p>后续：{change.configuration.next}</p><p>{change.configuration.existing_outputs}</p>
        </div>}
        {change.href && <Link className="text-accent" to={change.href}>查看对象 ↗</Link>}
      </li>)}</ol>
    </details>}
    {task.batches?.map(batch => <div className="border-t border-border pt-3 space-y-1" key={batch.id}>
      <p>{batch.title} · {batch.items.length} 项 · {batch.status === 'running' ? '后台处理中' : batch.status === 'completed' ? '已完成' : batch.status === 'prepared' ? '已准备，未执行' : '有未完成项'}</p>
      <Link className="text-accent" to={batch.href}>查看批次与逐项结果 ↗</Link>
    </div>)}
    {task.runs?.map(run => <div key={`${run.source}:${run.run_id}`} className="border-t border-border pt-3 space-y-2">
      <p className="font-medium">{run.source === 'manual' ? '手动研究' : '周期研究'} · {run.label}</p>
      <p className="break-words">{run.title || run.job_name}</p><p className="text-text-muted break-all">本次运行：{run.run_id}</p>
      {run.progress && <p className="text-text-muted whitespace-pre-wrap break-words">{run.progress}</p>}
      {run.cancel_requested && <p className="text-warning">已请求取消，等待执行器确认；后续周期保持。</p>}
      {run.error && <p className="text-warning whitespace-pre-wrap break-words">{run.error}</p>}
      <div className="flex flex-wrap gap-2"><Link className="btn text-xs" to={run.href}>{run.source === 'manual' ? '查看研究任务' : '查看周期与运行'}</Link>
        {run.report && <Link className="btn text-xs" to={run.report.href}>查看本次报告</Link>}
        {run.active && !run.cancel_requested && <button className="btn text-xs" onClick={() => onPrompt(`请取消${run.source === 'manual' ? '手动' : '周期'}研究任务 ${run.job_name} 的本次运行 ${run.run_id}（source=${run.source}），先核对准确运行，不取消其他运行，不暂停后续周期。`)}>取消本次研究</button>}
        {!run.active && run.state !== 'completed' && <button className="btn text-xs" onClick={() => onPrompt(`请检查研究任务 ${run.job_name} 的本次运行 ${run.run_id}（source=${run.source}）的状态、日志和产物，说明如何继续。先查询，不重跑。`)}>检查本次运行</button>}
        {run.report && run.status === 'success' && <button className="btn text-xs" onClick={() => onPrompt(`请基于研究报告 ${run.report!.path} 制作视频。先读取报告和研究任务 ${run.job_name} 的可用配方，说明可选方案，沿用这份报告。`)}>从报告制作视频</button>}</div>
    </div>)}
    {task.preparations?.map(p => <div className="border-t border-border pt-3 space-y-2" key={`${p.platform}:${p.render_id}`}>
      <p className="font-medium">{PLATFORMS[p.platform] || p.platform} · 发布包 · 文稿 v{p.version}</p>
      <p>账号：{p.account_name || p.account_uid || '尚未登录'}</p><p className="text-sm">{p.fields.title}</p>
      <details><summary className="cursor-pointer text-text-muted">查看简介与标签</summary><p className="whitespace-pre-wrap break-words mt-2">{p.fields.description}</p><p className="text-text-muted break-words mt-2">{p.fields.tags?.join(' · ')}</p></details>
      {p.warnings.map((w, i) => <p key={i} className="text-warning">{w}</p>)}
      <div className="flex flex-wrap gap-2"><Link className="btn text-xs" to={p.href}>查看发布包</Link>
        <button className="btn text-xs" disabled={!p.account_uid} onClick={() => onPrompt(`请把成片 ${p.render_id}（文稿 ${p.script_id} v${p.version}）发布到${PLATFORMS[p.platform] || p.platform}账号 ${p.account_name || ''}（${p.account_uid}），沿用本次已准备的发布包，先核对版本和账号。`)}>让助手发布</button></div>
    </div>)}
    {task.publications?.map(p => <div key={`${p.platform}:${p.id}`} className="border-t border-border pt-3 space-y-2">
      <p className="font-medium">{PLATFORMS[p.platform] || p.platform} · {p.label || PUBLICATION[p.status] || p.status}{p.version != null && ` · 文稿 v${p.version}`}</p>
      {(p.account_name || p.account_uid) && <p className="text-text-muted">账号：{p.account_name || p.account_uid}</p>}
      {p.title && <p>{p.title}</p>}{p.phase && <p className="text-text-muted">{p.phase}</p>}{p.error && <p className="text-warning whitespace-pre-wrap">{p.error}</p>}
      {p.recovery && <p className="text-warning">{p.recovery.message}</p>}
      <div className="flex flex-wrap gap-2"><Link className="btn text-xs" to={p.href}>查看投稿任务</Link>
        {p.recovery && <Link className="btn text-xs" to={p.recovery.href}>{p.recovery.label}</Link>}
        {p.recovery?.resumable && p.account_uid && <button className="btn text-xs" onClick={() => onPrompt(`请继续${PLATFORMS[p.platform] || p.platform}原投稿任务 ${p.id}，账号 ${p.account_uid}。使用 manage_publishing resume，沿用原任务、文稿版本和发布包。`)}>处理后继续原任务</button>}
        {p.status === 'PUBLISHED' && p.url && <a className="btn text-xs" href={p.url} target="_blank" rel="noreferrer">查看平台作品</a>}
        {p.script_id && <Link className="btn text-xs" to={`/video/${p.script_id}?tab=publish&version=${p.version}`}>文稿与发布记录</Link>}
        {!p.active && p.state !== 'completed' && <button className="btn text-xs" onClick={() => onPrompt(`请检查${PLATFORMS[p.platform] || p.platform}投稿任务 ${p.id} 的当前状态和失败原因，说明可以从哪一步恢复。先查询，不重复投稿。`)}>诊断与恢复</button>}</div>
    </div>)}
    {task.failures?.map((f, i) => <p key={i} className="text-danger">未完成{f.render_id ? ` · ${f.render_id}` : ''}：{f.error}</p>)}
    {task.videos.map((video) => <div key={video.script_id} className="space-y-3 border-t border-border pt-3">
      <Link className="font-medium text-sm hover:text-accent break-words block" to={video.href}>{video.title || '视频文稿'} ↗</Link>
      <p className={video.status === 'failed' ? 'text-danger' : video.active ? 'text-accent' : ''}>{video.label}</p>
      {video.report && <Link className="text-accent block break-all" to={video.report.href}>原始报告 ↗ <span className="text-text-muted">{video.report.path.split('/').slice(-2).join('/')}</span></Link>}
      {video.recipe && <p className="text-text-muted">文稿配方：<Link className="text-accent" to={`/video-recipes/${encodeURIComponent(video.recipe)}`}>{video.recipe} ↗</Link></p>}
      {video.target_version != null && <div className="space-y-1">
        <p>本次文稿：v{video.target_version} · {video.script_ready ? '已生成' : video.active ? '生成中' : '未生成'}{video.before_version ? `（原 v${video.before_version}）` : ''}</p>
        <p>本次视频：{video.render ? `文稿 v${video.render.version} · ${video.render.status === 'COMPLETED' ? '已完成' : video.label}` : video.render_requested ? '尚未生成' : '本次未请求制作'}</p>
        {video.current_version !== video.target_version && <p className="text-text-muted">当前文稿已到 v{video.current_version}，此卡保留本次 v{video.target_version} 的结果。</p>}
        {video.previous_video_version != null && video.previous_video_version !== video.target_version && <p className="text-text-muted">已有成片对应文稿 v{video.previous_video_version}，不代表本次视频已完成。</p>}
      </div>}
      {video.review && <p className="text-text-muted">{video.review.label}{video.review.verdict == null && ' · 暂无审校结论'}</p>}
      {video.duration?.target_minutes != null && <p className="text-text-muted">参考时长 {video.duration.target_minutes} 分钟{video.duration.estimated_seconds != null && ` · 口播预估 ${video.duration.estimated_seconds} 秒`}，仅供参考。</p>}
      {video.error && <p className="text-danger whitespace-pre-wrap break-words">{video.error}</p>}
      <div className="flex flex-wrap gap-2">
        {video.script_ready && video.script_href && <Link className="btn text-xs" to={video.script_href}>查看本次文稿</Link>}
        {video.render?.status === 'COMPLETED' && video.video_href && <Link className="btn-primary text-xs" to={video.video_href}>查看本次成片</Link>}
        {video.compare_href && <Link className="btn text-xs" to={video.compare_href}>对比前后</Link>}
        {video.retry_step && !video.active && <button className="btn text-xs" onClick={() => onPrompt(`请为视频文稿 ${video.script_id} 重试「${STEPS[video.retry_step!] || video.retry_step}」这一步。先核对当前状态，沿用已完成的结果。`)}>重试{STEPS[video.retry_step] || '失败步骤'}</button>}
        {video.status === 'waiting' && <Link className="btn text-xs" to={video.href}>处理待办</Link>}
      </div>
    </div>)}
    {task.active && <p className="text-text-muted">后台进度自动更新，可离开此页。</p>}
  </section>;
}
