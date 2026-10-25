import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  addPublication,
  deletePublication,
  getPublishPack,
  recheckVideoScript,
  revealVideoRender,
  type Publication,
  type Recheck,
  type VideoRender,
} from "@/api/video";
import { ErrorState } from "@/components/ErrorState";
import { usePageResource } from "@/lib/use-page-resource";
import { errorMessage, useToast } from "@/lib/toast";

const sizeText = (bytes: number | null) => (bytes == null ? "" : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

/** 在这台电脑的访达里显示成片：发布页的成片文件卡和成片与发布列表每一行都用它 */
export function RevealButton({ renderId, className = "btn text-xs" }: { renderId: string; className?: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function open() {
    setBusy(true);
    try {
      const shown = await revealVideoRender(renderId);
      if (shown.opened) toast.success("已在访达中显示成片");
      else toast.error("没能在这台电脑上打开文件夹", `成片在 ${shown.path}`);
    } catch (e) {
      toast.error("没能打开文件夹", errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return <button className={className} disabled={busy} onClick={open}>在访达中显示</button>;
}

// 成片文件：在这台电脑的访达里打开存着成片的文件夹，直接拖去 B 站上传；不在这台电脑上看页面时，复制路径
export function RenderFolder({ render, canEdit = false }: { render: VideoRender; canEdit?: boolean }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");

  async function open() {
    setBusy(true);
    try {
      const shown = await revealVideoRender(render.id);
      setPath(shown.path);
      if (shown.opened) toast.success("已在访达中显示成片");
      else toast.error("没能在这台电脑上打开文件夹", `成片在 ${shown.path}，可以复制路径自己找`);
    } catch (e) {
      toast.error("没能打开文件夹", errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function copyPath() {
    try {
      const shown = path ? { path } : await revealVideoRender(render.id).then((r) => ({ path: r.path }));
      setPath(shown.path);
      await navigator.clipboard.writeText(shown.path);
      toast.success("已复制成片路径");
    } catch (e) {
      toast.error("没能复制路径", errorMessage(e));
    }
  }

  return (
    <section className="card p-4 space-y-2" aria-label="成片文件">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-medium flex-1">成片文件</h2>
        <span className="text-xs text-text-muted">使用文稿 v{render.version}</span>
        {canEdit ? (
          <>
            <button className="btn btn-primary text-xs" disabled={busy} onClick={open}>在访达中显示</button>
            <button className="btn text-xs" onClick={copyPath}>复制路径</button>
          </>
        ) : null}
      </div>
      <ul className="text-xs space-y-1">
        {render.files.filter(f => ["video.mp4", "captions.srt"].includes(f.name)).map((f) => (
          <li key={f.name} className="flex gap-2">
            <a className="text-accent hover:underline" href={f.url} download={f.name}>{f.name}</a>
            <span className="text-text-muted">{sizeText(f.bytes)}</span>
          </li>
        ))}
      </ul>
      {render.files.some(f => !["video.mp4", "captions.srt"].includes(f.name)) ? <details className="text-xs">
        <summary className="cursor-pointer text-text-muted">技术文件与生成记录（{render.files.filter(f => !["video.mp4", "captions.srt"].includes(f.name)).length}）</summary>
        <ul className="mt-2 space-y-1">{render.files.filter(f => !["video.mp4", "captions.srt"].includes(f.name)).map(f => <li key={f.name}><a className="text-accent hover:underline" href={f.url} download={f.name}>{f.name}</a><span className="text-text-muted ml-2">{sizeText(f.bytes)}</span></li>)}</ul>
      </details> : null}
      {path ? <p className="text-xs text-text-muted break-all">{path}</p> : null}
    </section>
  );
}

// 发布素材与统一发布记录；平台投稿在平台管理确认，也保留手动补录。
export function PublishPanel({
  scriptId,
  version,
  renderId,
  refreshKey,
  canEdit = false,
  recheckEnabled = false,
  publications = [],
  rechecks = [],
  onChanged,
}: {
  scriptId: string;
  version?: number;
  renderId?: string;
  refreshKey?: string;
  canEdit?: boolean;
  recheckEnabled?: boolean;
  publications?: Publication[];
  rechecks?: Recheck[];
  onChanged?: () => void;
}) {
  const toast = useToast();
  const loadPack = useCallback((signal: AbortSignal) => getPublishPack(scriptId, version, signal), [scriptId, version]);
  const { data: pack, error, reload } = usePageResource(loadPack);
  const [link, setLink] = useState("");
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const running = rechecks.some((r) => r.status === "RUNNING");
  const latestRecheck = rechecks.find((r) => r.status !== "RUNNING") ?? null;

  // 复核在后台跑，几分钟；跑着的时候每隔几秒让上层刷新一次
  useEffect(() => {
    if (!running || !onChanged) return;
    const timer = setInterval(() => { if (!document.hidden) onChanged(); }, 5000);
    return () => clearInterval(timer);
  }, [running, onChanged]);

  useEffect(() => { void reload(); }, [reload, refreshKey, rechecks.length, running, publications.length]);

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
      await addPublication(scriptId, { url: link.trim(), version: pack?.version ?? version,
        ...(renderId ? { render_id: renderId } : {}), ...(day ? { published_on: day } : {}) });
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

  if (error && !pack) return <ErrorState error={error} onRetry={reload} compact />;
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
    <section className="card p-4 space-y-3" aria-label="发布包">
      {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
      <div className="flex items-center gap-2">
        <h2 className="font-medium flex-1">发布包</h2>
        <span className="text-xs text-text-muted">v{pack.version} · 用于平台投稿，也可复制后手动发布</span>
      </div>
      {pack.warnings.map((w) => (
        <p key={w} className="text-xs text-warning">
          {w}
        </p>
      ))}
      {row(`标题（${pack.title.length} 字）`, pack.title, "标题")}
      {row(`简介（${pack.description.length} 字）`, pack.description, "简介", true)}
      {pack.references.length ? (
        <details className="rounded border border-border p-2 text-sm">
          <summary className="cursor-pointer">资料来源（{pack.references.length}）</summary>
          <ul className="mt-2 space-y-2">
            {pack.references.map((ref) => (
              <li key={ref.url} className="space-y-0.5">
                <a href={ref.url} target="_blank" rel="noreferrer" className="text-accent hover:underline break-words">
                  {ref.title}
                </a>
                <span className="ml-2 text-xs text-text-muted">{ref.cited ? "文稿引用" : "报告补充资料"}{ref.date ? ` · ${ref.date}` : ""}</span>
                <p className="break-all text-xs text-text-muted">{ref.url}</p>
              </li>
            ))}
          </ul>
          <button className="btn mt-2 text-xs" onClick={() => copy(pack.references.map((ref) => `${ref.title}\n${ref.url}`).join("\n\n"), "资料链接")}>
            复制资料链接
          </button>
        </details>
      ) : null}
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
              <div className="md-body text-xs overflow-x-auto">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{latestRecheck.summary}</ReactMarkdown>
              </div>
              {latestRecheck.report_path ? (
                <a
                  className="text-xs text-accent hover:underline"
                  href={`/reports/view?path=${encodeURIComponent(latestRecheck.report_path.replace(/^output\//, ""))}`}
                >
                  查看完整复核报告 →
                </a>
              ) : null}
            </div>
          ) : latestRecheck?.status === "FAILED" ? (
            <p className="text-xs text-danger">上次复核没有完成：{latestRecheck.error}</p>
          ) : null}
        </div>
      ) : null}

      <div className="space-y-2 border-t border-border pt-3">
        <h3 className="text-sm font-medium">发布记录</h3>
        <p className="text-xs text-text-muted">平台投稿确认公开后自动回填链接和发布日期。已在 B 站手动发布的视频，可在这里补录。</p>
        {publications.map((p) => (
          <div key={p.id} className="flex items-center gap-2 text-sm">
            <span className="text-xs text-text-muted">{p.published_on}</span>
            <a href={p.url} target="_blank" rel="noreferrer" className="text-accent hover:underline break-all flex-1">
              {p.url}
            </a>
            {p.version ? <span className="text-xs text-text-muted">v{p.version}</span> : null}
            {p.recorded_by === "platform-manager" ? <span className="badge border border-border text-xs">平台自动回写</span> : null}
            {canEdit ? (
              <button className="text-xs text-text-muted hover:text-danger" onClick={() => forget(p.id)}>
                删除
              </button>
            ) : null}
          </div>
        ))}
        {canEdit ? (
          <div className="space-y-2"><p className="text-xs text-text-muted">手动补录发布记录</p><div className="flex flex-wrap gap-2">
            <input
              className="input flex-1 min-w-[16rem]"
              placeholder="B 站视频链接，例如 https://www.bilibili.com/video/BV..."
              value={link}
              onChange={(e) => setLink(e.target.value)}
            />
            <input className="input w-auto" type="date" aria-label="发布日期" value={day} onChange={(e) => setDay(e.target.value)} />
            <button className="btn text-xs" disabled={busy || link.trim().length < 8} onClick={record}>
              记录发布
            </button>
          </div></div>
        ) : null}
      </div>
    </section>
  );
}
