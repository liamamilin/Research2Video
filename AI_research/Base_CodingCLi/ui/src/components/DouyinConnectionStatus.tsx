import type { DouyinJob, DouyinOverview } from "@/api/douyin";

type Props = Pick<DouyinOverview, "account" | "checks">;

function checkedTime(value: number) {
  return new Date(value * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

function CheckRecord({ row, historical = false }: { row: DouyinJob; historical?: boolean }) {
  const label = row.kind === "VERIFY" ? "登录验证" : "作品回查";
  const state = row.error ? "失败" : ["QUEUED", "RUNNING"].includes(row.status) ? "进行中" : "完成";
  return <li className="space-y-1">
    <p className={row.error && !historical ? "text-warning" : "text-text-muted"}>
      {label} · {state} · <time dateTime={new Date(row.updated_at * 1000).toISOString()}>{checkedTime(row.updated_at)}</time>
    </p>
    <p role={row.error && !historical ? "alert" : undefined} className={historical ? "text-text-muted" : row.error ? "text-warning" : "text-text-muted"}>
      {row.error || row.phase}
    </p>
  </li>;
}

export function DouyinConnectionStatus({ account, checks }: Props) {
  const verifiedAt = account.connected && account.verified_at && account.verified_at > 0 ? account.verified_at : 0;
  const records = checks.filter(row => row.kind === "VERIFY" || row.error)
    .sort((a, b) => b.updated_at - a.updated_at);
  const historical = records.filter(row => verifiedAt > 0 && row.updated_at <= verifiedAt
    && !["QUEUED", "RUNNING"].includes(row.status));
  const historyIds = new Set(historical.map(row => row.id));
  const current = records.filter(row => !historyIds.has(row.id));
  if (!verifiedAt && !records.length) return null;

  return <section aria-label="抖音连接状态" className="space-y-3 text-xs">
    {verifiedAt ? <p className="text-success">抖音账号已连接 · 最近登录验证成功：<time dateTime={new Date(verifiedAt * 1000).toISOString()}>{checkedTime(verifiedAt)}</time></p> : null}
    {current.length ? <ul aria-label="最近抖音检查" className="space-y-3">{current.slice(0, 3).map(row => <CheckRecord key={row.id} row={row} />)}</ul> : null}
    {historical.length ? <details className="card p-4">
      <summary className="cursor-pointer">历史检查记录（{historical.length}）</summary>
      <p className="text-text-muted mt-3">以下记录早于或发生在最近一次登录验证成功时，不代表当前登录状态；原任务记录保留。</p>
      <ul aria-label="历史抖音检查" className="space-y-3 mt-3">{historical.map(row => <CheckRecord key={row.id} row={row} historical />)}</ul>
    </details> : null}
  </section>;
}
