import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getDraftQuality, type DraftQuality } from "@/api/video";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

// 初稿质量：人工定稿后还保留了多少生成的初稿。样本很少时只能当参考，每行都带篇数。
export function DraftQualityPanel() {
  const [data, setData] = useState<DraftQuality | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getDraftQuality()
      .then((d) => alive && setData(d))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  if (!data || data.scripts.length === 0) return null;
  return (
    <section className="card space-y-3" aria-label="初稿质量">
      <div className="flex items-center gap-2">
        <h2 className="font-medium flex-1">初稿质量</h2>
        <span className="text-xs text-text-muted">定稿后还保留了多少生成的初稿；篇数少时只作参考</span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs text-text-muted border-b border-border">
          <tr>
            <th className="text-left font-normal py-1">栏目</th>
            <th className="text-left font-normal py-1">篇数</th>
            <th className="text-left font-normal py-1">手改过</th>
            <th className="text-left font-normal py-1">保留的字（中位数）</th>
            <th className="text-left font-normal py-1">原样保留的段落</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(data.columns).map(([key, c]) => (
            <tr key={key} className="border-b border-border last:border-0">
              <td className="py-1">{key}</td>
              <td className="py-1">{c.scripts}</td>
              <td className="py-1">{c.edited_by_hand}</td>
              <td className="py-1">{pct(c.median_kept_share)}</td>
              <td className="py-1">{pct(c.paragraphs_untouched)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="space-y-1">
        {data.scripts.map((s) => (
          <div key={s.script_id} className="text-xs">
            <button className="text-accent hover:underline" onClick={() => setOpen(open === s.script_id ? null : s.script_id)}>
              {open === s.script_id ? "收起" : "展开"}
            </button>{" "}
            <Link to={`/video/${s.script_id}`} className="hover:underline">
              {s.title}
            </Link>
            <span className="text-text-muted">
              {" "}
              · 初稿 v{s.draft_version} → 定稿 v{s.approved_version} · 保留 {pct(s.kept_share)} 的字 · 原样 {s.unchanged} 段、改过{" "}
              {s.edited} 段、删掉 {s.dropped} 段、新增 {s.added} 段
            </span>
            {open === s.script_id
              ? s.examples.map((e, i) => (
                  <div key={i} className="ml-6 mt-1 rounded border border-border p-2 space-y-1">
                    <p className="text-text-muted">初稿：{e.before}</p>
                    <p>定稿：{e.after}</p>
                  </div>
                ))
              : null}
          </div>
        ))}
      </div>
    </section>
  );
}
