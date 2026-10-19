import { useEffect, useState } from "react";

import {
  addPublication,
  deletePublication,
  getPublishPack,
  recheckVideoScript,
  type PublishPack,
  type Publication,
  type Recheck,
} from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";

// 发布包：标题、简介（含章节和参考来源）、标签、封面文字，每项一个复制按钮。只生成建议，不发布。
export function PublishPanel({
  scriptId,
  version,
  refreshKey,
  canEdit = false,
  recheckEnabled = false,
  publications = [],
  rechecks = [],
  onChanged,
}: {
  scriptId: string;
  version?: number;
  refreshKey?: string;
  canEdit?: boolean;
  recheckEnabled?: boolean;
  publications?: Publication[];
  rechecks?: Recheck[];
  onChanged?: () => void;
}) {
  const toast = useToast();
  const [pack, setPack] = useState<PublishPack | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const running = rechecks.some((r) => r.status === "RUNNING");
  const latestRecheck = rechecks.find((r) => r.status !== "RUNNING") ?? null;

  // 复核在后台跑，几分钟；跑着的时候每隔几秒让上层刷新一次
  useEffect(() => {
    if (!running || !onChanged) return;
    const timer = setInterval(onChanged, 5000);
    return () => clearInterval(timer);
  }, [running, onChanged]);

  useEffect(() => {
    let alive = true;
    getPublishPack(scriptId, version)
      .then((p) => alive && (setPack(p), setError(null)))
      .catch((e) => alive && setError(errorMessage(e, "生成发布包失败")));
    return () => {
      alive = false;
    };
  }, [scriptId, version, refreshKey, rechecks.length, running, publications.length]);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`已复制${what}`);
    } catch {
      toast.error("没能复制", "浏览器不允许访问剪贴板，请手动选中文字");
    }
  }

  async function recheck() {
    setBusy(true);
    try {
      await recheckVideoScript(scriptId);
      toast.success("已开始复核", "联网检索通常要几分钟，结果会显示在这里");
      onChanged?.();
    } catch (e) {
      toast.error("没能开始复核", errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function record() {
    setBusy(true);
    try {
      await addPublication(scriptId, { url: link.trim(), ...(day ? { published_on: day } : {}) });
      setLink("");
      setDay("");
      toast.success("已记录发布");
      onChanged?.();
    } catch (e) {
      toast.error("没能记录", errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function forget(id: string) {
    try {
      await deletePublication(scriptId, id);
      onChanged?.();
    } catch (e) {
      toast.error("没能删除", errorMessage(e));
    }
  }

  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (!pack) return null;

  const row = (label: string, text: string, what: string, multiline = false) => (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted flex-1">{label}</span>
        <button className="btn text-xs" onClick={() => copy(text, what)}>
          复制
        </button>
      </div>
      {multiline ? (
        <pre className="whitespace-pre-wrap break-words text-sm rounded border border-border p-2 font-sans">{text}</pre>
      ) : (
        <p className="text-sm rounded border border-border p-2 break-words">{text}</p>
      )}
    </div>
  );

  return (
    <section className="card space-y-3" aria-label="发布包">
      <div className="flex items-center gap-2">
        <h2 className="font-medium flex-1">发布包</h2>
        <span className="text-xs text-text-muted">v{pack.version} · 只是建议，不会发布到任何平台</span>
      </div>
      {pack.warnings.map((w) => (
        <p key={w} className="text-xs text-warning">
          {w}
        </p>
      ))}
      {row(`标题（${pack.title.length} 字）`, pack.title, "标题")}
      {row(`简介（${pack.description.length} 字）`, pack.description, "简介", true)}
      {pack.chapter_note ? <p className="text-xs text-text-muted">{pack.chapter_note}</p> : null}
      {pack.tags.length ? row("标签", pack.tags.join(" "), "标签") : null}
      {row("封面文字", `${pack.cover.headline}（角标：${pack.cover.badge}）`, "封面文字")}

      {recheckEnabled ? (
        <div className="space-y-1 border-t border-border pt-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted flex-1">
              发布前复核（联网检索稿子里的事实自写稿以来有没有变化，只提醒，不改稿）
            </span>
            {canEdit ? (
              <button className="btn text-xs" disabled={busy || running} onClick={recheck}>
                {running ? "复核中..." : "发布前复核"}
              </button>
            ) : null}
          </div>
          {latestRecheck?.status === "DONE" ? (
            <div className="text-sm rounded border border-border p-2 space-y-1">
              <p>
                复核结论：<strong>{latestRecheck.verdict}</strong>
                <span className="text-xs text-text-muted"> · {latestRecheck.finished_at?.slice(0, 16).replace("T", " ")}</span>
              </p>
              <pre className="whitespace-pre-wrap break-words text-xs font-sans text-text-muted">{latestRecheck.summary}</pre>
            </div>
          ) : latestRecheck?.status === "FAILED" ? (
            <p className="text-xs text-danger">上次复核没有完成：{latestRecheck.error}</p>
          ) : null}
        </div>
      ) : null}

      <div className="space-y-2 border-t border-border pt-3">
        <span className="text-xs text-text-muted">发布记录（发布后把链接记在这里；不会自动发布或抓取数据）</span>
        {publications.map((p) => (
          <div key={p.id} className="flex items-center gap-2 text-sm">
            <span className="text-xs text-text-muted">{p.published_on}</span>
            <a href={p.url} target="_blank" rel="noreferrer" className="text-accent hover:underline break-all flex-1">
              {p.url}
            </a>
            {p.version ? <span className="text-xs text-text-muted">v{p.version}</span> : null}
            {canEdit ? (
              <button className="text-xs text-text-muted hover:text-danger" onClick={() => forget(p.id)}>
                删除
              </button>
            ) : null}
          </div>
        ))}
        {canEdit ? (
          <div className="flex flex-wrap gap-2">
            <input
              className="input flex-1 min-w-[16rem]"
              placeholder="B 站视频链接，例如 https://www.bilibili.com/video/BV..."
              value={link}
              onChange={(e) => setLink(e.target.value)}
            />
            <input className="input w-auto" type="date" aria-label="发布日期" value={day} onChange={(e) => setDay(e.target.value)} />
            <button className="btn btn-primary text-xs" disabled={busy || link.trim().length < 8} onClick={record}>
              记录发布
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
