import { useSearchParams, Link } from "react-router-dom";
import { BarChart3, Gauge } from "lucide-react";
import { loadPlatforms } from "@/api/platforms";
import { ErrorState } from "@/components/ErrorState";
import { UsageAnalytics } from "@/components/UsageAnalytics";
import { VideoAnalytics } from "@/components/VideoAnalytics";
import { useAuthStore } from "@/lib/auth-store";
import { usePageResource } from "@/lib/use-page-resource";
import { cn } from "@/lib/utils";

const TABS = [{ id: "usage", label: "用量与耗时", icon: Gauge }, { id: "video", label: "视频数据", icon: BarChart3 }];

/** The bilibili account decides whether there is anything to show; the data itself loads inside VideoAnalytics. */
function VideoData() {
  const { data, error, reload } = usePageResource(loadPlatforms);
  const role = useAuthStore(s => s.user?.role);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-sm text-text-muted">正在读取…</p>;
  if (!data.account.connected) {
    return <section className="card p-8 text-center space-y-3"><p className="text-sm text-text-muted">先在平台管理里连接哔哩哔哩账号，才能采集视频数据。</p><Link className="btn btn-primary text-xs inline-flex" to="/platforms?platform=bilibili&tab=settings">去连接账号</Link></section>;
  }
  return <VideoAnalytics key={data.account.uid} isAdmin={role === "admin"} canEdit={role === "admin" || role === "editor"} />;
}

export function AnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "video" ? "video" : "usage";
  const change = (value: string) => setParams(previous => { const next = new URLSearchParams(previous); next.set("tab", value); return next; });
  return <div className="space-y-5 max-w-6xl">
    <header><h1 className="text-xl font-semibold tracking-tight">数据分析</h1>
      <p className="text-xs text-text-muted mt-1">一条视频从调研到成片的耗时、模型和 token，以及发布到哔哩哔哩之后的表现。</p></header>
    <div role="tablist" className="flex gap-1 border-b border-border">{TABS.map(item =>
      <button key={item.id} role="tab" aria-selected={tab === item.id} className={cn("px-3 py-2 text-sm flex gap-2 items-center border-b-2", tab === item.id ? "text-accent border-accent" : "text-text-muted border-transparent")} onClick={() => change(item.id)}><item.icon className="w-4 h-4" />{item.label}</button>)}</div>
    {tab === "usage" ? <UsageAnalytics /> : <VideoData />}
  </div>;
}
