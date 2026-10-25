import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listProduction, writeReportFromProduction, type ProductionConfig } from "@/api/production";
import { createVideoScript, listVideoCells, recordResearchReport, type ResearchReport, type VideoCell } from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";

// 报告直接写成视频（spec/product-redesign.md 第一期）：不用先建选题，报告本身就是题目，不再调研、不花搜索额度。
export function ReportToVideo({ path }: { path: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<(ResearchReport & { title: string }) | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [configs, setConfigs] = useState<ProductionConfig[]>([]); // 这份报告所属 Job 的「整份报告一期」配置
  const [column, setColumn] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const show = async () => {
    setOpen(true);
    if (report) return;
    try {
      const [made, all] = await Promise.all([recordResearchReport(path), listVideoCells()]);
      setReport(made);
      setCells(all);
      if (made.job) {
        const same = (job: string) => job === made.job || job.endsWith(`/${made.job}`);
        const every = await listProduction().catch(() => []);
        setConfigs((Array.isArray(every) ? every : []).filter((c) => c.split === "whole" && !c.errors.length && same(c.job)));
      }
    } catch (e) {
      toast.error(errorMessage(e));
      setOpen(false);
    }
  };

  const write = async () => {
    if (!report) return;
    setBusy(true);
    try {
      const typed = title.trim();
      const made = await createVideoScript({ column, report_id: report.id, ...(typed ? { topic: { title: typed } } : {}) });
      toast.success("已开始写稿：用这份报告，不再调研");
      navigate(`/video/${made.id}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const writeWith = async (config: ProductionConfig) => {
    if (!report) return;
    setBusy(true);
    try {
      const made = await writeReportFromProduction(config.key, report.report_path);
      toast.success("已开始写稿：用这份报告，不再调研");
      navigate(`/video/${made[0].id}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="btn text-xs" onClick={show}>
        写成视频
      </button>
    );
  }
  if (!report) return <span className="text-xs text-text-muted">读取中…</span>;

  // 一份报告只写一条选题的配方；盘点、对比要另选条目
  const choices = cells.filter((c) => c.usable && c.key && c.column?.topic_kind === "single");
  return (
    <section className="card p-3 space-y-2 text-xs w-full" aria-label="写成视频">
      {report.kind === "topic_source" ? (
        <p className="text-text-muted">
          这份报告是选题来源，里面每一条都已经在
          <Link className="text-accent hover:underline" to="/topics">选题页</Link>
          。去那里挑一条写快讯，或者挑几条写一期盘点。
        </p>
      ) : (
        <>
        {configs.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-text-muted">用这个 Job 的视频配置写：</span>
            {configs.map((c) => (
              <button key={c.key} className="btn btn-primary" disabled={busy} onClick={() => writeWith(c)}>
                {c.name}
              </button>
            ))}
            <span className="text-text-muted">（给谁看、多长都按配置；或者在下面自己选一个配方）</span>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="写成哪种视频" className="input w-auto" value={column} onChange={(e) => setColumn(e.target.value)}>
            <option value="">选一个配方…</option>
            {choices.map((c) => (
              <option key={c.id} value={c.key!}>
                {c.label}
              </option>
            ))}
          </select>
          <input
            aria-label="视频题目"
            className="input flex-1 min-w-[16rem]"
            placeholder={report.title}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button className="btn btn-primary" disabled={busy || !column} onClick={write}>
            写这一篇
          </button>
          <span className="text-text-muted">不再调研，不花搜索额度。题目不填就用报告标题。</span>
        </div>
        </>
      )}
      {report.scripts.length ? (
        <p className="text-text-muted">
          已经用它写了：
          {report.scripts.map((s, i) => (
            <span key={s.id}>
              {i ? "、" : ""}
              <Link className="text-accent hover:underline" to={`/video/${s.id}`}>
                {s.title || s.id.slice(0, 8)}
              </Link>
            </span>
          ))}
        </p>
      ) : null}
      <button className="btn text-xs" onClick={() => setOpen(false)}>
        收起
      </button>
    </section>
  );
}
