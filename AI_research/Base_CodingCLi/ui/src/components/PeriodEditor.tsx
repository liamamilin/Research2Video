import { useEffect, useState } from "react";

import { previewPeriod, setPeriod, type Matrix, type MatrixMember, type PeriodBody, type PeriodKind, type PeriodPreview } from "@/api/matrices";
import { errorMessage, useToast } from "@/lib/toast";

export const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
const KIND_NAMES: Record<PeriodKind, string> = { manual: "手动", daily: "每天", weekdays: "工作日", weekly: "每周几天", monthly: "每月一次" };

export function periodWords(member: MatrixMember) {
  const plan = member.schedule;
  if (!plan) return "手动";
  const period = plan.period;
  const when = period?.time ?? "";
  const words =
    period?.kind === "daily" ? `每天 ${when}`
    : period?.kind === "weekdays" ? `工作日 ${when}`
    : period?.kind === "weekly" ? `每周${(period.days ?? []).map((d) => WEEKDAYS[d]).join("、")} ${when}`
    : period?.kind === "monthly" ? `每月 ${period.day} 号 ${when}`
    : null;
  if (words === null) return `${plan.enabled ? "定时" : "定时（已暂停）"} ${plan.schedule}`;  // a cron the period words cannot say
  return plan.enabled ? words : `${words}（已暂停）`;
}

/** 改周期：挑一种、选时间，当场看下次几点跑和每月大约花多少；开定时前要确认 */
export function PeriodEditor({ matrixKey, member, onDone, onCancel }: { matrixKey: string; member: MatrixMember; onDone: (m: Matrix) => void; onCancel: () => void }) {
  const toast = useToast();
  const now = member.schedule?.period;
  const [form, setForm] = useState<PeriodBody>({
    kind: !now ? "manual" : now.kind === "custom" ? "daily" : now.kind,
    time: now?.time ?? "05:00",
    days: now?.days?.length ? now.days : [1],
    day: now?.day ?? 1,
  });
  const [preview, setPreview] = useState<PeriodPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const on = !!member.schedule?.enabled;

  useEffect(() => {
    if (form.kind === "manual") { setPreview(null); setError(""); return; }
    let live = true;
    previewPeriod(matrixKey, member.job, form).then((p) => { if (live) { setPreview(p); setError(""); } })
      .catch((e) => { if (live) { setPreview(null); setError(errorMessage(e)); } });
    return () => { live = false; };
  }, [matrixKey, member.job, form]);

  const cost = preview
    ? `每月约 ${preview.runs_per_month} 次运行${preview.searches_per_run ? `，上次每次用了 ${preview.searches_per_run} 次搜索，约 ${preview.searches_per_month} 次` : ""}`
    : "";
  const save = async () => {
    if (form.kind !== "manual" && !on && !window.confirm(`打开定时后「${member.name}」会自己运行并花搜索额度：${cost}。确定？`)) return;
    setBusy(true);
    try {
      onDone(await setPeriod(matrixKey, member.job, { ...form, confirm: form.kind !== "manual" }));
      toast.success(form.kind === "manual" ? "改成手动了，不会自己跑" : "周期保存了");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const set = (patch: Partial<PeriodBody>) => setForm({ ...form, ...patch });
  return (
    <div className="mt-2 space-y-2 border border-border rounded p-2 text-xs" aria-label={`改 ${member.name} 的周期`}>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="周期" className="input w-auto" value={form.kind} onChange={(e) => set({ kind: e.target.value as PeriodKind })}>
          {(Object.keys(KIND_NAMES) as PeriodKind[]).map((k) => <option key={k} value={k}>{KIND_NAMES[k]}</option>)}
        </select>
        {form.kind !== "manual" ? (
          <input aria-label="几点跑" type="time" className="input w-auto" value={form.time} onChange={(e) => set({ time: e.target.value })} />
        ) : null}
        {form.kind === "monthly" ? (
          <select aria-label="每月几号" className="input w-auto" value={form.day} onChange={(e) => set({ day: Number(e.target.value) })}>
            {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d} 号</option>)}
          </select>
        ) : null}
      </div>
      {form.kind === "weekly" ? (
        <div className="flex flex-wrap gap-2">
          {WEEKDAYS.map((name, d) => (
            <label key={d} className="flex items-center gap-1">
              <input type="checkbox" aria-label={`周${name}`} checked={form.days.includes(d)}
                onChange={(e) => set({ days: e.target.checked ? [...form.days, d] : form.days.filter((x) => x !== d) })} />
              周{name}
            </label>
          ))}
        </div>
      ) : null}
      {form.kind === "manual" ? <p className="text-text-muted">手动：不会自己跑，也不花搜索额度，点「运行一次」才跑。</p> : null}
      {error ? <p role="alert" className="text-danger">{error}</p> : null}
      {preview ? (
        <p className="text-text-muted">
          接下来：{preview.next_runs.map((t) => t.replace("T", " ").slice(5, 16)).join("、")}。{cost}。
        </p>
      ) : null}
      <div className="flex gap-2">
        <button className="btn btn-primary text-xs" disabled={busy || (form.kind !== "manual" && !preview)} onClick={save}>保存</button>
        <button className="btn text-xs" onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}

