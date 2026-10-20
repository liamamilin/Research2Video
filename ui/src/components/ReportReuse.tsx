import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { createVideoScript, getResearchReport, listVideoCells, type ResearchReport, type VideoCell } from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";

// 一份研究、多种写法：列出同一份研究报告写出的其他文稿，并能用它按另一个配方再写一篇（不再调研、不花搜索额度）。
export function ReportReuse({ scriptId, reportId, canEdit }: { scriptId: string; reportId: string; canEdit: boolean }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [column, setColumn] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getResearchReport(reportId).then(setReport).catch(() => setReport(null));
    if (canEdit) listVideoCells().then(setCells).catch(() => setCells([]));
  }, [reportId, canEdit]);

  if (!report) return null;
  const others = report.scripts.filter((s) => s.id !== scriptId);
  // 只有一条选题的配方能直接沿用报告的选题；盘点、对比要另选条目
  const choices = cells.filter((c) => c.usable && c.key && c.column?.topic_kind === "single");
  const canWrite = canEdit && report.on_disk && Boolean(report.topic_id);

  const write = async () => {
    setBusy(true);
    try {
      const made = await createVideoScript({ column, report_id: report.id });
      toast.success("已开始写稿：用这份研究，不再调研");
      navigate(`/video/${made.id}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-3 space-y-2 text-xs" aria-label="同一份研究">
      <div className="text-text-muted">
        研究报告（{report.kind_label}）
        {others.length ? (
          <>
            ，还写了：
            {others.map((s, i) => (
              <span key={s.id}>
                {i ? "、" : ""}
                <Link className="text-accent hover:underline" to={`/video/${s.id}`}>
                  {s.title || s.id.slice(0, 8)}
                </Link>
                <span className="text-text-muted">（{s.column_key}）</span>
              </span>
            ))}
          </>
        ) : (
          "，目前只写了这一篇。"
        )}
        {!report.on_disk ? <span className="text-warning"> 报告文件已不在，不能再用它写。</span> : null}
      </div>
      {canWrite && choices.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="用这份研究写的配方" className="input w-auto" value={column} onChange={(e) => setColumn(e.target.value)}>
            <option value="">选一个配方…</option>
            {choices.map((c) => (
              <option key={c.id} value={c.key!}>
                {c.label}
              </option>
            ))}
          </select>
          <button className="btn" disabled={busy || !column} onClick={write}>
            用这份研究再写一篇
          </button>
          <span className="text-text-muted">不再调研，选题沿用「{report.topic_title}」</span>
        </div>
      ) : null}
    </section>
  );
}
