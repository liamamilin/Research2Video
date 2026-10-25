import { useEffect, useRef, useState } from "react";
import { createArticle, importArticle, type ArticleCreate as ArticleInput, type ArticleImport } from "@/api/articles";
import { listVideoCells, type VideoCell } from "@/api/video";
import { listProduction, type ProductionConfig } from "@/api/production";
import { listMatrices } from "@/api/matrices";
import { errorMessage } from "@/lib/toast";

export function ArticleCreate({ onCreated }: { onCreated: (id: string) => void }) {
  const [values, setValues] = useState<ArticleInput>({ title: "", text: "", mode: "read", kind: "literary",
    column: "", production: "", author: "", source_url: "", published_on: "", origin: "copied" });
  const [imported, setImported] = useState<ArticleImport | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [configs, setConfigs] = useState<ProductionConfig[]>([]);
  const [jobNames, setJobNames] = useState<Record<string, string>>({});
  const matrixNamesLoaded = useRef(false);
  const [error, setError] = useState("");
  const [configError, setConfigError] = useState("");
  const [busy, setBusy] = useState("");
  const [retry, setRetry] = useState(0);
  const lock = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); setConfigError("");
    Promise.all([listVideoCells(controller.signal), listProduction(undefined, controller.signal)]).then(([all, productions]) => {
      if (controller.signal.aborted) return;
      const usable = all.filter(c => c.usable && c.column && (c.column.topic_kind ?? "single") === "single");
      setCells(usable); setConfigs(productions.filter(c => !c.errors?.length));
      setValues(v => ({ ...v, column: v.column || usable.find(c => c.key === "topic_digest")?.key || usable[0]?.key || "" }));
    }).catch(e => { if (!controller.signal.aborted) setConfigError(errorMessage(e, "读取配置失败")); });
    return () => controller.abort();
  }, [retry]);
  async function upload(file: File) {
    if (lock.current) return;
    lock.current = true; setBusy("upload"); setError("");
    try {
      const result = await importArticle(file);
      setImported(result); setValues(v => ({ ...v, text: result.text, import_id: result.id }));
    } catch (e) { setError(errorMessage(e, "文章导入失败，原有正文仍保留")); }
    finally { lock.current = false; setBusy(""); }
  }
  async function submit() {
    if (lock.current) return;
    lock.current = true; setBusy("create"); setError("");
    try { const made = await createArticle(values); onCreated(made.id); }
    catch (e) { setError(errorMessage(e, "未能生成文稿")); }
    finally { lock.current = false; setBusy(""); }
  }
  async function loadMatrixNames() {
    if (matrixNamesLoaded.current) return;
    matrixNamesLoaded.current = true;
    try {
      const result = await listMatrices();
      setJobNames(Object.fromEntries((result.matrices || []).flatMap(m => (m.members || []).map(j => [j.job, `${m.name} / ${j.name}`]))));
    } catch { matrixNamesLoaded.current = false; }
  }
  return <section className="card p-4 space-y-4" aria-label="文章生成视频">
    <div><h2 className="text-sm font-medium">文章生成视频</h2><p className="text-xs text-text-muted mt-1">粘贴或上传文章，检查原文后生成文稿。定稿、配音与出片沿用现有流程。</p></div>
    <label className="block text-xs space-y-1">文章标题（可选）<input className="input" maxLength={80} value={values.title} onChange={e => setValues({ ...values, title: e.target.value })} /></label>
    <label className="block text-xs space-y-1">上传文章<input type="file" accept=".txt,.md,.docx,.pdf" disabled={!!busy} className="block" onChange={e => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }} /></label>
    {busy === "upload" ? <p role="status" className="text-xs">正在提取文章正文…</p> : null}
    {imported ? <div className="text-xs text-text-muted"><p>已导入 {imported.filename}，原始文件会保留。请检查下面的正文。</p>{imported.warnings.map(w => <p key={w}>{w}</p>)}<button className="text-accent mt-1" disabled={!!busy} onClick={() => { setImported(null); setValues(v => ({ ...v, text: "", import_id: undefined })); }}>改为粘贴另一篇文章</button></div> : null}
    <label className="block text-xs space-y-1">文章正文<textarea className="input min-h-64 font-normal" maxLength={60000} disabled={!!busy} value={values.text} placeholder="把文章粘贴在这里，也可以先上传文件，再检查和整理正文。" onChange={e => setValues({ ...values, text: e.target.value })} /></label>
    <p className="text-xs text-text-muted">{values.text.length} / 60000 字符 · 原文与整理后的正文分别保留</p>
    <div className="flex flex-wrap gap-4">
      <label className="text-xs space-y-1">处理方式<select className="input" value={values.mode} onChange={e => setValues({ ...values, mode: e.target.value as ArticleInput["mode"] })}><option value="read">原文朗读</option><option value="adapt">视频化改编</option></select></label>
      <label className="text-xs space-y-1">文章类型<select className="input" value={values.kind} onChange={e => setValues({ ...values, kind: e.target.value as ArticleInput["kind"] })}><option value="literary">文学／散文／故事</option><option value="information">信息／知识／评论</option></select></label>
    </div>
    <p className="text-xs text-text-muted">{values.mode === "read" ? "保留原句和顺序，仅组织段落；生成文稿时不改写原文。" : "调整口语表达和叙述结构，保留原意、观点归属及事实限定。"}审校核对原文忠实度，不代表完成外部事实核验。</p>
    <details onToggle={e => { if (e.currentTarget.open) void loadMatrixNames(); }}><summary className="text-xs cursor-pointer">来源与制作配置（可选）</summary><div className="grid gap-3 mt-3 sm:grid-cols-2">
      <label className="text-xs">文章来源<select className="input" value={values.origin} onChange={e => setValues({ ...values, origin: e.target.value as ArticleInput["origin"] })}><option value="copied">复制／转载文章</option><option value="original">原创文章</option><option value="ai">大模型生成的文章</option></select></label>
      <label className="text-xs">原文作者<input className="input" maxLength={80} value={values.author} onChange={e => setValues({ ...values, author: e.target.value })} /></label>
      <label className="text-xs">来源链接<input className="input" type="url" maxLength={300} value={values.source_url} onChange={e => setValues({ ...values, source_url: e.target.value })} /></label>
      <label className="text-xs">原文发布日期<input className="input" type="date" value={values.published_on} onChange={e => setValues({ ...values, published_on: e.target.value })} /></label>
      <label className="text-xs">沿用矩阵视频配置<select className="input" value={values.production} onChange={e => setValues({ ...values, production: e.target.value })}><option value="">独立创作</option>{configs.map(c => <option key={c.key} value={c.key}>{jobNames[c.job] || c.job} · {c.name || c.key}</option>)}</select></label>
      {!values.production ? <label className="text-xs">文稿配方<select className="input" value={values.column} onChange={e => setValues({ ...values, column: e.target.value })}>{cells.map(c => <option key={c.id} value={c.key || ""}>{c.label}</option>)}</select></label> : <p className="text-xs text-text-muted">继承所选配置的配方、配音和制作设置；文章处理方式按本次选择执行。</p>}
    </div></details>
    {configError ? <p className="text-xs text-danger" role="alert">{configError} <button className="text-accent" onClick={() => setRetry(retry + 1)}>重试</button></p> : null}
    {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    <button className="btn btn-primary text-xs" disabled={!!busy || !values.text.trim() || !values.column || !!configError} onClick={submit}>{busy === "create" ? "正在创建…" : "生成文稿"}</button>
  </section>;
}
