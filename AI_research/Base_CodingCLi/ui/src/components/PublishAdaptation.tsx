import { useState } from "react";
import { api } from "@/api/client";
import { publishCheck, type PublishAdaptationResult, type PublishPayload } from "@/lib/publish-text";
import { errorMessage } from "@/lib/toast";

type History = { render_id?: string; id: number; saved_at: string; payload: PublishPayload };
type Run = (action: () => Promise<unknown>) => Promise<void>;
export function PublishAdaptation({ platform, renderId, accountUid, value, initial, onApply, run, busy }: {
  platform: string; renderId: string; accountUid?: string; value: PublishPayload; initial?: PublishAdaptationResult;
  onApply: (value: PublishPayload) => void; run: Run; busy: boolean;
}) {
  const [result,setResult] = useState<PublishAdaptationResult | null>(null);
  const [previewKey,setPreviewKey] = useState("");
  const [history,setHistory] = useState<History[] | null>(null);
  const [archive,setArchive] = useState(false);
  const [reading,setReading] = useState(false);
  const [error,setError] = useState("");
  const check = publishCheck(platform,value,initial?.check.limits);
  const source = result ?? initial;
  const stale = !!result && previewKey !== JSON.stringify(value);
  const matchesInitial = initial && value.title === initial.payload.title && value.description === initial.payload.description
    && JSON.stringify(value.tags) === JSON.stringify(initial.payload.tags);
  async function read(action: () => Promise<void>) {
    setReading(true);setError("");
    try {await action();} catch(e) {setError(errorMessage(e));} finally {setReading(false);}
  }
  async function save(next: PublishPayload) {
    await api(`/api/video/distributions/${platform}/draft`, {method:"PUT",body:{render_id:renderId,account_uid:accountUid,
      payload:next,previous_payload:value}});
    onApply(next); setResult(null); setHistory(null);
  }
  return <section className="rounded border border-border p-3 space-y-3 text-xs" aria-label="平台文案适配">
    <div className="flex flex-wrap items-center gap-3"><button type="button" className="btn text-xs" disabled={busy || reading} onClick={() => void read(async () => {
      const snapshot = JSON.stringify(value);
      const response = await api<PublishAdaptationResult>(`/api/video/distributions/${platform}/adapt`,{method:"POST",body:{render_id:renderId,payload:value}});
      setResult(response); setPreviewKey(snapshot);
    })}>一键适配{check.limits.name}</button>
      <button type="button" className="text-accent" onClick={() => setArchive(!archive)}>完整发布包与出处{source ? `（${source.references.length} 条）` : ""}</button>
      <button type="button" className="text-accent" disabled={busy || reading} onClick={() => void read(async () => {
        const response = await api<{versions:History[]}>(`/api/video/distributions/${platform}/history/${renderId}`);setHistory(response.versions);
      })}>查看手动版本</button>
    </div>
    {error ? <p role="alert" className="text-warning">{error}</p> : null}
    {check.limits.combined_limit !== null ? <p className={check.valid ? "text-text-muted" : "text-warning"}><span>{check.limits.combined_label} {check.counts.combined}/{check.limits.combined_limit} 字</span><span> · 当前简介额度 {check.counts.description_budget} 字</span></p> : null}
    <p className="text-text-muted">适配先预览；应用时保存修改前后的版本。原文句子、事实限定和网址不会被截断。</p>
    {!result && matchesInitial ? initial.notes.map(note => <p key={note} className="text-text-muted">{note}</p>) : null}
    {archive ? <div className="space-y-2"><p>以下内容保留在本地发布包中；不代表已全部展示在平台简介。</p>
      <pre className="whitespace-pre-wrap break-words max-h-64 overflow-auto font-sans" aria-label="完整发布包">{source?.source_description || "暂无完整发布包，请先获取适配预览。"}</pre>
      {source?.references.length ? <ul className="space-y-1">{source.references.map((ref,index) => <li key={`${ref.url}:${index}`}><span>{ref.title} </span><a className="text-accent break-all" href={ref.url} target="_blank" rel="noreferrer">{ref.url}</a></li>)}</ul> : null}</div> : null}
    {result ? <section className="space-y-3 border-t border-border pt-3" aria-label="平台适配预览">
      <div className="grid gap-3 sm:grid-cols-2">{[["修改前",result.before],["适配后",result.payload]].map(([label,raw]) => {
        const copy=raw as PublishPayload;
        return <div key={label as string}><p className="font-medium">{label as string}</p><pre className="whitespace-pre-wrap break-words max-h-60 overflow-auto font-sans">{copy.title}{"\n\n"}{copy.description}{"\n\n"}{copy.tags.map(tag => `#${tag}`).join(" ")}</pre></div>;
      })}</div>
      {result.notes.map(note => <p key={note}>{note}</p>)}
      {result.check.issues.map(issue => <p className="text-warning" key={issue}>{issue}</p>)}
      {stale ? <p role="status" className="text-warning">文案已变化，请重新预览，避免覆盖刚才的修改。</p> : null}
      <button type="button" className="btn text-xs" disabled={busy || reading || stale || !accountUid} onClick={() => void run(() => save(result.payload))}>{result.check.valid ? "应用适配并保留原版本" : "应用后继续编辑，并保留原版本"}</button>
      <button type="button" className="text-accent ml-3" onClick={() => setResult(null)}>保留当前文案</button>
    </section> : null}
    {history ? <section aria-label="发布包手动版本" className="space-y-2">{history.length ? history.map(row => <div key={row.id} className="border-t border-border pt-2"><p>版本 {row.id} · {row.saved_at} · 成片 {row.render_id || "旧记录未绑定"}</p><p>{row.payload.title}</p><p className="whitespace-pre-wrap break-words max-h-20 overflow-auto text-text-muted">{row.payload.description}</p><button type="button" className="text-accent" disabled={busy || !accountUid} onClick={() => void run(() => save({...value,...row.payload}))}>恢复这个版本并保留当前内容</button></div>) : <p>暂无已保存版本。应用适配或保存发布包后会留存版本。</p>}</section> : null}
  </section>;
}
