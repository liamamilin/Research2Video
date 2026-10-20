import { useState } from "react";

import {
  confirmVideoPlan,
  discardVideoPlan,
  redoVideoPlan,
  type PendingPlan,
  type PlanEdits,
  type VideoScriptDetail,
} from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";
import { PlanPanel } from "@/pages/VideoAdaptation";

interface Form {
  question: string;
  core_message: string;
  hook_idea: string;
  beats: { title: string; point: string }[];
}

function formOf(pending: PendingPlan): Form {
  const plan = pending.plan;
  return {
    question: plan.question ?? "",
    core_message: plan.core_message,
    hook_idea: plan.hook.idea,
    beats: plan.beats.map((b) => ({ title: b.title, point: b.point })),
  };
}

/** 只送改了的字：没改的不送，服务端就不会把它算作改动（确认后的方案和模型给的一字不差）。 */
export function changedEdits(pending: PendingPlan, form: Form): PlanEdits {
  const before = formOf(pending);
  const edits: PlanEdits = {};
  if (form.question !== before.question) edits.question = form.question;
  if (form.core_message !== before.core_message) edits.core_message = form.core_message;
  if (form.hook_idea !== before.hook_idea) edits.hook_idea = form.hook_idea;
  const beats = form.beats.map((b, i) => ({
    ...(b.title !== before.beats[i].title ? { title: b.title } : {}),
    ...(b.point !== before.beats[i].point ? { point: b.point } : {}),
  }));
  if (beats.some((b) => Object.keys(b).length)) edits.beats = beats;
  return edits;
}

/** 配方 writing.pause_after_plan：方案定好后停下，确认（可改字）、带意见重做或放弃。 */
export function PlanReview({ detail, canEdit, onChanged }: {
  detail: VideoScriptDetail; canEdit: boolean; onChanged: (d: VideoScriptDetail) => void;
}) {
  const toast = useToast();
  const pending = detail.pending_plan;
  const [form, setForm] = useState<Form | null>(pending ? formOf(pending) : null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"" | "confirm" | "redo" | "discard">("");
  if (!pending || !form || detail.status !== "PLAN_READY") return null;
  const edits = changedEdits(pending, form);
  const edited = Object.keys(edits).length > 0;

  const act = async (kind: "confirm" | "redo" | "discard", text: string, call: () => Promise<VideoScriptDetail>, done: string) => {
    if (!window.confirm(text)) return;
    setBusy(kind);
    try {
      onChanged(await call());
      toast.success(done);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy("");
    }
  };
  const field = (key: "question" | "core_message" | "hook_idea", label: string) => (
    <label className="block space-y-1">
      <span className="text-xs text-text-muted">{label}</span>
      <textarea
        aria-label={label}
        className="input w-full min-h-[2.5rem]"
        rows={key === "hook_idea" ? 3 : 2}
        value={form[key]}
        disabled={!canEdit}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
      />
    </label>
  );

  return (
    <section className="card space-y-3 text-sm border-warning/40" aria-label="待确认的方案">
      <div>
        <p className="font-medium">讲述方案定好了，等你确认再写稿</p>
        <p className="text-xs text-text-muted">
          这个配方设了「方案定好后暂停」。确认后才会写稿（调用写稿和审校模型）；不改字直接确认，写出来的稿子和不暂停时一样。
          {pending.note ? ` 这版是按你的意见重做的：「${pending.note}」` : ""}
        </p>
        {pending.problems.length ? (
          <ul className="mt-1 text-xs text-warning list-disc pl-4">
            {pending.problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
        ) : null}
      </div>

      <PlanPanel plan={pending.plan} open />

      <div className="space-y-2">
        <p className="text-xs text-text-muted">可以改这些字（节数、事实、长度分配不变，审校会按方案检查稿子）。每节左边是标题，右边是这一节观众要记住的一句话：</p>
        {field("question", "核心问题")}
        {field("core_message", "主旨")}
        {field("hook_idea", "开场钩子")}
        <ol className="space-y-2">
          {form.beats.map((beat, i) => (
            <li key={i} className="grid gap-1 sm:grid-cols-[1.5rem_14rem_1fr] items-start">
              <span className="text-xs text-text-muted pt-2">{i + 1}.</span>
              <textarea
                aria-label={`第 ${i + 1} 节标题`}
                className="input"
                rows={2}
                value={beat.title}
                disabled={!canEdit}
                onChange={(e) => setForm({ ...form, beats: form.beats.map((b, j) => (j === i ? { ...b, title: e.target.value } : b)) })}
              />
              <textarea
                aria-label={`第 ${i + 1} 节要点`}
                className="input"
                rows={2}
                value={beat.point}
                disabled={!canEdit}
                onChange={(e) => setForm({ ...form, beats: form.beats.map((b, j) => (j === i ? { ...b, point: e.target.value } : b)) })}
              />
            </li>
          ))}
        </ol>
      </div>

      {canEdit ? (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <button
              className="btn btn-primary"
              disabled={busy !== ""}
              onClick={() => act("confirm",
                `${edited ? "按改过的方案" : "按这份方案"}写稿？会调用写稿模型和审校模型。`,
                () => confirmVideoPlan(detail.id, edits), "已确认方案，开始写稿")}
            >
              {busy === "confirm" ? "…" : edited ? "确认改动并写稿" : "确认并写稿"}
            </button>
            {edited ? (
              <button className="btn" disabled={busy !== ""} onClick={() => setForm(formOf(pending))}>撤销改动</button>
            ) : null}
            <button
              className="btn"
              disabled={busy !== ""}
              onClick={() => act("discard", "放弃这份方案？不写稿；文稿回到之前的状态。", () => discardVideoPlan(detail.id), "已放弃方案")}
            >
              {busy === "discard" ? "…" : "放弃"}
            </button>
          </div>
          <div className="flex flex-wrap items-start gap-2">
            <textarea
              aria-label="对方案的意见"
              className="input flex-1 min-w-[16rem]"
              rows={2}
              placeholder="对方案的意见，比如：开场换成读者自己会遇到的场景；少讲历史，多讲怎么判断"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button
              className="btn"
              disabled={busy !== "" || !note.trim()}
              onClick={() => act("redo", "按你的意见重做方案？会调用写稿模型；新方案出来前旧方案还在。",
                () => redoVideoPlan(detail.id, note), "已开始重做方案")}
            >
              {busy === "redo" ? "…" : "带意见重做"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
