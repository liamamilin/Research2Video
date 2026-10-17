// 审稿页上和改编有关的部分：风格、审校结果、讲述方案、简介、事实标签。
// 标准编号见 spec/video-adaptation.md 第一节。
import { useState } from "react";
import type {
  CheckIssue,
  Fact,
  ScriptChecks,
  ScriptDescription,
  ScriptPlan,
  StyleCatalog,
  VideoScriptDetail,
} from "@/api/video";
import { cn } from "@/lib/utils";

export const RULE_NAME: Record<string, string> = {
  A1: "观众画像",
  B1: "主旨",
  B2: "钩子",
  B3: "取舍",
  B4: "收束",
  C1: "先铺垫",
  C2: "背景",
  C3: "类比",
  C4: "数字",
  C5: "一段一事",
  C6: "句长",
  C7: "路标",
  C8: "并列称呼",
  D1: "新增事实",
  D2: "放大",
  D3: "数字等价",
  D4: "出处",
  D5: "审稿用语",
  E1: "符号",
  E2: "命令",
  E3: "用语",
  E4: "时长",
};

const SCORE_NAME: Record<string, string> = { hook: "钩子", clarity: "好懂", pacing: "节奏", style: "风格" };

export function pid(si: number, pi: number): string {
  return `S${si + 1}P${pi + 1}`;
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function RuleBadge({ issue }: { issue: Pick<CheckIssue, "rule" | "severity"> }) {
  return (
    <span
      className={cn(
        "badge border font-mono",
        issue.severity === "must" ? "bg-danger/15 text-danger border-danger/30" : "bg-warning/15 text-warning border-warning/30",
      )}
      title={RULE_NAME[issue.rule] ?? issue.rule}
    >
      {issue.rule}
    </span>
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
    <section className="card text-sm flex flex-wrap items-center gap-x-4 gap-y-2">
      <span>
        风格：<b>{current?.label ?? "旧版写稿（没有分风格）"}</b>
      </span>
      {canRestyle && catalog ? (
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
          <span className="text-xs text-text-muted">用同一份研究生成新版本，不重新调研，约需几分钟。</span>
        </span>
      ) : null}
    </section>
  );
}

export function ChecksPanel({ checks }: { checks: ScriptChecks }) {
  const { summary, newcomer, fidelity } = checks;
  const general = checks.rules.filter((i) => i.section === null);
  const modelRan = newcomer !== null && fidelity !== null;
  return (
    <section className="card text-sm space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="font-medium">审校</h2>
        {modelRan ? (
          <span className={summary.passed ? "text-success" : "text-warning"}>{summary.passed ? "通过" : "未通过"}</span>
        ) : (
          <span className="text-text-muted">只跑了规则检查</span>
        )}
        <span className={summary.must ? "text-danger" : "text-text-muted"}>必须改 {summary.must}</span>
        <span className="text-text-muted">提醒 {summary.warn}</span>
        {modelRan ? (
          <>
            <span className={summary.questions ? "text-warning" : "text-text-muted"}>新观众提问 {summary.questions}</span>
            <span className={summary.fidelity ? "text-danger" : "text-text-muted"}>忠实问题 {summary.fidelity}</span>
            {checks.rounds ? <span className="text-text-muted">自动修改 {checks.rounds} 轮</span> : null}
          </>
        ) : null}
      </div>
      {checks.model_checks_stale ? (
        <p className="text-xs text-text-muted">这个版本是编辑后保存的，只重跑了规则检查；新观众评审和忠实核对是改动前的结果，没有显示。</p>
      ) : null}
      {newcomer ? (
        <div className="text-xs space-y-1">
          <p className="flex flex-wrap gap-3">
            {Object.entries(newcomer.scores).map(([k, v]) => (
              <span key={k} className={(v ?? 0) < 4 ? "text-warning" : "text-text-muted"}>
                {SCORE_NAME[k] ?? k} {v}/5
              </span>
            ))}
          </p>
          {newcomer.retell ? (
            <p>
              <span className="text-text-muted">新观众复述的主旨：</span>
              {newcomer.retell}
              {fidelity?.core_message_match === false ? (
                <span className="text-warning">（和方案的主旨不一致{fidelity.core_message_note ? `：${fidelity.core_message_note}` : ""}）</span>
              ) : null}
            </p>
          ) : null}
          {newcomer.comments ? <p className="text-text-muted">{newcomer.comments}</p> : null}
        </div>
      ) : null}
      {general.length ? (
        <ul className="text-xs space-y-1">
          {general.map((issue, i) => (
            <li key={i} className="flex gap-2 items-baseline">
              <RuleBadge issue={issue} />
              <span>{issue.detail}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** Issues for one section header (section-level) or one paragraph. */
export function IssueList({ checks, si, pi }: { checks: ScriptChecks | undefined; si: number; pi: number | null }) {
  if (!checks) return null;
  const rules = checks.rules.filter((i) => i.section === si && i.paragraph === pi);
  const key = pi === null ? null : pid(si, pi);
  const fidelity = key ? (checks.fidelity?.issues ?? []).filter((i) => i.id === key) : [];
  const reading = key ? checks.newcomer?.paragraphs[key] : undefined;
  if (!rules.length && !fidelity.length && !reading) return null;
  return (
    <ul className="text-xs space-y-0.5">
      {rules.map((issue, i) => (
        <li key={`r${i}`} className="flex gap-2 items-baseline">
          <RuleBadge issue={issue} />
          <span>{issue.detail}</span>
        </li>
      ))}
      {fidelity.map((issue, i) => (
        <li key={`f${i}`} className="flex gap-2 items-baseline">
          <RuleBadge issue={{ rule: issue.rule, severity: "must" }} />
          <span>核对：{issue.detail}</span>
        </li>
      ))}
      {reading?.questions.map((q, i) => (
        <li key={`q${i}`} className="text-warning">
          新观众会问：{q}
        </li>
      ))}
      {reading?.lost.map((q, i) => (
        <li key={`l${i}`} className="text-warning">
          新观众跟不上：{q}
        </li>
      ))}
    </ul>
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

export function PlanPanel({ plan }: { plan: ScriptPlan }) {
  return (
    <details className="card text-sm">
      <summary className="cursor-pointer font-medium">讲述方案</summary>
      <div className="mt-3 space-y-3">
        <p>
          <span className="text-text-muted">主旨：</span>
          <b>{plan.core_message}</b>
        </p>
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
    <details className="card text-sm">
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
