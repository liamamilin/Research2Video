import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { MatrixMember } from "@/api/matrices";
import {
  addProduction, getPending, listProduction, removeProduction, setAuto, writeFromProduction, writeReportFromProduction,
  type PendingItem, type ProductionConfig, type WholeReport,
} from "@/api/production";
import { listVideoCells, type VideoCell } from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";

/** 整份报告的配置：这个 Job 最近的几份报告，每份写成一期视频；写过的标出来。报告就是调研，不再搜索。 */
function PendingReports({ config, canEdit, onWritten }: { config: ProductionConfig; canEdit: boolean; onWritten: () => void }) {
  const toast = useToast();
  const [reports, setReports] = useState<WholeReport[] | null>(null);
  const [busy, setBusy] = useState("");
  const load = () => getPending(config.key).then((d) => setReports(d.reports ?? [])).catch((e) => toast.error(errorMessage(e)));
  useEffect(() => {
    load();
  }, [config.key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!reports) return <p className="text-xs text-text-muted">读取中…</p>;
  if (!reports.length) return <p className="text-xs text-text-muted">这个 Job 还没有报告，跑一次才有报告可写。</p>;
  const write = async (r: WholeReport) => {
    if (!window.confirm(`用「${config.name}」把 ${r.date} 这份报告写成一期视频：直接用报告，不再搜索。确定？`)) return;
    setBusy(r.path);
    try {
      await writeReportFromProduction(config.key, r.path);
      toast.success("已开始写稿，在文稿页能看到进度");
      await load();
      onWritten();
    } catch (e) {
      toast.error(errorMessage(e));
      await load();
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="space-y-1 text-xs" aria-label={`${config.name} 的报告`}>
      <p className="text-text-muted">最近的报告，每份写成一期：</p>
      <ul className="space-y-1">
        {reports.map((r) => (
          <li key={r.path} className="flex items-center gap-2">
            <span className={r.written ? "text-text-muted" : ""}>{r.date}</span>
            <span className="font-mono text-text-muted truncate">{r.path}</span>
            <span className="ml-auto whitespace-nowrap">
              {r.written ? (
                r.scripts.slice(0, 1).map((s) => (
                  <Link key={s.id} className="text-accent hover:underline" to={`/video/${s.id}`}>写过了</Link>
                ))
              ) : canEdit ? (
                <button className="btn text-xs" disabled={busy === r.path} aria-label={`写 ${r.date} 的报告`} onClick={() => write(r)}>
                  写成视频
                </button>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 一套配置的待写条目：这个 Job 最新一份报告里的条目，标出这套配置写过的、标了不写的。 */
function Pending({ config, canEdit, onWritten }: { config: ProductionConfig; canEdit: boolean; onWritten: () => void }) {
  const toast = useToast();
  const [data, setData] = useState<{ report_date: string | null; items: PendingItem[] } | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getPending(config.key).then(setData).catch((e) => toast.error(errorMessage(e)));
  }, [config.key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data) return <p className="text-xs text-text-muted">读取中…</p>;
  if (!data.items.length) return <p className="text-xs text-text-muted">这个 Job 还没有报告，跑一次才有条目可写。</p>;

  const some = config.split === "some";
  const ready = some ? picked.length >= 2 && picked.length <= 10 : picked.length >= 1;
  const write = async () => {
    // 再核实要跑调研，花搜索额度；挑几条合一期的不调研
    const cost = config.reverify ? `每条先调研一遍，每篇约 4–20 次搜索，` : "";
    const count = some ? 1 : picked.length;
    if (!window.confirm(`用「${config.name}」写 ${count} 篇：${cost}然后写稿。确定？`)) return;
    setBusy(true);
    try {
      const made = await writeFromProduction(config.key, picked);
      const failed = made.filter((m) => "failed" in m).length;
      toast.success(`已开始 ${made.length - failed} 篇，在文稿页能看到进度${failed ? `；${failed} 条没建起来` : ""}`);
      setPicked([]);
      getPending(config.key).then(setData).catch(() => undefined);
      onWritten();
    } catch (e) {
      toast.error(errorMessage(e));
      getPending(config.key).then(setData).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1 text-xs" aria-label={`${config.name} 待写条目`}>
      <p className="text-text-muted">{data.report_date} 那一期的条目：</p>
      <ul className="space-y-1">
        {data.items.map((item) => {
          const done = item.written || item.dismissed;
          return (
            <li key={item.id} className="flex items-start gap-2">
              {canEdit ? (
                <input
                  type="checkbox"
                  aria-label={`选 ${item.code}`}
                  disabled={done}
                  checked={picked.includes(item.id)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, item.id] : picked.filter((p) => p !== item.id))}
                />
              ) : null}
              <span className={done ? "text-text-muted" : ""}>
                <span className="font-mono">{item.code}</span> {item.title}
              </span>
              {item.written ? (
                <span className="ml-auto whitespace-nowrap">
                  {item.scripts.slice(0, 1).map((s) => (
                    <Link key={s.id} className="text-accent hover:underline" to={`/video/${s.id}`}>写过了</Link>
                  ))}
                </span>
              ) : item.dismissed ? (
                <span className="ml-auto text-text-muted whitespace-nowrap">标了不写</span>
              ) : null}
            </li>
          );
        })}
      </ul>
      {canEdit ? (
        <button className="btn btn-primary text-xs" disabled={busy || !ready} onClick={write}>
          {some ? `合成一期（已选 ${picked.length} 条，要 2–10 条）` : `写选中的 ${picked.length} 条，每条一期`}
        </button>
      ) : null}
    </div>
  );
}

/** 自动制作只有开关；全部候选经排重进入原有制作队列。 */
function AutoWrite({ config, scheduled, onChange }: { config: ProductionConfig; scheduled: boolean; onChange: () => void }) {
  const toast = useToast();
  const change = async (enabled: boolean) => {
    try {
      await setAuto(config.key, enabled ? 1 : null);
      onChange();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-1">
        <input type="checkbox" aria-label="自动写" checked={config.auto > 0}
          onChange={(e) => change(e.target.checked)} />
        报告一出来就自动制作
      </label>
      <span className="text-text-muted">{config.split === "whole" ? "整份报告按配方制作" : "全部不重复选题依次制作"}</span>
      {config.auto > 0 && !scheduled ? <span className="text-warning">这个 Job 没设周期：要有人跑出报告才会触发</span> : null}
    </div>
  );
}

function ConfigCard({ config, canEdit, scheduled, onChange }: { config: ProductionConfig; canEdit: boolean; scheduled: boolean; onChange: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-border p-2 space-y-1" aria-label={`配置 ${config.name}`}>
      <div className="flex flex-wrap items-baseline gap-2 text-sm">
        <span className="font-medium">{config.name}</span>
        <span className="text-xs text-text-muted">配方 {config.recipe}</span>
        <span className="text-xs text-text-muted ml-auto">{config.scripts ? `已写 ${config.scripts} 篇` : "还没写过"}</span>
      </div>
      {config.errors.length ? (
        <p className="text-xs text-warning">{config.errors.join("；")}</p>
      ) : (
        <p className="text-xs text-text-muted">
          给{config.audience || "—"}看 · 约 {config.target_minutes} 分钟 · {config.split_name}
          {config.split === "whole" ? " · 每份报告写一期，直接用报告，不调研"
            : config.split === "each" ? (config.reverify ? " · 挑中的条目会重新调研一遍" : " · 不再调研") : " · 用报告里的条目，不调研"}
          {config.auto ? (config.split === "whole" ? " · 自动：报告一出来就写" : " · 自动：全部不重复选题") : ""}
        </p>
      )}
      {canEdit && (config.split === "each" || config.split === "whole") && !config.errors.length ? <AutoWrite config={config} scheduled={scheduled} onChange={onChange} /> : null}
      <div className="flex gap-2">
        {!config.errors.length ? (
          <button className="btn text-xs" onClick={() => setOpen(!open)}>
            {open ? "收起" : config.split === "whole" ? "选报告写" : "挑条目写"}
          </button>
        ) : null}
        {canEdit ? (
          <button
            className="btn text-xs text-danger ml-auto"
            onClick={async () => {
              if (!window.confirm(`删掉「${config.name}」这套配置？已经写好的文稿和成片都还在。`)) return;
              try {
                await removeProduction(config.key);
                onChange();
              } catch (e) {
                toast.error(errorMessage(e));
              }
            }}
          >
            删除这套
          </button>
        ) : null}
      </div>
      {open ? (config.split === "whole"
        ? <PendingReports config={config} canEdit={canEdit} onWritten={onChange} />
        : <Pending config={config} canEdit={canEdit} onWritten={onChange} />) : null}
    </div>
  );
}

/** 一个 Job（或一组 Job）的「视频配置」：报告写成哪几种视频。给谁看在配置里选，多长和要不要重新调研在配方允许时也能选，拆不拆由配方定。
 *  只给一个 Job 时就是那个 Job 自己的页面：不再问「给哪个 Job」，也不再重复写 Job 名。 */
export function ProductionConfigs({ members, canEdit }: { members: MatrixMember[]; canEdit: boolean }) {
  const toast = useToast();
  const [configs, setConfigs] = useState<ProductionConfig[] | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const only = members.length === 1 && members[0].exists ? members[0].job : ""; // 页面就是这一个 Job 的
  const [job, setJob] = useState(only);
  const [recipe, setRecipe] = useState(""); // 配方文件名
  const [audience, setAudience] = useState("");
  const [minutes, setMinutes] = useState("");
  const [researchAgain, setResearchAgain] = useState(true);

  const load = () => listProduction().then(setConfigs).catch((e) => toast.error(errorMessage(e)));
  useEffect(() => {
    load();
    if (canEdit) listVideoCells().then(setCells).catch(() => setCells([]));
  }, [canEdit]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!configs) return null;
  const jobs = new Set(members.map((m) => m.job));
  // 同一个 Job 下按名字排，快讯、盘点、一个知识点各自挨在一起
  const mine = configs.filter((c) => jobs.has(c.job)).sort((a, b) => a.name.localeCompare(b.name, "zh"));
  const whole = members.length - members.filter((m) => m.topics).length;
  const sources = members.filter((m) => m.topics);
  const single = members.length === 1;
  // 报告不是条目清单的 Job（只有它自己的页面才挂）：每份新报告写成一期
  const wholes = single ? members.filter((m) => !m.topics && m.exists) : [];
  const pickable = [...sources, ...wholes];
  const isWhole = wholes.some((m) => m.job === job);
  // 能挂在这个 Job 上的配方：一条一期的、挑几条合一期的，而且是给这个 Job 的条目写的（topic.sources 里有它）
  const source = pickable.find((m) => m.job === job)?.topics ?? "";
  const usable = cells.filter((c) => c.usable && c.key && (isWhole
    ? c.column?.topic_kind === "single" && c.whole_report
    : ["single", "roundup"].includes(c.column?.topic_kind ?? "") && (c.topic_sources ?? []).includes(source)));
  // 一份配方文件一项，同一份文件的几个受众收在一起：先选配方（快讯、盘点…），再选给谁看
  const files = new Map<string, VideoCell[]>();
  for (const c of usable) files.set(c.base ?? c.recipe ?? c.id, [...(files.get(c.base ?? c.recipe ?? c.id) ?? []), c]);
  const fileName = (cs: VideoCell[]) => (cs[0].parts ? `${cs[0].parts.type.name} · ${cs[0].parts.format.name}` : cs[0].label);
  const audiences = files.get(recipe) ?? [];
  const attached = (c: VideoCell) => mine.some((m) => m.job === job && m.recipe === c.key);
  // 配方写了 minutes_range 才能改时长；不填就是配方自己的
  const chosen = audiences.find((c) => c.audience === audience) ?? audiences[0];
  const span = chosen?.minutes_range?.length === 2 ? chosen.minutes_range : null;
  const canSkip = !!chosen?.research_optional; // 配方说它的调研可以不做

  return (
    <section className="card p-3 space-y-3" aria-label="视频配置">
      <div>
        <h2 className="font-medium text-sm">视频配置</h2>
        <p className="text-xs text-text-muted">每个 Job 的报告写成哪几种视频。给谁看在配置里选；多长、要不要重新调研，配方允许的才能在配置里改；拆不拆由配方定。</p>
      </div>
      {pickable.map((m) => {
        const own = mine.filter((c) => c.job === m.job);
        return (
          <div key={m.job} className="space-y-2">
            {single ? null : <h3 className="text-xs text-text-muted">{m.name}</h3>}
            {own.length ? (
              own.map((c) => <ConfigCard key={c.key} config={c} canEdit={canEdit} scheduled={!!m.schedule} onChange={load} />)
            ) : (
              <p className="text-xs text-text-muted">
                {m.topics ? "这个 Job 还没配视频，报告只存着，不会写稿。" : "这个 Job 的报告是整份的，还没配视频：配一套，每份新报告就能写成一期。"}
              </p>
            )}
          </div>
        );
      })}
      {!pickable.length ? <p className="text-xs text-text-muted">这个矩阵里还没有出条目列表的 Job（Job 要声明 topics:）。</p> : null}
      {whole && sources.length && !single ? (
        <p className="text-xs text-text-muted">
          另外 {whole} 个 Job 的报告是整份的，不拆成条目，所以不在这里挂配置。要写成视频，打开那份报告点「写成视频」。
        </p>
      ) : null}
      {mine.filter((c) => !pickable.some((m) => m.job === c.job)).map((c) => (
        <ConfigCard key={c.key} config={c} canEdit={canEdit} scheduled={!!members.find((m) => m.job === c.job)?.schedule} onChange={load} />
      ))}
      {canEdit && pickable.length ? (
        <div className="flex flex-wrap items-center gap-2 text-xs border-t border-border pt-2">
          {only ? null : (
            <select aria-label="加配置的 Job" className="input w-auto" value={job} onChange={(e) => { setJob(e.target.value); setRecipe(""); setAudience(""); setMinutes(""); setResearchAgain(true); }}>
              <option value="">给哪个 Job…</option>
              {pickable.map((m) => (
                <option key={m.job} value={m.job}>{m.name}</option>
              ))}
            </select>
          )}
          <select
            aria-label="加配置用的配方"
            className="input w-auto"
            disabled={!job}
            value={recipe}
            onChange={(e) => { setRecipe(e.target.value); setAudience(files.get(e.target.value)?.[0].audience ?? ""); setMinutes(""); setResearchAgain(true); }}
          >
            <option value="">{job ? "用哪个配方…" : "先选 Job"}</option>
            {[...files.entries()].map(([name, cs]) => (
              <option key={name} value={name}>{fileName(cs)}</option>
            ))}
          </select>
          <select
            aria-label="加配置给谁看"
            className="input w-auto"
            disabled={!recipe || !audiences[0]?.audience}
            value={audience}
            onChange={(e) => setAudience(e.target.value)}
          >
            {!recipe ? <option value="">先选配方</option> : !audiences[0]?.audience ? <option value="">这份配方不分受众</option> : null}
            {audiences.filter((c) => c.audience).map((c) => (
              <option key={c.audience} value={c.audience}>
                给{c.parts?.audience.name ?? c.audience}看{attached(c) ? "（已挂了一套）" : ""}
              </option>
            ))}
          </select>
          {span ? (
            <label className="flex items-center gap-1 text-text-muted">
              <input
                aria-label="加配置的分钟数"
                className="input w-20"
                type="number"
                min={span[0]}
                max={span[1]}
                step={0.5}
                placeholder={`${chosen.column?.target_minutes ?? ""}`}
                value={minutes}
                onChange={(e) => setMinutes(e.target.value)}
              />
              分钟（{span[0]}–{span[1]}，不填用配方的）
            </label>
          ) : null}
          {canSkip ? (
            <label className="flex items-center gap-1 text-text-muted" title="不勾：直接用报告里这一条的那一节和它引用的来源写，不再搜索，省搜索额度；证据只有报告里已有的">
              <input type="checkbox" aria-label="重新调研一遍" checked={researchAgain} onChange={(e) => setResearchAgain(e.target.checked)} />
              挑中的条目重新调研一遍
            </label>
          ) : null}
          <button
            className="btn text-xs"
            disabled={!job || !recipe}
            onClick={async () => {
              try {
                await addProduction({ job, recipe, audience: audience || undefined, minutes: span && minutes ? Number(minutes) : undefined, reverify: canSkip && !researchAgain ? false : undefined });
                setJob(only);
                setRecipe("");
                setAudience("");
                setMinutes("");
                setResearchAgain(true);
                load();
              } catch (e) {
                toast.error(errorMessage(e));
              }
            }}
          >
            加一套配置
          </button>
        </div>
      ) : null}
    </section>
  );
}
