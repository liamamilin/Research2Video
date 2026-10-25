import { useState } from "react";
import { getArticle, type ArticleSource as Source } from "@/api/articles";
import { errorMessage } from "@/lib/toast";

export function ArticleSource({ id, mode }: { id: string; mode: "read" | "adapt" }) {
  const [source, setSource] = useState<Source | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    if (busy || source) return;
    setBusy(true); setError("");
    try { setSource(await getArticle(id)); }
    catch (e) { setError(errorMessage(e, "读取文章原文失败")); }
    finally { setBusy(false); }
  }
  return <details className="card" onToggle={e => { if (e.currentTarget.open) void load(); }}>
    <summary className="p-3 cursor-pointer text-xs">文章原文 · {mode === "read" ? "原文朗读" : "视频化改编"}</summary>
    <div className="px-4 pb-4 space-y-3 text-xs">
      {busy ? <p role="status">正在读取原文…</p> : null}
      {error ? <p role="alert" className="text-danger">{error} <button className="text-accent" onClick={load}>重试</button></p> : null}
      {source ? <><p className="text-text-muted">{source.filename} · {source.chars} 字符{source.author ? ` · 作者：${source.author}` : ""} · 原文与当前文稿分别保存</p>
        <a className="text-accent" href={source.original_url} download>下载原始文章</a>
        {source.source_url ? <p><a className="text-accent break-all" href={source.source_url} target="_blank" rel="noreferrer">{source.source_url}</a></p> : null}
        {source.warnings.map(w => <p className="text-text-muted" key={w}>{w}</p>)}
        <pre className="whitespace-pre-wrap break-words font-sans leading-7 max-h-[32rem] overflow-y-auto" aria-label="保存的文章正文">{source.text}</pre>
        {source.edited_after_import ? <details><summary className="cursor-pointer">查看文件提取的初始正文</summary><pre className="whitespace-pre-wrap break-words font-sans leading-7 max-h-80 overflow-y-auto">{source.extracted_text}</pre></details> : null}
      </> : null}
    </div>
  </details>;
}
