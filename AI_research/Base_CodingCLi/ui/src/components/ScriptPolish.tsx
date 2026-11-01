import type { ScriptPolish as Polish } from "@/api/video";

const RULE_LABEL: Record<string, string> = {
  production_note: "制作说明进了口播",
  stock_ending: "固定句式收尾",
  contrast_overuse: "「不是……而是……」过多",
};

/** What the automatic polish of a fresh draft changed, so an editor can see and undo it by hand. */
export function ScriptPolish({ polish }: { polish?: Polish }) {
  if (!polish || (!polish.changes.length && !polish.error && !polish.rejected.length)) return null;
  const summary = polish.changes.length
    ? `自动润色了 ${polish.changes.length} 段`
    : polish.error ? "自动润色没有完成，保留原稿" : "自动润色的改写没有采用";
  return (
    <details className="card">
      <summary className="p-3 cursor-pointer text-xs text-text-muted">{summary}</summary>
      <div className="px-3 pb-3 space-y-3 text-sm">
        {polish.changes.map((c) => (
          <div key={c.id} className="space-y-1">
            <div className="text-xs text-text-muted">{c.id} · {c.rules.map((r) => RULE_LABEL[r] ?? r).join("、")}</div>
            <div className="text-text-muted line-through decoration-text-muted/50">{c.before}</div>
            <div>{c.after}</div>
          </div>
        ))}
        {polish.rejected.map((r) => (
          <div key={r.id} className="text-xs text-warning">{r.id} 的改写没有采用：{r.reason}</div>
        ))}
        {polish.error ? <div className="text-xs text-warning">{polish.error}</div> : null}
      </div>
    </details>
  );
}
