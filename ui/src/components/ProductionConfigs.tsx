import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { MatrixMember } from "@/api/matrices";
import {
  addProduction, getPending, listProduction, removeProduction, setAuto, writeFromProduction,
  type PendingItem, type ProductionConfig,
} from "@/api/production";
import { listVideoCells, type VideoCell } from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";

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

/** 自动写：这个 Job 每出一份新报告，就写最多 N 条并出片，不等人点。只写打开之后出的报告，所以打开时不会去写眼前这份旧的。 */
function AutoWrite({ config, scheduled, onChange }: { config: ProductionConfig; scheduled: boolean; onChange: () => void }) {
  const toast = useToast();
  const [count, setCount] = useState(String(config.auto || 3));
  const n = Number(count);
  const valid = Number.isInteger(n) && n >= 1 && n <= 10;
  const change = async (next: number | null) => {
    if (next !== null) {
      const cost = config.reverify ? "每条先调研一遍，每篇约 4–20 次搜索，" : "";
      if (!window.confirm(`打开后，这个 Job 每出一份新报告，就自动用「${config.name}」写最多 ${next} 条并出片，不等你确认；${cost}只写打开之后出的报告。确定？`)) return;
    }
    try {
      await setAuto(config.key, next);
      onChange();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-1">
        <input type="checkbox" aria-label="自动写" checked={config.auto > 0} disabled={config.auto === 0 && !valid}
          onChange={(e) => change(e.target.checked ? n : null)} />
        报告一出来就自动写
      </label>
      <label className="flex items-center gap-1 text-text-muted">
        最多
        <input aria-label="自动每次写几条" className="input w-14" type="number" min={1} max={10} value={count} onChange={(e) => setCount(e.target.value)} />
        条
      </label>
      {config.auto > 0 && valid && n !== config.auto ? <button className="btn text-xs" onClick={() => change(n)}>改成 {n} 条</button> : null}
      {config.auto > 0 && !scheduled ? (
        <span className="text-warning">这个 Job 没设周期：要有人跑出报告才会触发</span>
      ) : null}
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
          {config.split === "each" ? (config.reverify ? " · 挑中的条目会重新调研一遍" : " · 不再调研") : " · 用报告里的条目，不调研"}
          {config.auto ? ` · 自动：报告一出来写最多 ${config.auto} 条` : ""}
        </p>
      )}
      {canEdit && config.split === "each" && !config.errors.length ? <AutoWrite config={config} scheduled={scheduled} onChange={onChange} /> : null}
      <div className="flex gap-2">
        {!config.errors.length ? (
          <button className="btn text-xs" onClick={() => setOpen(!open)}>
            {open ? "收起" : "挑条目写"}
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
      {open ? <Pending config={config} canEdit={canEdit} onWritten={onChange} /> : null}
    </div>
  );
}

/** 矩阵详情里的「视频配置」：每个 Job 的报告写成哪几种视频。给谁看在配置里选，多长和要不要重新调研在配方允许时也能选，拆不拆由配方定。 */
export function ProductionConfigs({ members, canEdit }: { members: MatrixMember[]; canEdit: boolean }) {
  const toast = useToast();
  const [configs, setConfigs] = useState<ProductionConfig[] | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [job, setJob] = useState("");
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
  // 能挂在这个 Job 上的配方：一条一期的、挑几条合一期的，而且是给这个 Job 的条目写的（topic.sources 里有它）
  const source = sources.find((m) => m.job === job)?.topics ?? "";
  const usable = cells.filter((c) => c.usable && c.key && ["single", "roundup"].includes(c.column?.topic_kind ?? "")
    && (c.topic_sources ?? []).includes(source));
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
      {sources.map((m) => {
        const own = mine.filter((c) => c.job === m.job);
        return (
          <div key={m.job} className="space-y-2">
            <h3 className="text-xs text-text-muted">{m.name}</h3>
            {own.length ? (
              own.map((c) => <ConfigCard key={c.key} config={c} canEdit={canEdit} scheduled={!!m.schedule} onChange={load} />)
            ) : (
              <p className="text-xs text-text-muted">这个 Job 还没配视频，报告只存着，不会写稿。</p>
            )}
          </div>
        );
      })}
      {!sources.length ? <p className="text-xs text-text-muted">这个矩阵里还没有出条目列表的 Job（Job 要声明 topics:）。</p> : null}
      {whole && sources.length ? (
        <p className="text-xs text-text-muted">
          另外 {whole} 个 Job 的报告是整份的，不拆成条目，所以不在这里挂配置。要写成视频，打开那份报告点「写成视频」。
        </p>
      ) : null}
      {mine.filter((c) => !sources.some((m) => m.job === c.job)).map((c) => (
        <ConfigCard key={c.key} config={c} canEdit={canEdit} scheduled={!!members.find((m) => m.job === c.job)?.schedule} onChange={load} />
      ))}
      {canEdit && sources.length ? (
        <div className="flex flex-wrap items-center gap-2 text-xs border-t border-border pt-2">
          <select aria-label="加配置的 Job" className="input w-auto" value={job} onChange={(e) => { setJob(e.target.value); setRecipe(""); setAudience(""); setMinutes(""); setResearchAgain(true); }}>
            <option value="">给哪个 Job…</option>
            {sources.map((m) => (
              <option key={m.job} value={m.job}>{m.name}</option>
            ))}
          </select>
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
                setJob("");
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
