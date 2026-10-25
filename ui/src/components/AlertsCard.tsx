import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, ChevronDown, ArrowUpRight } from "lucide-react";
import { ackAutomationAlert, listAutomationAlerts, type AutomationAlert } from "@/api/video";
import { useAuthStore } from "@/lib/auth-store";

/** 一次先列这么多，其余折起来：一屏红字只会被忽略。 */
const SHOWN = 3;

/** 自动化异常：最近 7 天自己跑的东西哪里出了问题。没有就不显示；「知道了」之后不再出现。 */
export function AlertsCard({ compact = false }: { compact?: boolean }) {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const [alerts, setAlerts] = useState<AutomationAlert[]>([]);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      listAutomationAlerts()
        .then((rows) => alive && (setAlerts(Array.isArray(rows) ? rows : []), setError("")))
        .catch((e) => alive && setError(e instanceof Error ? e.message : "读取自动化异常失败"));
    load();
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, 60000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  if (!alerts.length && !error) return null;

  const ack = async (key: string) => {
    try {
      await ackAutomationAlert(key);
      setAlerts((rows) => rows.filter((a) => a.key !== key));
    } catch (e) {
      setError(e instanceof Error ? e.message : "没记上");
    }
  };

  const ackAll = async () => {
    const keys = alerts.map((a) => a.key);
    const done = await Promise.allSettled(keys.map((key) => ackAutomationAlert(key)));
    const acked = new Set(keys.filter((_, i) => done[i].status === "fulfilled"));
    setAlerts((rows) => rows.filter((a) => !acked.has(a.key)));
    if (acked.size < keys.length) setError("有几条没记上，再点一次");
  };

  const shown = all ? alerts : alerts.slice(0, SHOWN);

  if (compact) {
    const grouped = new Map<string, AutomationAlert[]>();
    for (const alert of alerts) {
      const key = JSON.stringify([alert.kind, alert.title, alert.detail, alert.link]);
      grouped.set(key, [...(grouped.get(key) ?? []), alert]);
    }
    return <section className="card overflow-hidden" aria-label="全局自动化提醒">
      <button className="w-full flex flex-wrap items-center gap-2 px-4 py-3 text-left" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <AlertCircle className="w-4 h-4 text-warning shrink-0" /><span className="text-xs font-medium">自动化提醒</span>
        <span className="text-xs text-text-muted">{alerts.length} 条记录 · 最近 7 天 · 全部矩阵</span>
        <span className="ml-auto text-xs text-text-muted">{expanded ? "收起" : "展开查看"}</span><ChevronDown className={`w-3.5 h-3.5 text-text-muted ${expanded ? "rotate-180" : ""}`} />
      </button>
      {expanded ? <div className="px-4 pb-3 border-t border-border">
        <div className="flex items-center justify-between gap-2 py-3"><p className="text-xs text-text-muted">相同问题合并显示，保留每次运行的记录。</p>{canEdit && alerts.length > 1 ? <button className="btn text-xs" onClick={ackAll}>全部知道了</button> : null}</div>
        {error ? <p className="text-xs text-danger" role="alert">{error}</p> : null}
        <ul className="divide-y divide-border">{[...grouped.values()].map((group) => {
          const alert = group[0];
          const deprecated = /ModelDeprecated|has been deprecated/i.test(alert.detail);
          const title = deprecated ? "研究任务使用的模型已停用" : alert.title;
          return <li key={alert.key} className="py-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{title}</span>{group.length > 1 ? <span className="badge border border-border text-text-muted">重复 {group.length} 次</span> : null}<time className="text-xs text-text-muted ml-auto">{new Date(alert.at * 1000).toLocaleString("zh-CN", { hour12: false })}</time></div>
            {deprecated ? <p className="text-xs text-text-muted">更换任务使用的模型后再重试。已有文稿和成片可继续查看。</p> : null}
            <div className="flex flex-wrap items-center gap-3 text-xs">{alert.link ? <Link className="text-accent hover:underline inline-flex items-center gap-1" to={alert.link}>查看相关任务<ArrowUpRight className="w-3 h-3" /></Link> : null}{deprecated && user?.role === "admin" ? <Link className="text-accent hover:underline" to="/settings">检查模型设置</Link> : null}
              {canEdit ? <button className="text-text-muted hover:text-text" onClick={() => Promise.all(group.map((a) => ack(a.key)))}>知道了{group.length > 1 ? `（${group.length} 条）` : ""}</button> : null}
            </div>
            {alert.detail ? <details className="text-xs text-text-muted"><summary className="cursor-pointer">运行详情</summary><p className="mt-2 p-3 rounded bg-bg break-words font-mono leading-5">{alert.detail}</p></details> : null}
          </li>;
        })}</ul>
      </div> : error ? <p className="px-4 pb-3 text-xs text-danger" role="alert">{error}</p> : null}
    </section>;
  }

  return (
    <section className="card space-y-2 border-danger" aria-label="自动化异常">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-medium text-sm text-danger">自动化异常（最近 7 天，共 {alerts.length} 条）</h2>
        {canEdit && alerts.length > 1 ? <button className="btn text-xs" onClick={ackAll}>全部知道了</button> : null}
      </div>
      {error ? <p className="text-sm text-danger">{error}</p> : null}
      <ul className="divide-y divide-border">
        {shown.map((a) => (
          <li key={a.key} className="py-2 space-y-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-xs text-text-muted">{new Date(a.at * 1000).toLocaleString("zh-CN", { hour12: false })}</span>
              {a.link ? (
                <Link to={a.link} className="text-sm flex-1 min-w-0 hover:underline">{a.title}</Link>
              ) : (
                <span className="text-sm flex-1 min-w-0">{a.title}</span>
              )}
              {canEdit ? (
                <button className="btn text-xs" onClick={() => ack(a.key)}>知道了</button>
              ) : null}
            </div>
            {a.detail ? <p className="text-xs text-text-muted break-words">{a.detail}</p> : null}
          </li>
        ))}
      </ul>
      {alerts.length > SHOWN ? (
        <button className="text-xs text-primary hover:underline" onClick={() => setAll((v) => !v)}>
          {all ? "收起" : `还有 ${alerts.length - SHOWN} 条，展开`}
        </button>
      ) : null}
    </section>
  );
}
