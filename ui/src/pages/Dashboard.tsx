import { useState } from "react";
import { listJobs, getUsage, getReportStats, getRatings, getHealth } from "@/api";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { ErrorState } from "@/components/ErrorState";
import { usePageResource } from "@/lib/use-page-resource";
import { cn, timeAgo, formatTokens } from "@/lib/utils";

const loadUsage = (signal: AbortSignal) => getUsage(30, false, signal);
const loadHealth = (signal: AbortSignal) => getHealth(false, signal);

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-text-muted">未运行</span>;
  const colors: Record<string, string> = {
    success: "bg-green-900/40 text-green-400 border-green-800",
    failed: "bg-red-900/40 text-red-400 border-red-800",
    skipped: "bg-yellow-900/40 text-yellow-400 border-yellow-800",
    cancelled: "bg-yellow-900/40 text-yellow-400 border-yellow-800",
  };
  return (
    <span className={cn("badge border", colors[status] || "bg-gray-800 text-gray-400")}>
      {({ success: "已完成", failed: "失败", skipped: "已跳过", cancelled: "已取消", running: "运行中" } as Record<string,string>)[status] || status}
    </span>
  );
}

export function DashboardPage() {
  const { data: jobData, error, loading, reload } = usePageResource(listJobs, 15000);
  const jobs = jobData ?? [];
  const { data: usage, error: usageError, reload: reloadUsage } = usePageResource(loadUsage);
  const { data: reportStats, error: reportError, reload: reloadReports } = usePageResource(getReportStats);
  const { data: ratings, error: ratingError, reload: reloadRatings } = usePageResource(getRatings);
  const { data: health, error: healthError, reload: reloadHealth } = usePageResource(loadHealth);
  const statsError = Boolean(usageError || reportError || ratingError || healthError);
  const [healthOpen, setHealthOpen] = useState(false);
  const loadStats = () => { void reloadUsage(); void reloadReports(); void reloadRatings(); void reloadHealth(); };

  const total = jobs.length;
  const enabled = jobs.filter((j) => j.enabled).length;
  const running = jobs.filter((j) => j.is_running).length;
  const lastFailed = jobs.filter((j) => j.state?.last_status === "failed").length;

  const stats = [
    { label: "全部 Job", value: total, color: "", href: "/jobs" },
    { label: "已启用", value: enabled, color: "text-accent", href: "/jobs?status=enabled" },
    { label: "运行中", value: running, color: running > 0 ? "text-accent" : "", href: "/jobs?status=running" },
    { label: "上次失败", value: lastFailed, color: lastFailed > 0 ? "text-danger" : "", href: "/jobs?status=failed" },
  ];

  const recent = jobs
    .filter((j) => j.state?.last_run_at)
    .sort((a, b) => (b.state?.last_run_at || "").localeCompare(a.state?.last_run_at || ""))
    .slice(0, 10);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1"><h1 className="text-xl font-semibold tracking-tight">研究总览</h1><p className="text-xs text-text-muted mt-1">查看研究任务的运行、近期报告与资源使用情况。</p></div><Link className="btn text-xs" to="/rounds">情报轮次 ↗</Link><button className="btn text-xs" disabled={loading} onClick={() => { void reload(); loadStats(); }}><RefreshCw className="h-3.5 w-3.5" />刷新</button>
        {health && (
          <button
            onClick={() => setHealthOpen((v) => !v)}
            className="text-xs"
            title={health.checks.map((c) => `${c.name}: ${c.status} — ${c.detail}`).join("\n")}
          >
            <span
              className={cn(
                "badge border",
                health.status === "ok"
                  ? "bg-green-900/40 text-green-400 border-green-800"
                  : health.status === "warn"
                    ? "bg-yellow-900/40 text-yellow-400 border-yellow-800"
                    : "bg-red-900/40 text-red-400 border-red-800"
              )}
            >
              系统 {health.status === "ok" ? "正常" : health.status === "warn" ? `注意 ${health.warnings.length}` : `异常 ${health.errors.length}`}
            </span>
          </button>
        )}
      </div>

      {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
      {statsError ? <p className="text-xs text-warning">部分统计暂时无法读取，请刷新重试。</p> : null}
      {health && healthOpen && (
        <div className="card p-3 space-y-1.5">
          <div className="text-xs text-text-muted">
            检查于 {health.checked_at}（不含索引/向量/轮次等深度检查）
          </div>
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1">
            {health.checks.map((c) => (
              <div key={`${c.name}-${c.detail}`} className="flex items-start gap-2 text-xs">
                <span
                  className={cn(
                    "mt-0.5 w-1.5 h-1.5 rounded-full shrink-0",
                    c.status === "ok" ? "bg-success" : c.status === "warn" ? "bg-warning" : "bg-danger"
                  )}
                />
                <span className="font-mono">{c.name}</span>
                <span className="text-text-muted truncate">{c.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {stats.map((s) => (
          <Link key={s.label} to={s.href} className="card p-4 hover:bg-bg-hover">
            <div className="text-xs text-text-muted">{s.label}</div>
            <div className={cn("text-2xl font-bold mt-1", s.color)}>
              {!jobData ? "—" : s.value}
            </div>
          </Link>
        ))}
      </div>

      <details className="card" open={usage?.budget?.warn || usage?.budget?.exceeded || undefined}><summary className="p-4 text-sm cursor-pointer">用量、预算与报告库<span className="ml-2 text-xs text-text-muted">{reportStats ? `${reportStats.total} 篇报告 · ` : ""}按配置单价估算</span></summary><div className="px-4 pb-4 space-y-3">
      {usage?.budget && (
        <div className={cn("card px-4 py-3 space-y-2",
          usage.budget.exceeded && "border-danger/60")}>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-text-muted">月度预算</span>
            <span className={cn("font-mono",
              usage.budget.exceeded ? "text-danger" :
              usage.budget.warn ? "text-warning" : "text-text")}>
              ${usage.budget.spent.toFixed(2)}{usage.budget.limit > 0 ? ` / $${usage.budget.limit.toFixed(2)}` : " · 未设置上限"}
            </span>
            {usage.budget.exceeded && (
              <span className="text-danger">已超限{usage.budget.block_pipeline ? "，轮次已被阻止" : ""}</span>
            )}
            {!usage.budget.exceeded && usage.budget.warn && (
              <span className="text-warning">接近上限</span>
            )}
          </div>
          <div className="h-1.5 w-full bg-bg-hover rounded-full overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all",
                usage.budget.exceeded ? "bg-danger" :
                usage.budget.warn ? "bg-warning" : "bg-accent")}
              style={{ width: `${Math.min(100, usage.budget.ratio * 100).toFixed(1)}%` }}
            />
          </div>
          {usage.budget.limit <= 0 && (
            <div className="text-[11px] text-text-muted">
              未设置预算上限（budget.monthly_usd_limit = 0 表示不限制）
              {usage.budget.has_pricing ? ` · 本月已用 $${usage.budget.spent.toFixed(2)}` : " · 未配置 ai.pricing，无法估算金额"}
            </div>
          )}
          {(usage.budget.per_user?.length ?? 0) > 0 && (
            <div className="text-[11px] text-text-muted space-y-0.5">
              <div>本月用量归属（自 {usage.budget.window_start} 起）：</div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                {usage.budget.per_user!.slice(0, 5).map((u) => (
                  <span key={u.user} className="font-mono">
                    {u.user} · {formatTokens(u.total_tokens)} tok · {u.runs} 次
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {reportStats && (
        <div className="card px-4 py-2.5 text-xs text-text-muted">
          报告库：{reportStats.total} 篇 · {(reportStats.total_size_bytes / 1024 / 1024).toFixed(1)} MB · {reportStats.categories} 个分类
          {ratings && ratings.count > 0 && ratings.average != null && (
            <span> · 平均评分 <span className="text-warning">★ {ratings.average.toFixed(1)}</span>（{ratings.count} 篇）</span>
          )}
        </div>
      )}

      {/* Usage (last 30 days) */}
      {usage && (usage.totals?.runs ?? 0) > 0 && (
        <div className="card">
          <div className="px-4 py-3 border-b border-border font-medium text-sm flex items-center gap-2">
            用量（近 30 天）
            {usage.estimated_cost_usd != null && (
              <span className="text-xs text-text-muted font-normal">
                估算成本 ${usage.estimated_cost_usd.toFixed(2)}
              </span>
            )}
          </div>
          <div className="px-4 py-3 grid grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
            <div>
              <div className="text-xs text-text-muted">运行次数</div>
              <div className="font-mono">{usage.totals.runs_with_usage} 次（含用量）</div>
            </div>
            <div>
              <div className="text-xs text-text-muted">总 tokens</div>
              <div className="font-mono">{formatTokens(usage.totals.total_tokens)}</div>
            </div>
            <div>
              <div className="text-xs text-text-muted">输入 / 输出</div>
              <div className="font-mono">
                {formatTokens(usage.totals.prompt_tokens)} / {formatTokens(usage.totals.completion_tokens)}
              </div>
            </div>
            <div>
              <div className="text-xs text-text-muted">搜索次数</div>
              <div className="font-mono">{usage.totals.searches}</div>
            </div>
          </div>
          {usage.per_model.length > 0 && (
            <div className="px-4 pb-3 text-xs text-text-muted space-y-0.5">
              {usage.per_model.slice(0, 4).map((m) => (
                <div key={m.model} className="flex gap-2">
                  <span className="font-mono">{m.model}</span>
                  <span>· {m.runs} 次 · {formatTokens(m.total_tokens)} tokens</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      </div></details>

      <div className="card">
        <div className="px-4 py-3 border-b border-border font-medium text-sm">
          最近运行
        </div>
        {recent.length === 0 ? (
          <div className="p-4 text-sm text-text-muted">暂无运行记录</div>
        ) : (
          <div className="divide-y divide-border text-sm">
            {recent.map((j) => (
              <div key={j.name} className="px-4 py-2.5 flex items-center gap-3">
                <StatusBadge status={j.state?.last_status || null} />
                <Link to={`/jobs/${encodeURIComponent(j.name)}`} className="text-xs flex-1 truncate text-accent hover:underline" title={j.description}>{j.name}</Link>
                {j.state?.last_usage?.total_tokens != null && (
                  <span className="text-text-muted text-xs">
                    {formatTokens(j.state.last_usage.total_tokens)} tok
                  </span>
                )}
                {j.state?.last_duration_seconds != null && (
                  <span className="text-text-muted text-xs">
                    {Math.round(j.state.last_duration_seconds)}s
                  </span>
                )}
                <span className="text-text-muted text-xs" title={j.state?.last_run_at || ""}>
                  {timeAgo(j.state?.last_run_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
