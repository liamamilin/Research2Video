import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  copyMatrix, createMatrix, deleteMatrix, getMatrix, listMatrices, splitKeywords, updateMatrix,
  type Matrix, type MatrixMember,
} from "@/api/matrices";
import { TimeStamp } from "@/components/TimeStamp";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";

function useCanEdit() {
  const role = useAuthStore((s) => s.user?.role);
  return role === "editor" || role === "admin";
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

  useEffect(() => {
    listMatrices()
      .then((body) => {
        setRows(body.matrices);
        setUngrouped(body.ungrouped);
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);

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
      {ungrouped.length ? (
        <p className="text-xs text-text-muted">
          还有 {ungrouped.length} 个 Job 不在任何矩阵里：{ungrouped.map((j) => j.name).join("、")}
        </p>
      ) : null}
    </div>
  );
}

function scheduleText(member: MatrixMember) {
  const plan = member.schedule;
  if (!plan) return "手动";
  return `${plan.enabled ? "定时" : "定时（已暂停）"} ${plan.schedule}`;
}

function MemberRow({ member, canEdit, onRemove }: { member: MatrixMember; canEdit: boolean; onRemove: () => void }) {
  const state = member.state;
  return (
    <tr className="border-t border-border align-top">
      <td className="py-2 pr-3">
        {member.exists ? (
          <Link className="text-accent hover:underline" to={`/jobs/${encodeURIComponent(member.job)}`}>{member.name}</Link>
        ) : (
          <span className="text-warning">{member.job}（Job 文件不在了）</span>
        )}
        <div className="text-xs text-text-muted font-mono">{member.job}</div>
        {member.keywords?.length ? (
          <div className="text-xs mt-1">
            <span className="text-text-muted">自己的关键词：</span>
            {member.keywords.join("、")}
          </div>
        ) : null}
      </td>
      <td className="py-2 pr-3 text-xs whitespace-nowrap">
        {member.exists && !member.enabled ? <span className="text-text-muted">已停用</span> : scheduleText(member)}
        {member.schedule?.next_runs[0] ? <div><TimeStamp value={member.schedule.next_runs[0]} label="下次" /></div> : null}
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
      </td>
      <td className="py-2 pr-3 text-xs">{member.topics ? `选题来源 ${member.topics}` : ""}</td>
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
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [ungrouped, setUngrouped] = useState<{ job: string; name: string }[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [words, setWords] = useState("");
  const [domain, setDomain] = useState("");
  const [adding, setAdding] = useState("");
  const [copying, setCopying] = useState(false);

  const load = () => {
    getMatrix(key).then(setMatrix).catch((e) => setError(errorMessage(e)));
    listMatrices().then((body) => setUngrouped(body.ungrouped)).catch(() => setUngrouped([]));
  };
  useEffect(load, [key]);

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <Link className="text-xs text-accent hover:underline" to="/matrices">矩阵</Link>
        <span className="text-xs text-text-muted">/</span>
        <h1 className="text-lg font-semibold flex-1">{matrix.name}</h1>
        {canEdit ? (
          <button className="btn text-xs" onClick={() => setCopying(!copying)}>
            换关键词复制
          </button>
        ) : null}
      </div>
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

      <section className="card p-3 space-y-2" aria-label="矩阵里的 Job">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-medium text-sm flex-1">Job（{matrix.members.length}）</h2>
          <span className="text-xs text-text-muted">
            运行、停用、改提示词在各 Job 页；定时在<Link className="text-accent hover:underline" to="/scheduler">周期调度</Link>页。
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
                <th />
              </tr>
            </thead>
            <tbody>
              {matrix.members.map((m) => (
                <MemberRow key={m.job} member={m} canEdit={canEdit} onRemove={() => save({ jobs: jobs.filter((j) => j !== m.job) })} />
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
