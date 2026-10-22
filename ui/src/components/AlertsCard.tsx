import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ackAutomationAlert, listAutomationAlerts, type AutomationAlert } from "@/api/video";
import { useAuthStore } from "@/lib/auth-store";

/** 一次先列这么多，其余折起来：一屏红字只会被忽略。 */
const SHOWN = 3;

/** 自动化异常：最近 7 天自己跑的东西哪里出了问题。没有就不显示；「知道了」之后不再出现。 */
export function AlertsCard() {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const [alerts, setAlerts] = useState<AutomationAlert[]>([]);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      listAutomationAlerts()
        .then((rows) => alive && (setAlerts(rows), setError("")))
        .catch((e) => alive && setError(e instanceof Error ? e.message : "读取自动化异常失败"));
    load();
    const timer = window.setInterval(load, 60000);
    return () => {
      alive = false;
      window.clearInterval(timer);
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
