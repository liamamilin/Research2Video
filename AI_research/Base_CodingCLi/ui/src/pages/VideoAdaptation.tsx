// 审稿页上和改编有关的部分：风格、三项审计、讲述方案、简介、事实标签。
// 标准编号见 spec/video-adaptation.md 第一节，审计见 spec/video-adaptation-audit.md。
import { useState } from "react";
import type {
  AuditName,
  CheckIssue,
  Fact,
  ScriptChecks,
  ScriptDescription,
  ScriptPlan,
  Severity,
  StyleCatalog,
  VideoScriptDetail,
} from "@/api/video";
import { cn } from "@/lib/utils";

export const RULE_NAME: Record<string, string> = {
  A1: "观众画像",
  B1: "主旨",
  B2: "钩子",
  B3: "取舍",
  C1: "先铺垫",
  C3: "类比",
  C4: "数字",
  C5: "一段一事",
  C6: "句长",
  C7: "回顾",
  D1: "编造或无依据",
  D2: "放大",
  D3: "数字和推导",
  D4: "出处",
  D5: "审稿用语",
  D6: "数字溯源",
  D7: "行动建议",
  E1: "符号",
  E2: "命令",
  E3: "用语",
  E4: "时长",
  E5: "机械路标",
  E6: "重复数字",
  S1: "栏目风格",
  N1: "新观众疑问",
  N2: "新观众跟不上",
  N3: "测验答错",
};

const AUDIT_NAME: Record<AuditName, string> = { fact: "事实", comprehension: "理解", communication: "表达" };
const SEVERITY: Record<Severity, { label: string; cls: string }> = {
  serious: { label: "严重", cls: "bg-danger/15 text-danger border-danger/30" },
  must: { label: "必须改", cls: "bg-warning/15 text-warning border-warning/30" },
  warn: { label: "提醒", cls: "bg-bg-card text-text-muted border-border" },
};
const SCORE_NAME: Record<string, string> = { hook: "钩子", clarity: "好懂", pacing: "节奏", style: "风格" };

export function pid(si: number, pi: number): string {
  return `S${si + 1}P${pi + 1}`;
}

function where(issue: Pick<CheckIssue, "section" | "paragraph">): string {
  if (issue.section === null) return "全片";
  return issue.paragraph === null ? `第 ${issue.section + 1} 节` : `第 ${issue.section + 1} 节第 ${issue.paragraph + 1} 段`;
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export type LocateIssue = (section: number, paragraph: number | null) => void;

function IssueRow({ issue, showWhere = false, onLocate }: { issue: CheckIssue; showWhere?: boolean; onLocate?: LocateIssue }) {
  return (
    <li className="space-y-0.5">
      <div className="flex flex-wrap gap-1.5 items-baseline">
        <span className={cn("badge border", SEVERITY[issue.severity]?.cls)}>{SEVERITY[issue.severity]?.label ?? issue.severity}</span>
        <span className="badge border border-border font-mono" title={RULE_NAME[issue.rule] ?? issue.rule}>
          {AUDIT_NAME[issue.audit] ?? issue.audit} · {issue.rule}
        </span>
        {showWhere ? <span className="text-text-muted">{where(issue)}</span> : null}
        {onLocate && issue.section != null ? <button className="text-accent hover:underline" onClick={() => onLocate(issue.section!, issue.paragraph)} aria-label={`定位${where(issue)}：${issue.problem}`}>定位此段</button> : null}
        <span>{issue.problem}</span>
      </div>
      {issue.quote ? <p className="text-text-muted pl-1">原句：「{issue.quote}」</p> : null}
      {issue.why || issue.suggestion ? (
        <p className="text-text-muted pl-1">
          {issue.why ? `为什么：${issue.why}` : ""}
          {issue.why && issue.suggestion ? "；" : ""}
          {issue.suggestion ? `建议：${issue.suggestion}` : ""}
        </p>
      ) : null}
      {issue.unhandled ? <p className="text-danger pl-1">{issue.unhandled}</p> : null}
    </li>
  );
}

export function StylePanel({
  detail,
  catalog,
  canRestyle,
  busy,
  onRestyle,
}: {
  detail: VideoScriptDetail;
  catalog: StyleCatalog | null;
  canRestyle: boolean;
  busy: boolean;
  onRestyle: (depth: string, tone: string) => void;
}) {
  const current = detail.version_shown?.meta?.style;
  const defaults = detail.column?.default_style;
  const [depth, setDepth] = useState(current?.depth ?? defaults?.depth ?? "intermediate");
  const [tone, setTone] = useState(current?.tone ?? defaults?.tone ?? "explainer");
  const allowed = detail.column?.allowed_tones ?? [];
  const tones = (catalog?.tones ?? []).filter((t) => !allowed.length || allowed.includes(t.key));

  return (
    <section className="card p-4 text-sm flex flex-wrap items-center gap-x-4 gap-y-2">
      <span>
        风格：<b>{current?.label ?? "短稿一次成稿（这个栏目不分风格）"}</b>
      </span>
      {canRestyle && catalog && (current || detail.column?.writer === "adapt") ? (
        <span className="flex flex-wrap items-center gap-2">
          <select className="input text-xs py-1 w-auto" value={depth} onChange={(e) => setDepth(e.target.value)} aria-label="深浅">
            {catalog.depths.map((d) => (
              <option key={d.key} value={d.key} title={d.audience}>
                {d.name}
              </option>
            ))}
          </select>
          <select className="input text-xs py-1 w-auto" value={tone} onChange={(e) => setTone(e.target.value)} aria-label="语气">
            {tones.map((t) => (
              <option key={t.key} value={t.key} title={t.guidance}>
                {t.name}
              </option>
            ))}
          </select>
          <button className="btn text-xs" onClick={() => onRestyle(depth, tone)} disabled={busy}>
            {busy ? "提交中..." : "用这个风格重写"}
          </button>
          <span className="text-xs text-text-muted">用同一份研究生成新版本，不重新调研，约需十分钟。</span>
        </span>
      ) : null}
    </section>
  );
}

export function ChecksPanel({ checks, onLocate }: { checks: ScriptChecks; onLocate?: LocateIssue }) {
  if (checks.scope === "article_fidelity") return <section className="card p-4 text-sm space-y-3">
    <h2 className="font-medium">原文忠实度审校 · {checks.model_checks_stale || !checks.fidelity_checked ? "待核对当前版本" : checks.summary.passed ? "通过" : "有问题需处理"}</h2>
    <p className="text-xs text-text-muted">{checks.article_summary || "对照保存的文章核对原意与限定条件。"} 此项不代表完成外部事实核验。</p>
    {checks.issues.length ? <ul className="space-y-2 text-xs">{checks.issues.map((item, i) => <IssueRow key={i} issue={item} showWhere onLocate={onLocate} />)}</ul> : null}
  </section>;
  if (checks.mode === "on_request") return <section className="card p-4 text-sm text-text-muted">本版直接根据素材生成，未进行自动审校。</section>;
  const { summary, newcomer, quiz } = checks;
  const unhandled = checks.issues.filter((i) => i.severity === "serious");
  const general = checks.issues.filter((i) => i.section === null && i.severity !== "serious");
  const fixed = checks.initial.filter((i) => i.resolved === true).length;
  const deleted = checks.initial.filter((i) => i.resolved === "deleted").length;
  const audits: AuditName[] = ["fact", "comprehension", "communication"];
  return (
    <section className="card p-4 text-sm space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="font-medium">审校</h2>
        {checks.model_ran ? (
          <span className={summary.passed ? "text-success" : "text-warning"}>{summary.passed ? "通过" : "未通过"}</span>
        ) : (
          <span className="text-text-muted">只跑了规则检查</span>
        )}
        {checks.rounds ? <span className="text-text-muted">自动修改 {checks.rounds} 轮</span> : null}
        {checks.initial.length ? (
          <span className="text-text-muted">
            第一轮发现 {checks.initial.length} 个问题，已改好 {fixed} 个{deleted ? `，删句处理 ${deleted} 个` : ""}
          </span>
        ) : null}
      </div>

      <p className="text-xs text-text-muted">事实与引用问题需要核实；理解、风格和时长检查提供建议，不按数量或评分强制改写。</p>

      <div className="grid gap-2 sm:grid-cols-3 text-xs">
        {audits.map((name) => {
          const counts = summary.by_audit?.[name] ?? { serious: 0, must: 0, warn: 0 };
          return (
            <div key={name} className="rounded border border-border p-2 space-y-0.5">
              <p className="font-medium">{AUDIT_NAME[name]}审计</p>
              <p>
                <span className={counts.serious ? "text-danger" : "text-text-muted"}>严重 {counts.serious}</span>
                <span className="text-text-muted"> · </span>
                <span className={counts.must ? "text-warning" : "text-text-muted"}>必须改 {counts.must}</span>
                <span className="text-text-muted"> · 提醒 {counts.warn}</span>
              </p>
              {name === "comprehension" && quiz.items.length ? (
                <p className={quiz.score !== null && quiz.score < 0.8 ? "text-warning" : "text-text-muted"}>
                  测验 {quiz.items.filter((q) => q.correct).length}/{quiz.items.length}
                </p>
              ) : null}
              {name === "communication" && newcomer ? (
                <p className="text-text-muted">
                  {Object.entries(newcomer.scores)
                    .map(([k, v]) => `${SCORE_NAME[k] ?? k} ${v}/5`)
                    .join(" · ")}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      {checks.model_checks_stale ? (
        <p className="text-xs text-text-muted">这个版本是编辑后保存的，只重跑了规则检查；模型审计是改动前的结果，没有显示。</p>
      ) : null}

      {unhandled.length ? (
        <div className="text-xs space-y-1">
          <p className="text-danger font-medium">需要你处理的严重问题</p>
          <ul className="space-y-2">
            {unhandled.map((issue, i) => (
              <IssueRow key={i} issue={issue} showWhere onLocate={onLocate} />
            ))}
          </ul>
        </div>
      ) : null}

      {checks.issues.some(i => i.section != null && i.severity !== "serious") ? <details open>
        <summary className="cursor-pointer text-xs text-text-muted">段落审校建议</summary>
        <ul className="text-xs space-y-3 mt-2">{checks.issues.filter(i => i.section != null && i.severity !== "serious").map((issue, i) => <IssueRow key={i} issue={issue} showWhere onLocate={onLocate} />)}</ul>
      </details> : null}

      {checks.deletions.length ? (
        <div className="text-xs space-y-1">
          <p className="font-medium">自动删除了 {checks.deletions.length} 句（严重事实问题，两轮修改没改好）</p>
          <ul className="space-y-1">
            {checks.deletions.map((d, i) => (
              <li key={i}>
                <span className="text-text-muted">{where(d)}：</span>
                <s>{d.quote}</s>
                <span className="text-text-muted"> · {d.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {newcomer ? (
        <div className="text-xs space-y-1">
          {newcomer.retell ? (
            <p>
              <span className="text-text-muted">新观众复述的主旨：</span>
              {newcomer.retell}
              {checks.core_message_match === false ? <span className="text-warning">（和方案的主旨不一致）</span> : null}
            </p>
          ) : null}
          {newcomer.comments ? <p className="text-text-muted">{newcomer.comments}</p> : null}
        </div>
      ) : null}

      {quiz.items.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer">理解测验：新观众看完的回答</summary>
          <ol className="mt-2 space-y-2 list-decimal pl-5">
            {quiz.items.map((q) => (
              <li key={q.id}>
                <p>
                  {q.question}
                  {q.correct === true ? <span className="text-success"> ✓</span> : null}
                  {q.correct === false ? <span className="text-danger"> ✗</span> : null}
                </p>
                <p className="text-text-muted">答案要点：{q.expected}</p>
                <p className="text-text-muted">新观众：{q.answer ?? "（没有回答）"}</p>
                {q.note ? <p className="text-text-muted">判分：{q.note}</p> : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      {general.length ? (
        <ul className="text-xs space-y-2">
          {general.map((issue, i) => (
            <IssueRow key={i} issue={issue} onLocate={onLocate} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function ReviewNotes({ checks, version, onLocate }: { checks: ScriptChecks; version: number; onLocate: LocateIssue }) {
  return <aside className="card p-4 space-y-3 lg:sticky lg:top-24 self-start lg:max-h-[calc(100vh-8rem)] overflow-y-auto" aria-label="编辑审校意见">
    <h2 className="text-sm font-medium">对照审校意见（{checks.issues.length}）</h2>
    <p className="text-xs text-text-muted">意见来自保存版 v{version}，尚未包含本次未保存修改；保存不会自动发起审校。</p>
    {checks.issues.length ? <ul className="text-xs space-y-4">{checks.issues.map((issue, i) => <IssueRow key={i} issue={issue} showWhere onLocate={onLocate} />)}</ul>
      : <p className="text-xs text-text-muted">这版没有可定位的审校意见。</p>}
  </aside>;
}

/** Issues for one section header (section-level) or one paragraph. */
export function IssueList({ checks, si, pi }: { checks: ScriptChecks | undefined; si: number; pi: number | null }) {
  if (!checks) return null;
  const items = checks.issues.filter((i) => i.section === si && i.paragraph === pi);
  if (!items.length) return null;
  const serious = items.filter((i) => i.audit === "fact" && i.severity === "serious" && !i.resolved);
  const advice = items.filter((i) => !serious.includes(i));
  return (
    <div className="text-xs space-y-1.5">
      {serious.length ? <ul className="space-y-1.5">{serious.map((issue, i) => <IssueRow key={i} issue={issue} />)}</ul> : null}
      {advice.length ? <details><summary className="cursor-pointer text-text-muted">审校建议 {advice.length} 条</summary><ul className="space-y-1.5 mt-2">{advice.map((issue, i) => <IssueRow key={i} issue={issue} />)}</ul></details> : null}
    </div>
  );
}

export function FactChips({ ids, facts }: { ids: string[] | undefined; facts: Record<string, Fact> }) {
  if (!ids?.length) return null;
  return (
    <>
      {ids.map((id) => {
        const fact = facts[id];
        return (
          <span
            key={id}
            className="badge border border-border bg-bg-card font-mono cursor-help"
            title={fact ? `[${fact.confidence}] ${fact.claim}${fact.caveat ? `（${fact.caveat}）` : ""}` : id}
          >
            {id}
          </span>
        );
      })}
    </>
  );
}

export function PlanPanel({ plan, open = false }: { plan: ScriptPlan; open?: boolean }) {
  return (
    <details className="card p-4 text-sm" open={open}>
      <summary className="cursor-pointer font-medium">讲述方案</summary>
      <div className="mt-3 space-y-3">
        {plan.question ? (
          <p>
            <span className="text-text-muted">核心问题：</span>
            {plan.question}
          </p>
        ) : null}
        <p>
          <span className="text-text-muted">主旨：</span>
          <b>{plan.core_message}</b>
        </p>
        {plan.fit && !plan.fit.ok ? <p className="text-warning text-xs">范围：{plan.fit.note}</p> : null}
        <div className="grid gap-1 text-xs sm:grid-cols-2">
          <p>
            <span className="text-text-muted">观众已经知道：</span>
            {plan.audience.knows.join("；") || "—"}
          </p>
          <p>
            <span className="text-text-muted">观众不知道：</span>
            {plan.audience.lacks.join("；") || "—"}
          </p>
          <p>
            <span className="text-text-muted">关心：</span>
            {plan.audience.cares || "—"}
          </p>
          <p>
            <span className="text-text-muted">看完能做：</span>
            {plan.audience.takeaway || "—"}
          </p>
        </div>
        {plan.chain?.length ? (
          <div className="text-xs space-y-1">
            <p className="text-text-muted">因果链</p>
            <ol className="list-decimal pl-5 space-y-0.5">
              {plan.chain.map((c, i) => (
                <li key={i}>
                  {c.step}
                  <span className="text-text-muted"> · {c.facts.join(" ") || "无事实"}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {plan.misconceptions?.length ? (
          <div className="text-xs space-y-1">
            <p className="text-text-muted">可能的误解</p>
            {plan.misconceptions.map((m, i) => (
              <p key={i}>
                ✗ {m.wrong}
                <span className="text-text-muted"> → {m.fix}</span>
              </p>
            ))}
          </div>
        ) : null}
        <p className="text-xs">
          <span className="text-text-muted">钩子（{plan.hook.kind}）：</span>
          {plan.hook.idea}
        </p>
        {plan.glossary.length ? (
          <div className="text-xs space-y-1">
            <p className="text-text-muted">术语表</p>
            {plan.glossary.map((g) => (
              <p key={g.term}>
                <b>{g.term}</b>：{g.plain}
                {g.needs?.length ? <span className="text-text-muted">（先懂：{g.needs.join("、")}）</span> : null}
                {g.analogy ? (
                  <span className="text-text-muted">
                    {" "}
                    · 类比「{g.analogy}」：
                    {Object.entries(g.mapping)
                      .map(([a, b]) => `${a} = ${b}`)
                      .join("，")}
                    {g.breaks ? `；不成立的地方：${g.breaks}` : ""}
                  </span>
                ) : null}
              </p>
            ))}
          </div>
        ) : null}
        <ol className="text-xs space-y-1 list-decimal pl-5">
          {plan.beats.map((b, i) => (
            <li key={i}>
              <span className="badge border border-border mr-1">{b.role}</span>
              <b>{b.title}</b>：{b.point}
              <span className="text-text-muted"> · {b.budget} 字 · {b.facts.join(" ") || "无事实"}</span>
            </li>
          ))}
        </ol>
        {plan.actions?.length ? (
          <div className="text-xs space-y-1">
            <p className="text-text-muted">行动建议</p>
            {plan.actions.map((a, i) => (
              <p key={i}>
                <b>{a.step}</b>
                <span className="text-text-muted">
                  {" "}
                  · 适用：{a.condition || "—"} · 验证：{a.verify || "—"}
                </span>
              </p>
            ))}
          </div>
        ) : null}
        {plan.scope_out?.length ? (
          <p className="text-xs text-text-muted">这期不讲：{plan.scope_out.join("；")}</p>
        ) : null}
        {plan.left_out.length ? (
          <p className="text-xs text-text-muted">
            没有用的事实：{plan.left_out.map((x) => `${x.fact}（${x.reason}）`).join("；")}
          </p>
        ) : null}
      </div>
    </details>
  );
}

export function DescriptionPanel({ description }: { description: ScriptDescription }) {
  return (
    <details className="card p-4 text-sm">
      <summary className="cursor-pointer font-medium">视频简介（不朗读）</summary>
      <div className="mt-3 space-y-2 text-xs">
        {description.summary ? <p className="text-sm">{description.summary}</p> : null}
        {[
          ["精确数字和口径", description.details],
          ["仍未确定", description.open_questions],
          ["讲稿没展开", description.not_covered],
        ].map(([label, items]) =>
          (items as string[]).length ? (
            <div key={label as string}>
              <p className="text-text-muted">{label as string}</p>
              <ul className="list-disc pl-5">
                {(items as string[]).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
          ) : null,
        )}
        {description.sources.length ? (
          <div>
            <p className="text-text-muted">来源</p>
            <ul className="list-disc pl-5">
              {description.sources.map((s) => (
                <li key={s.url}>
                  <a href={s.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                    {host(s.url)}
                  </a>
                  <span className="text-text-muted"> · {s.facts.join(" ")}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </details>
  );
}
