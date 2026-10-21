import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { runJob } from "@/api";
import { MatrixRuns } from "@/components/MatrixRuns";
import { MatrixWeek } from "@/components/MatrixWeek";
import { periodWords, PeriodEditor } from "@/components/PeriodEditor";
import {
  copyMatrix, createMatrix, deleteMatrix, getMatrix, listMatrices, pauseMatrix, resumeMatrix, splitKeywords, updateMatrix,
  type Matrix, type MatrixMember,
} from "@/api/matrices";
import { ProductionConfigs } from "@/components/ProductionConfigs";
import { listProduction, removeProduction, type ProductionConfig } from "@/api/production";
import { TimeStamp } from "@/components/TimeStamp";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";

function useCanEdit() {
  const role = useAuthStore((s) => s.user?.role);
  return role === "editor" || role === "admin";
}

function useIsAdmin() {
  return useAuthStore((s) => s.user?.role) === "admin";
}

function Keywords({ words, empty }: { words: string[]; empty: string }) {
  if (!words.length) return <span className="text-text-muted">{empty}</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {words.map((w) => (
        <span key={w} className="px-1.5 py-0.5 rounded bg-bg-hover text-xs">{w}</span>
      ))}
    </span>
  );
}

/** 新建一个空矩阵：名字、英文名（文件名和 Job 目录名）、领域、关键词 */
function NewMatrixForm({ onDone }: { onDone: (key: string) => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [words, setWords] = useState("");
  if (!open) {
    return (
      <button className="btn text-xs" onClick={() => setOpen(true)}>
        新建矩阵
      </button>
    );
  }
  const submit = async () => {
    try {
      const made = await createMatrix({ key, name, domain, keywords: splitKeywords(words) });
      onDone(made.key);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <section className="card p-3 space-y-2 text-sm w-full" aria-label="新建矩阵">
      <div className="grid gap-2 sm:grid-cols-2">
        <input className="input" placeholder="名字，如 医疗矩阵" value={name} onChange={(e) => setName(e.target.value)} />
        <input className="input" placeholder="英文名，如 medical" value={key} onChange={(e) => setKey(e.target.value)} />
        <input className="input" placeholder="领域，如 医疗健康" value={domain} onChange={(e) => setDomain(e.target.value)} />
        <input className="input" placeholder="关键词，用逗号分开" value={words} onChange={(e) => setWords(e.target.value)} />
      </div>
      <p className="text-xs text-text-muted">建好后再把 Job 放进来。想照着现有矩阵换关键词，去那个矩阵里点「换关键词复制」。</p>
      <div className="flex gap-2">
        <button className="btn btn-primary text-xs" disabled={!name.trim() || !key.trim()} onClick={submit}>
          建好
        </button>
        <button className="btn text-xs" onClick={() => setOpen(false)}>
          取消
        </button>
      </div>
    </section>
  );
}

export function MatricesPage() {
  const navigate = useNavigate();
  const canEdit = useCanEdit();
  const [rows, setRows] = useState<Matrix[] | null>(null);
  const [ungrouped, setUngrouped] = useState<{ job: string; name: string }[]>([]);
  const [error, setError] = useState("");
  const [configs, setConfigs] = useState<ProductionConfig[]>([]);
  const toast = useToast();

  useEffect(() => {
    listMatrices()
      .then((body) => {
        setRows(body.matrices);
        setUngrouped(body.ungrouped);
      })
      .catch((e) => setError(errorMessage(e)));
    listProduction().then((list) => setConfigs(Array.isArray(list) ? list : [])).catch(() => setConfigs([]));
  }, []);
  // a config on a Job that left its matrix (or whose file is gone) shows on no Job page: list it here to remove it
  const grouped = new Set((rows || []).flatMap((m) => m.members.map((x) => x.job)));
  const stray = rows ? configs.filter((c) => !grouped.has(c.job)) : [];
  const drop = async (c: ProductionConfig) => {
    if (!window.confirm(`删掉「${c.name || c.key}」这套配置？已经写好的文稿和成片都还在。`)) return;
    try {
      await removeProduction(c.key);
      setConfigs(configs.filter((x) => x.key !== c.key));
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold flex-1">矩阵</h1>
        {canEdit ? <NewMatrixForm onDone={(key) => navigate(`/matrices/${key}`)} /> : null}
      </div>
      <p className="text-xs text-text-muted">一个矩阵是一组 Job，共用一个领域和一套关键词。Job 跑出报告，报告再写成视频。</p>
      {error ? <p className="text-sm text-danger">{error}</p> : null}
      {rows === null && !error ? <p className="text-sm text-text-muted">读取中…</p> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {(rows || []).map((m) => (
          <Link key={m.key} to={`/matrices/${m.key}`} className="card p-3 space-y-1 hover:border-accent" aria-label={m.name}>
            <div className="flex items-baseline gap-2">
              <span className="font-medium">{m.name}</span>
              <span className="text-xs text-text-muted">{m.domain}</span>
              <span className="text-xs text-text-muted ml-auto">{m.members.length} 个 Job</span>
            </div>
            <div className="text-xs">
              <Keywords words={m.keywords} empty="关键词写在各个 Job 里" />
            </div>
            {m.errors.length ? <p className="text-xs text-warning">{m.errors.join("；")}</p> : null}
          </Link>
        ))}
      </div>
      {stray.length ? (
        <section className="card p-3 space-y-1 text-xs" aria-label="没有矩阵的视频配置">
          <h2 className="font-medium text-sm">{stray.length} 套视频配置挂的 Job 不在任何矩阵里</h2>
          <p className="text-text-muted">Job 被移出了矩阵，或者 Job 文件不在了：这些配置在 Job 页上看不到。把 Job 放回矩阵就能继续用，不要了就删掉。</p>
          <ul>
            {stray.map((c) => (
              <li key={c.key} className="flex flex-wrap items-center gap-2">
                <span>{c.name || c.key}</span>
                <span className="font-mono text-text-muted">{c.job}</span>
                {c.errors.length ? <span className="text-warning">{c.errors.join("；")}</span> : null}
                {canEdit ? <button className="text-accent hover:underline" onClick={() => drop(c)}>删除</button> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {ungrouped.length ? (
        <p className="text-xs text-text-muted">
          还有 {ungrouped.length} 个 Job 不在任何矩阵里：{ungrouped.map((j) => j.name).join("、")}
        </p>
      ) : null}
    </div>
  );
}

/** Job 自己的关键词：有的 Job 有两百多个，只列前几个，其余点开看 */
const KEYWORDS_SHOWN = 6;
function MemberKeywords({ words }: { words: string[] }) {
  const [all, setAll] = useState(false);
  const more = words.length - KEYWORDS_SHOWN;
  return (
    <div className="text-xs mt-1">
      <span className="text-text-muted">自己的关键词：</span>
      {(all || more <= 0 ? words : words.slice(0, KEYWORDS_SHOWN)).join("、")}
      {more > 0 ? (
        <button className="ml-1 text-accent hover:underline" onClick={() => setAll(!all)}>
          {all ? "收起" : `…共 ${words.length} 个，展开`}
        </button>
      ) : null}
    </div>
  );
}

type RowConfigs = { count: number; auto: number } | null;

function MemberRow({ member, matrixKey, configs, canEdit, isAdmin, onRemove, onChanged }: {
  member: MatrixMember; matrixKey: string; configs: RowConfigs; canEdit: boolean; isAdmin: boolean; onRemove: () => void; onChanged: (m?: Matrix) => void;
}) {
  const state = member.state;
  const toast = useToast();
  const [editingPeriod, setEditingPeriod] = useState(false);
  const [starting, setStarting] = useState(false);
  const runnable = canEdit && member.exists && member.enabled !== false && !member.is_running;
  const run = async () => {
    const spend = member.searches_per_run ? `上次用了 ${member.searches_per_run} 次搜索。` : "";
    if (!window.confirm(`运行「${member.name}」一次？会花搜索额度，${spend}跑完后到视频配置里写成视频。`)) return;
    setStarting(true);
    try {
      await runJob(member.job);
      toast.success("开始运行了");
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setStarting(false);
    }
  };
  const latest = member.latest_report;
  const hasConfig = !!configs?.count;
  // the newest report: still to write, written by an auto config, or already written
  const reportNote = member.is_running || !latest || !hasConfig ? null
    : latest.scripts ? `最新报告已写 ${latest.scripts} 篇`
    : configs!.auto ? "最新报告还没写，会自动写"
    : "最新报告还没写视频 →";
  return (
    <tr className="border-t border-border align-top">
      <td className="py-2 pr-3">
        {member.exists ? (
          <Link className="text-accent hover:underline" to={`/jobs/${encodeURIComponent(member.job)}`}>{member.name}</Link>
        ) : (
          <span className="text-warning">{member.job}（Job 文件不在了）</span>
        )}
        <div className="text-xs text-text-muted font-mono">{member.job}</div>
        {member.keywords?.length ? <MemberKeywords words={member.keywords} /> : null}
      </td>
      <td className="py-2 pr-3 text-xs whitespace-nowrap">
        {member.exists && !member.enabled ? <span className="text-text-muted">已停用</span> : periodWords(member)}
        {member.schedule?.next_runs[0] ? <div><TimeStamp value={member.schedule.next_runs[0]} label="下次" /></div> : null}
        {isAdmin && member.exists && !editingPeriod ? (
          <div><button className="text-accent hover:underline" aria-label={`改 ${member.name} 的周期`} onClick={() => setEditingPeriod(true)}>改周期</button></div>
        ) : null}
        {editingPeriod ? (
          <PeriodEditor matrixKey={matrixKey} member={member} onCancel={() => setEditingPeriod(false)}
            onDone={(m) => { setEditingPeriod(false); onChanged(m); }} />
        ) : null}
      </td>
      <td className="py-2 pr-3 text-xs">
        {member.is_running ? "运行中" : state?.last_run_at ? (
          <>
            {state.last_status === "success" ? "完成" : state.last_status === "failed" ? "失败" : state.last_status}
            <div><TimeStamp value={state.last_run_at} staleDays={14} /></div>
          </>
        ) : (
          <span className="text-text-muted">还没跑过</span>
        )}
        {runnable ? (
          <div><button className="btn text-xs mt-1" disabled={starting} aria-label={`运行 ${member.name} 一次`} onClick={run}>运行一次</button></div>
        ) : null}
      </td>
      <td className="py-2 pr-3 text-xs">{member.topics ? `选题来源 ${member.topics}` : ""}</td>
      <td className="py-2 pr-3 text-xs whitespace-nowrap">
        {member.exists ? (
          <Link className="text-accent hover:underline" to={`/matrices/${encodeURIComponent(matrixKey)}/video/${encodeURIComponent(member.job)}`}>
            视频配置
          </Link>
        ) : null}
        <div className="text-text-muted">
          {configs === null ? "" : configs.count ? `${configs.count} 套${configs.auto ? `，${configs.auto} 套自动写` : ""}` : "还没配"}
        </div>
        {reportNote ? (
          <div>
            {!latest!.scripts && !configs!.auto ? (
              <Link className="text-success hover:underline" to={`/matrices/${encodeURIComponent(matrixKey)}/video/${encodeURIComponent(member.job)}`}>{reportNote}</Link>
            ) : (
              <span className="text-text-muted">{reportNote}</span>
            )}
          </div>
        ) : null}
      </td>
      <td className="py-2 text-xs text-right">
        {canEdit ? (
          <button className="btn text-xs" aria-label={`把 ${member.name} 移出矩阵`} onClick={onRemove}>
            移出
          </button>
        ) : null}
      </td>
    </tr>
  );
}

/** 每月搜索上限：用完这个矩阵的 Job 都不再启动（定时和手动都停），到下个月自动恢复 */
function MonthlyLimit({ matrix, isAdmin, onSaved }: { matrix: Matrix; isAdmin: boolean; onSaved: (m: Matrix) => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const cap = matrix.monthly_searches ?? 0;
  const used = matrix.searches_this_month ?? 0;
  const save = async () => {
    const n = Number(value);
    if (!Number.isInteger(n) || n < 0) { toast.error("填每月最多几次搜索，0 是不限"); return; }
    try {
      onSaved(await updateMatrix(matrix.key, { name: matrix.name, domain: matrix.domain, keywords: matrix.keywords, monthly_searches: n }));
      setEditing(false);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <div className="text-sm">
      <span className="text-text-muted text-xs">每月搜索上限：</span>
      {cap ? (
        <span className={used >= cap ? "text-danger" : used >= cap * 0.8 ? "text-warning" : ""}>
          {cap} 次，本月已用 {used} 次{used >= cap ? "，这个矩阵的 Job 本月不会再启动" : ""}
        </span>
      ) : (
        <span className="text-text-muted">没设（Job 想跑多少跑多少）</span>
      )}
      {isAdmin && !editing ? (
        <button className="ml-2 text-xs text-accent hover:underline" onClick={() => { setValue(String(cap || "")); setEditing(true); }}>
          {cap ? "改上限" : "设上限"}
        </button>
      ) : null}
      {editing ? (
        <span className="ml-2 inline-flex items-center gap-2">
          <input aria-label="每月搜索上限" className="input w-28" type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} />
          <button className="btn btn-primary text-xs" onClick={save}>保存</button>
          <button className="btn text-xs" onClick={() => setEditing(false)}>取消</button>
          <span className="text-xs text-text-muted">0 是不限；到了上限定时和手动都不再启动，下个月自动恢复</span>
        </span>
      ) : null}
    </div>
  );
}

/** 换关键词复制：挑出能复制的 Job，用不分领域的模板重新建一份，放进新矩阵；新 Job 都是手动，不会自己跑 */
function CopyForm({ matrix, onDone }: { matrix: Matrix; onDone: (key: string) => void }) {
  const toast = useToast();
  const copyable = matrix.members.filter((m) => m.copyable);
  const stuck = matrix.members.filter((m) => !m.copyable);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [words, setWords] = useState("");
  const [picked, setPicked] = useState<string[]>(copyable.map((m) => m.job));
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const made = await copyMatrix(matrix.key, { key, name, domain, keywords: splitKeywords(words), jobs: picked });
      toast.success(`已复制成「${made.name}」：新 Job 都是手动，点运行才会跑`);
      onDone(made.key);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card p-3 space-y-2 text-sm" aria-label="换关键词复制">
      <h2 className="font-medium">换关键词复制</h2>
      {copyable.length ? (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            <input className="input" placeholder="新矩阵的名字，如 医疗矩阵" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="英文名，如 medical" value={key} onChange={(e) => setKey(e.target.value)} />
            <input className="input" placeholder="领域，如 医疗健康" value={domain} onChange={(e) => setDomain(e.target.value)} />
            <input className="input" placeholder="关键词，用逗号分开" value={words} onChange={(e) => setWords(e.target.value)} />
          </div>
          <fieldset className="space-y-1 text-xs">
            <legend className="text-text-muted">带上哪些 Job</legend>
            {copyable.map((m) => (
              <label key={m.job} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={picked.includes(m.job)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, m.job] : picked.filter((j) => j !== m.job))}
                />
                {m.name}
              </label>
            ))}
          </fieldset>
          <button
            className="btn btn-primary text-xs"
            disabled={busy || !key.trim() || !name.trim() || !domain.trim() || !splitKeywords(words).length || !picked.length}
            onClick={submit}
          >
            复制
          </button>
          <p className="text-xs text-text-muted">新 Job 放在 jobs/{key || "<英文名>"}/，都是手动运行，不会自己花搜索额度。</p>
        </>
      ) : (
        <p className="text-xs text-text-muted">这个矩阵里还没有能换关键词复制的 Job。</p>
      )}
      {stuck.length ? (
        <p className="text-xs text-text-muted">
          暂不能复制（提示词里写死了「{matrix.domain || matrix.name}」）：{stuck.map((m) => m.name).join("、")}
        </p>
      ) : null}
    </section>
  );
}

export function MatrixDetailPage() {
  const { key = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canEdit = useCanEdit();
  const isAdmin = useIsAdmin();
  const [params, setParams] = useSearchParams();
  const tab = ["runs", "week"].includes(params.get("tab") ?? "") ? (params.get("tab") as "runs" | "week") : "jobs";
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [ungrouped, setUngrouped] = useState<{ job: string; name: string }[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [words, setWords] = useState("");
  const [domain, setDomain] = useState("");
  const [adding, setAdding] = useState("");
  const [copying, setCopying] = useState(false);
  const [configs, setConfigs] = useState<ProductionConfig[] | null>(null);

  const load = () => {
    getMatrix(key).then(setMatrix).catch((e) => setError(errorMessage(e)));
    listProduction().then(setConfigs).catch(() => setConfigs(null));
    listMatrices().then((body) => setUngrouped(body.ungrouped)).catch(() => setUngrouped([]));
  };
  useEffect(load, [key]);
  // a Job started from here is watched until it ends, so the row turns into "报告已出" without a refresh
  const anyRunning = !!matrix?.members.some((m) => m.is_running);
  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(() => getMatrix(key).then(setMatrix).catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [anyRunning, key]);

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!matrix) return <p className="text-sm text-text-muted">读取中…</p>;

  const save = async (fields: { domain?: string; keywords?: string[]; jobs?: string[] }) => {
    try {
      setMatrix(await updateMatrix(matrix.key, {
        name: matrix.name, domain: fields.domain ?? matrix.domain, keywords: fields.keywords ?? matrix.keywords, jobs: fields.jobs,
      }));
      listMatrices().then((body) => setUngrouped(body.ungrouped)).catch(() => undefined);
      return true;
    } catch (e) {
      toast.error(errorMessage(e));
      return false;
    }
  };
  const jobs = matrix.members.map((m) => m.job);
  const pause = async () => {
    if (!window.confirm("暂停后这个矩阵所有 Job 的定时都不跑，各自的周期保留，手动运行不受影响。确定？")) return;
    try {
      setMatrix(await pauseMatrix(matrix.key));
      toast.success("已暂停整个矩阵");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  const resume = async () => {
    try {
      let next: Matrix;
      try {
        next = await resumeMatrix(matrix.key, false);
      } catch (e) {
        // the server says what turning them back on costs; only a yes to that sends the confirmation
        if (!window.confirm(`${errorMessage(e)}。确定恢复？`)) return;
        next = await resumeMatrix(matrix.key, true);
      }
      setMatrix(next);
      toast.success("已恢复定时");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <Link className="text-xs text-accent hover:underline" to="/matrices">矩阵</Link>
        <span className="text-xs text-text-muted">/</span>
        <h1 className="text-lg font-semibold flex-1">{matrix.name}</h1>
        {isAdmin && matrix.members.some((m) => m.schedule?.enabled) ? (
          <button className="btn text-xs" onClick={pause}>整个暂停</button>
        ) : null}
        {canEdit ? (
          <button className="btn text-xs" onClick={() => setCopying(!copying)}>
            换关键词复制
          </button>
        ) : null}
      </div>
      {matrix.paused?.length ? (
        <div role="status" className="card border-warning/50 p-2 text-xs text-warning flex flex-wrap items-center gap-2">
          <span className="flex-1">矩阵已暂停：{matrix.paused.length} 个 Job 的定时没在跑，各自的周期还留着；「运行一次」仍可手动点。暂停期间错过的不补跑。</span>
          {isAdmin ? <button className="btn text-xs" onClick={resume}>恢复定时</button> : null}
        </div>
      ) : null}
      {matrix.errors.length ? <p className="text-xs text-warning">{matrix.errors.join("；")}</p> : null}

      <section className="card p-3 space-y-2 text-sm" aria-label="领域和关键词">
        {editing ? (
          <>
            <input className="input w-full" aria-label="领域" value={domain} onChange={(e) => setDomain(e.target.value)} />
            <input className="input w-full" aria-label="关键词" value={words} onChange={(e) => setWords(e.target.value)} />
            <p className="text-xs text-text-muted">所有 Job 运行时，关键词先放这里的，再放 Job 自己的。改了下次运行才生效。</p>
            <div className="flex gap-2">
              <button
                className="btn btn-primary text-xs"
                onClick={async () => {
                  if (await save({ domain, keywords: splitKeywords(words) })) setEditing(false);
                }}
              >
                保存
              </button>
              <button className="btn text-xs" onClick={() => setEditing(false)}>
                取消
              </button>
            </div>
          </>
        ) : (
          <>
            <div><span className="text-text-muted text-xs">领域：</span>{matrix.domain || "—"}</div>
            <div>
              <span className="text-text-muted text-xs">关键词：</span>
              <Keywords words={matrix.keywords} empty="还写在各个 Job 里（AI 矩阵是从现有 Job 归过来的，放到这里会改动它们的提示词）" />
            </div>
            <MonthlyLimit matrix={matrix} isAdmin={isAdmin} onSaved={setMatrix} />
            {canEdit ? (
              <button
                className="btn text-xs"
                onClick={() => {
                  setDomain(matrix.domain);
                  setWords(matrix.keywords.join("，"));
                  setEditing(true);
                }}
              >
                改领域和关键词
              </button>
            ) : null}
          </>
        )}
      </section>

      {copying ? <CopyForm matrix={matrix} onDone={(next) => { setCopying(false); navigate(`/matrices/${next}`); }} /> : null}

      <div role="tablist" className="flex gap-1 border-b border-border text-sm">
        {([["jobs", "Job"], ["runs", "运行记录"], ["week", "周期"]] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id}
            className={`px-3 py-1.5 -mb-px border-b-2 ${tab === id ? "border-accent text-text" : "border-transparent text-text-muted hover:text-text"}`}
            onClick={() => setParams(id === "jobs" ? {} : { tab: id }, { replace: true })}>
            {label}
          </button>
        ))}
      </div>
      {tab === "runs" ? <MatrixRuns matrix={matrix} initialJob={params.get("job") ?? ""} /> : null}
      {tab === "week" ? <MatrixWeek matrix={matrix} onChanged={setMatrix} /> : null}

      {tab === "jobs" ? (
      <section className="card p-3 space-y-2" aria-label="矩阵里的 Job">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-medium text-sm flex-1">Job（{matrix.members.length}）</h2>
          <span className="text-xs text-text-muted">
            停用、改提示词在各 Job 页；周期更细的设置在<Link className="text-accent hover:underline" to="/scheduler">周期调度</Link>页。
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-text-muted">
                <th className="font-normal pb-1">Job</th>
                <th className="font-normal pb-1">周期</th>
                <th className="font-normal pb-1">上次运行</th>
                <th className="font-normal pb-1">选题</th>
                <th className="font-normal pb-1">视频</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {matrix.members.map((m) => (
                <MemberRow key={m.job} member={m} matrixKey={matrix.key} canEdit={canEdit} isAdmin={isAdmin}
                  configs={configs ? { count: configs.filter((c) => c.job === m.job).length, auto: configs.filter((c) => c.job === m.job && c.auto > 0).length } : null}
                  onRemove={() => save({ jobs: jobs.filter((j) => j !== m.job) })}
                  onChanged={(next) => (next ? setMatrix(next) : load())} />
              ))}
            </tbody>
          </table>
        </div>
        {!matrix.members.length ? <p className="text-xs text-text-muted">还没有 Job。</p> : null}
        {canEdit && ungrouped.length ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <select aria-label="放进来的 Job" className="input w-auto" value={adding} onChange={(e) => setAdding(e.target.value)}>
              <option value="">选一个不在矩阵里的 Job…</option>
              {ungrouped.map((j) => (
                <option key={j.job} value={j.job}>{j.name}（{j.job}）</option>
              ))}
            </select>
            <button
              className="btn text-xs"
              disabled={!adding}
              onClick={async () => {
                if (await save({ jobs: [...jobs, adding] })) setAdding("");
              }}
            >
              放进来
            </button>
          </div>
        ) : null}
      </section>
      ) : null}

      {canEdit ? (
        <button
          className="btn text-xs text-danger"
          onClick={async () => {
            if (!window.confirm(`删掉「${matrix.name}」这个分组？里面的 Job 都还在，只是不再归在一起。`)) return;
            try {
              await deleteMatrix(matrix.key);
              navigate("/matrices");
            } catch (e) {
              toast.error(errorMessage(e));
            }
          }}
        >
          删除这个矩阵
        </button>
      ) : null}
    </div>
  );
}


/** 一个 Job 的视频配置：它的报告写成哪几种视频，给谁看、多长、要不要自动写都在这里设。 */
export function MatrixJobVideoPage() {
  const { key = "", job = "" } = useParams();
  const canEdit = useCanEdit();
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getMatrix(key).then(setMatrix).catch((e) => setError(errorMessage(e)));
  }, [key]);

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!matrix) return <p className="text-sm text-text-muted">读取中…</p>;
  const member = matrix.members.find((m) => m.job === job);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <Link className="text-xs text-accent hover:underline" to="/matrices">矩阵</Link>
        <span className="text-xs text-text-muted">/</span>
        <Link className="text-xs text-accent hover:underline" to={`/matrices/${encodeURIComponent(matrix.key)}`}>{matrix.name}</Link>
        <span className="text-xs text-text-muted">/</span>
        <h1 className="text-lg font-semibold">{member?.name ?? job} · 视频配置</h1>
      </div>
      {member ? (
        <>
          <p className="text-xs text-text-muted font-mono">{member.job}</p>
          <ProductionConfigs members={[member]} canEdit={canEdit} />
        </>
      ) : (
        <p className="text-sm text-text-muted">这个矩阵里没有这个 Job：{job}</p>
      )}
    </div>
  );
}
