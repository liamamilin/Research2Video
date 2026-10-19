import { useEffect, useState } from "react";

import { getPublishPack, type PublishPack } from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";

// 发布包：标题、简介（含章节和参考来源）、标签、封面文字，每项一个复制按钮。只生成建议，不发布。
export function PublishPanel({ scriptId, version, refreshKey }: { scriptId: string; version?: number; refreshKey?: string }) {
  const toast = useToast();
  const [pack, setPack] = useState<PublishPack | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getPublishPack(scriptId, version)
      .then((p) => alive && (setPack(p), setError(null)))
      .catch((e) => alive && setError(errorMessage(e, "生成发布包失败")));
    return () => {
      alive = false;
    };
  }, [scriptId, version, refreshKey]);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`已复制${what}`);
    } catch {
      toast.error("没能复制", "浏览器不允许访问剪贴板，请手动选中文字");
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
    </section>
  );
}
