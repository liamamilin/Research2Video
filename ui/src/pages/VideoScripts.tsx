import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  createVideoScript,
  listVideoColumns,
  listVideoScripts,
  listVideoTopics,
  type ScriptStatus,
  type VideoColumn,
  type VideoScriptSummary,
  type VideoTopic,
} from "@/api/video";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn, timeAgo } from "@/lib/utils";

export const STATUS_LABEL: Record<ScriptStatus, string> = {
  RESEARCHING: "研究中",
  WRITING: "写稿中",
  DRAFT: "待审稿",
  APPROVED: "已定稿",
  FAILED: "失败",
};

export const STATUS_CLASS: Record<ScriptStatus, string> = {
  RESEARCHING: "bg-accent/15 text-accent border-accent/30",
  WRITING: "bg-accent/15 text-accent border-accent/30",
  DRAFT: "bg-warning/15 text-warning border-warning/30",
  APPROVED: "bg-success/15 text-success border-success/30",
  FAILED: "bg-danger/15 text-danger border-danger/30",
};

const IN_FLIGHT: ScriptStatus[] = ["RESEARCHING", "WRITING"];

export function minutes(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function VideoScriptsPage() {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const toast = useToast();
  const navigate = useNavigate();

  const [scripts, setScripts] = useState<VideoScriptSummary[] | null>(null);
  const [columns, setColumns] = useState<VideoColumn[]>([]);
  const [round, setRound] = useState<string | null>(null);
  const [topics, setTopics] = useState<VideoTopic[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"topics" | "manual">("topics");
  const [busy, setBusy] = useState<string | null>(null);
  const [manual, setManual] = useState({ title: "", angle: "", audience: "" });

  const load = useCallback(async () => {
    try {
      const [s, c, t] = await Promise.all([listVideoScripts(), listVideoColumns(), listVideoTopics()]);
      setScripts(s);
      setColumns(c);
      setRound(t.round);
      setTopics(t.topics);
      setError(null);
    } catch (e) {
      setError(errorMessage(e, "加载视频文稿失败"));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Follow scripts that are still being researched or written.
  const inFlight = scripts?.some((s) => IN_FLIGHT.includes(s.status)) ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => {
      listVideoScripts().then(setScripts).catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [inFlight]);

  const column = columns[0];

  async function create(body: Parameters<typeof createVideoScript>[0], key: string) {
    setBusy(key);
    try {
      const made = await createVideoScript({ column: column?.key, ...body });
      toast.success("已开始研究", "研究通常需要 3–10 分钟，完成后自动写稿");
      navigate(`/video/${made.id}`);
    } catch (e) {
      toast.error("没能开始", errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">视频文稿</h1>
        {column ? (
          <span className="text-xs text-text-muted">
            栏目：{column.name} · 约 {column.target_minutes} 分钟
          </span>
        ) : null}
      </div>

      {canEdit && column ? (
        <section className="card space-y-3">
          <div className="flex items-center gap-2">
            <h2 className="font-medium flex-1">新建「{column.name}」</h2>
            {(["topics", "manual"] as const).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={cn(
                  "badge border cursor-pointer",
                  tab === key ? "bg-accent-solid text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover",
                )}
              >
                {key === "topics" ? "从内容机会选" : "手写选题"}
              </button>
            ))}
          </div>
          <p className="text-xs text-text-muted">{column.description}</p>

          {tab === "topics" ? (
            topics.length ? (
              <div className="space-y-2">
                <p className="text-xs text-text-muted">情报轮次 {round} 的内容机会（P7）</p>
                {topics.map((t) => (
                  <div key={t.id} className="flex items-start gap-3 rounded border border-border p-3">
                    <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
                    <div className="min-w-0 flex-1 space-y-1">
                      <p className="text-sm">{t.title}</p>
                      {t.fields["目标受众"] ? (
                        <p className="text-xs text-text-muted">受众：{t.fields["目标受众"]}</p>
                      ) : null}
                      {t.fields["核心角度"] ? (
                        <p className="text-xs text-text-muted">角度：{t.fields["核心角度"]}</p>
                      ) : null}
                    </div>
                    {t.score != null ? <span className="badge border border-border text-xs">{t.score}/10</span> : null}
                    <button
                      className="btn btn-primary text-xs shrink-0"
                      disabled={busy !== null || t.scripted}
                      onClick={() => create({ topic_id: t.id }, t.id)}
                      title={t.scripted ? "这个选题已经写过" : undefined}
                    >
                      {t.scripted ? "已写过" : busy === t.id ? "开始中..." : "写这一篇"}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <ErrorState compact empty="还没有内容机会报告。跑一轮情报流水线后，这里会列出它推荐的视频选题。" />
            )
          ) : (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                create({ topic: manual }, "manual");
              }}
            >
              <input
                className="input"
                placeholder="选题（必填），例如：crewAI eval --models 的每次运行成本怎么看"
                value={manual.title}
                onChange={(e) => setManual({ ...manual, title: e.target.value })}
              />
              <input
                className="input"
                placeholder="核心角度（可选）"
                value={manual.angle}
                onChange={(e) => setManual({ ...manual, angle: e.target.value })}
              />
              <input
                className="input"
                placeholder="目标受众（可选）"
                value={manual.audience}
                onChange={(e) => setManual({ ...manual, audience: e.target.value })}
              />
              <div>
                <button
                  type="submit"
                  className="btn btn-primary text-xs"
                  disabled={busy !== null || manual.title.trim().length < 4}
                >
                  {busy === "manual" ? "开始中..." : "开始研究"}
                </button>
              </div>
            </form>
          )}
        </section>
      ) : null}

      <section className="card p-0 overflow-hidden">
        {error ? (
          <ErrorState error={error} onRetry={load} />
        ) : scripts === null ? (
          <ErrorState empty="加载中..." />
        ) : scripts.length === 0 ? (
          <ErrorState empty="还没有视频文稿。" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-text-muted border-b border-border">
              <tr>
                <th className="text-left font-normal px-4 py-2">文稿</th>
                <th className="text-left font-normal px-4 py-2">状态</th>
                <th className="text-left font-normal px-4 py-2">时长</th>
                <th className="text-left font-normal px-4 py-2">更新</th>
              </tr>
            </thead>
            <tbody>
              {scripts.map((s) => (
                <tr key={s.id} className="border-b border-border last:border-0 hover:bg-bg-hover">
                  <td className="px-4 py-2">
                    <Link to={`/video/${s.id}`} className="text-accent hover:underline">
                      {s.title}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <span className={cn("badge border", STATUS_CLASS[s.status])}>
                      {STATUS_LABEL[s.status]}
                      {s.status === "WRITING" && s.stage ? ` · ${s.stage}` : ""}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-text-muted">{minutes(s.length_check?.estimated_seconds)}</td>
                  <td className="px-4 py-2 text-text-muted">{timeAgo(s.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
