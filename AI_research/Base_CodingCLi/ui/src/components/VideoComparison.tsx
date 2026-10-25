import { useCallback, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getVideoComparison, type VideoComparisonSide, type VideoScriptDetail } from '@/api/video';
import { usePageResource } from '@/lib/use-page-resource';

function SideHeader({ side, label, onRender }: { side: VideoComparisonSide; label: string; onRender: (id: string) => void }) {
  const movie = side.render?.status === 'COMPLETED' ? side.render.files.find(f => f.name === 'video.mp4') : undefined;
  return <div className="card p-4 space-y-3">
    <p className="text-xs text-text-muted">{label} · 文稿 v{side.version} · {new Date(side.created_at).toLocaleString('zh-CN')}</p>
    <h3 className="font-medium">{side.title}</h3>
    <div className="flex flex-wrap gap-3 text-xs"><Link className="text-accent" to={side.script_href}>打开稿件 ↗</Link>
      {side.report && <Link className="text-accent" to={side.report.href}>原研究报告 ↗</Link>}
      {side.duration.estimated_seconds != null && <span className="text-text-muted">预估口播 {side.duration.estimated_seconds} 秒（参考）</span>}</div>
    <details className="text-xs"><summary className="cursor-pointer text-text-muted">生成时的配方与表达方向</summary>
      <p className="mt-2">配方：{side.configuration.recipe || '历史版本未记录'}</p>
      {Object.entries(side.configuration.parts).map(([kind, value]) => <p className="mt-1" key={kind}>{({ audience: '受众', writing: '写稿', research: '调研', output: '出片', packaging: '包装' } as Record<string, string>)[kind] || kind}：{typeof value === 'object' && value && 'name' in value ? String(value.name) : '历史记录'}</p>)}
      {side.configuration.creative_direction && <p className="mt-2 whitespace-pre-wrap text-text-muted">{side.configuration.creative_direction}</p>}
      <p className="mt-2 text-text-muted">此处展示生成时保存的记录；配置编辑页展示当前配置。</p>
    </details>
    {side.renders.length > 0 && <label className="text-xs block">对应成片
      <select aria-label={`${label}成片记录`} className="input text-xs w-full mt-1" value={side.render?.id || ''} onChange={e => onRender(e.target.value)}>
        {side.renders.map(r => <option key={r.id} value={r.id}>v{r.version} · {r.status === 'COMPLETED' ? '已完成' : r.status === 'FAILED' ? '失败' : '未完成'} · {r.created_at.replace('T', ' ').slice(0, 16)}</option>)}
      </select></label>}
    {movie ? <video key={side.render!.id} aria-label={`${label}视频`} controls preload="none" src={movie.url} className="w-full aspect-video rounded bg-black" />
      : <p className="text-xs text-text-muted">{side.render ? '所选制作记录尚无可播放成片' : `文稿 v${side.version} 暂无成片`}</p>}
    {side.render?.error_message && <p className="text-xs text-danger">{side.render.error_message}</p>}
  </div>;
}

export function VideoComparisonPanel({ detail }: { detail: VideoScriptDetail }) {
  const [search, setSearch] = useSearchParams();
  const [onlyChanged, setOnlyChanged] = useState(false);
  const versions = detail.versions.length ? detail.versions : detail.version_shown ? [detail.version_shown] : [];
  const after = Number(search.get('version')) || detail.current_version;
  const before = Number(search.get('compare')) || versions.find(v => v.version < after)?.version || after;
  const beforeRender = search.get('before_render') || '';
  const afterRender = search.get('render') || '';
  const load = useCallback((signal: AbortSignal) => getVideoComparison(detail.id, before, after, beforeRender, afterRender, signal), [detail.id, before, after, beforeRender, afterRender]);
  const resource = usePageResource(load);
  const data = resource.data;
  function select(key: string, value: string) {
    setSearch(prev => { const next = new URLSearchParams(prev); next.set(key, value);
      if (key === 'compare') next.delete('before_render');
      if (key === 'version') next.delete('render');
      return next; });
  }
  const prompt = `请对比视频文稿 ${detail.id} 的 v${before} 与 v${after}。目标是：根据素材，产出目标观众愿意看、听得懂、想继续看的视频。结合保存的配方变化、正文和成片记录，围绕这个目标分析两版各自的得失，以及最值得尝试的改进方向。先分析。`;
  return <section className="space-y-5" aria-label="前后版本对比">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex flex-wrap gap-3">
        <label className="text-xs">基准文稿<select className="input block mt-1" aria-label="基准文稿版本" value={before} onChange={e => select('compare', e.target.value)}>{versions.map(v => <option key={v.version} value={v.version}>v{v.version}</option>)}</select></label>
        <label className="text-xs">对比文稿<select className="input block mt-1" aria-label="对比文稿版本" value={after} onChange={e => select('version', e.target.value)}>{versions.map(v => <option key={v.version} value={v.version}>v{v.version}</option>)}</select></label>
      </div>
      <Link className="btn text-sm" to={`/ask?video=${detail.id}&prompt=${encodeURIComponent(prompt)}`}>让助手分析这次变化 ↗</Link>
    </div>
    {resource.error && <div role="alert" className="card p-4 text-danger text-sm">对比暂不可用：{resource.error}<button className="btn ml-3 text-xs" onClick={() => void resource.reload()}>重试读取</button></div>}
    {resource.loading && <p className="text-text-muted text-sm">正在读取历史版本…</p>}
    {data && <>
      <div className="card p-4 space-y-2 text-sm"><p>{before === after ? '同一文稿，可选择不同制作记录对比视频。' : `${data.changes.title ? '标题有变化' : '标题沿用'} · ${data.changes.changed_sections} 个章节有文字或结构变化`}</p>
        <p className="text-xs text-text-muted">{data.changes.same_report === null ? '历史版本未完整记录报告来源。' : data.changes.same_report ? '两版使用同一研究报告。' : '两版的报告来源不同。'} {data.changes.configuration_fields.length ? '生成配置记录有变化，可展开两侧配置查看。' : '生成配置记录一致或历史版本未记录差异。'} 对比只读取历史记录。</p></div>
      <div className="grid xl:grid-cols-2 gap-4"><SideHeader side={data.before} label="基准" onRender={id => select('before_render', id)} /><SideHeader side={data.after} label="对比" onRender={id => select('render', id)} /></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={onlyChanged} onChange={e => setOnlyChanged(e.target.checked)} />只看有变化的章节</label>
      <div className="space-y-4">{data.sections.filter(s => !onlyChanged || s.changed).map(s => <div key={s.index} className="card overflow-hidden">
        <div className="px-4 py-2 border-b border-border text-xs text-text-muted">第 {s.index + 1} 节 · {s.changed ? '有变化' : '文字未变'}</div>
        <div className="grid xl:grid-cols-2 xl:divide-x divide-border">{[s.before, s.after].map((section, index) => <div key={index} className="p-4 space-y-3 text-sm">
          {section ? <><h4 className="font-medium">{section.title}</h4>{section.paragraphs.map((p, i) => <p key={i} className="whitespace-pre-wrap leading-relaxed">{p.text}</p>)}</> : <p className="text-text-muted">此版没有对应章节</p>}
        </div>)}</div>
      </div>)}</div>
      {onlyChanged && data.changes.changed_sections === 0 && <p className="text-sm text-text-muted">两版的章节文字相同，可直接比较上方的视频制作记录。</p>}
    </>}
  </section>;
}
