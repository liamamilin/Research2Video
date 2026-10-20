import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getVideoBoard, type BoardAttention, type VideoBoard } from "@/api/video";
import { AutopilotCard } from "@/components/AutopilotCard";
import { CredibilityBadge } from "@/components/CredibilityBadge";
import { TimeStamp } from "@/components/TimeStamp";
import { cn } from "@/lib/utils";

const KIND: Record<BoardAttention["kind"], { label: string; cls: string }> = {
  review: { label: "待审校", cls: "border-warning text-warning" },
  failed: { label: "失败", cls: "border-danger text-danger" },
  render: { label: "待成片", cls: "border-border text-text-muted" },
  render_done: { label: "待发布", cls: "border-success text-success" },
  recheck: { label: "待复核", cls: "border-warning text-warning" },
};

const STAGE_LINK: Record<string, string> = {
  researching: "/video?status=RESEARCHING",
  draft: "/video?status=DRAFT",
  approved: "/publish",
  rendering: "/publish",
  rendered: "/publish",
  published: "/video?status=published",
};

export function HomeBoardPage() {
  const [board, setBoard] = useState<VideoBoard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      getVideoBoard()
        .then((b) => alive && (setBoard(b), setError("")))
        .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">视频看板</h1>
        <Link to="/topics" className="btn text-sm">选题</Link>
        <Link to="/video" className="btn text-sm">全部文稿</Link>
      </div>

      {error ? <p className="text-danger text-sm">{error}</p> : null}
      {!board && !error ? <p className="text-text-muted text-sm">读取中…</p> : null}

      {board ? (
        <>
          <section aria-label="流水线阶段" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            {board.stages.map((stage) => (
              <Link key={stage.key} to={STAGE_LINK[stage.key] ?? "/video"} className="card text-center py-3 hover:bg-bg-hover">
                <div className="text-2xl font-semibold">{stage.count}</div>
                <div className="text-xs text-text-muted">{stage.label}</div>
              </Link>
            ))}
          </section>
          {board.failed ? (
            <p className="text-sm text-danger">另有 {board.failed} 篇失败，列在下面。</p>
          ) : null}

          <AutopilotCard />

          <section className="card space-y-2">
            <h2 className="font-medium text-sm">需要你处理</h2>
            {board.attention.length === 0 ? (
              <p className="text-sm text-text-muted">现在没有要处理的事。草稿不需要你审：流水线会自动定稿、出片，每条的可信度标在旁边，你只需要看片和发布。</p>
            ) : (
              <ul className="divide-y divide-border">
                {board.attention.map((item, i) => (
                  <li key={`${item.script_id}-${item.kind}-${i}`}>
                    <Link to={`/video/${item.script_id}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 hover:bg-bg-hover rounded">
                      <span className={cn("badge border", KIND[item.kind].cls)}>{KIND[item.kind].label}</span>
                      <span className="text-sm font-medium">{item.title || item.script_id.slice(0, 8)}</span>
                      <CredibilityBadge value={item.credibility} />
                      <span className="text-xs text-text-muted">{item.text}</span>
                      <TimeStamp value={item.created_at} label="创建" />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
