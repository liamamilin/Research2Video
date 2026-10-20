import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  copyVideoRecipe,
  deleteVideoRecipe,
  getVideoRecipe,
  listVideoRecipes,
  saveVideoRecipe,
  validateVideoRecipe,
  type VideoRecipe,
  type VideoRecipeDetail,
} from "@/api/video";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";

const STATUS_LABEL: Record<string, string> = { enabled: "已用", experimental: "试验", blocked: "停用" };
const PIPELINE_LABEL: Record<string, string> = { single: "一次写稿", adapt: "五步改编" };

// 文稿配方：一份 YAML 就是一种文稿的完整做法（调研 → 写稿 → 出片，外加自动流水线从哪挑题），像 Job 一样直接改、复制复用。
export function VideoRecipesPage() {
  const { key } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";

  const [list, setList] = useState<VideoRecipe[]>([]);
  const [detail, setDetail] = useState<VideoRecipeDetail | null>(null);
  const [text, setText] = useState("");
  const [check, setCheck] = useState<{ ok: boolean; error: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listVideoRecipes()
      .then(setList)
      .catch((e) => setError(errorMessage(e, "读取配方失败")));
  }, []);

  useEffect(refresh, [refresh]);

  useEffect(() => {
    if (!key) {
      setDetail(null);
      return;
    }
    setCheck(null);
    getVideoRecipe(key)
      .then((d) => {
        setDetail(d);
        setText(d.content);
        setError(null);
      })
      .catch((e) => setError(errorMessage(e, "读取配方失败")));
  }, [key]);

  useEffect(() => {
    if (!key && list.length) navigate(`/video-recipes/${list[0].key}`, { replace: true });
  }, [key, list, navigate]);

  const dirty = detail !== null && text !== detail.content;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const validate = () =>
    run(async () => {
      const r = await validateVideoRecipe(detail!.key, text);
      setCheck({ ok: r.ok, error: r.error });
    });

  const save = () =>
    run(async () => {
      const saved = await saveVideoRecipe(detail!.key, text, detail!.mtime);
      setDetail(saved);
      setText(saved.content);
      setCheck({ ok: true, error: "" });
      toast.success("已保存；之后新写的文稿按新配方来");
      refresh();
    });

  const copy = () => {
    const name = window.prompt("新配方的文件名（小写字母、数字、下划线、连字符）", `${detail!.key}_copy`);
    if (!name) return;
    run(async () => {
      const made = await copyVideoRecipe(name.trim(), detail!.key);
      refresh();
      navigate(`/video-recipes/${made.key}`);
    });
  };

  const remove = () => {
    if (!window.confirm(`删除配方 ${detail!.key}？文件会先备份到 state/backups。`)) return;
    run(async () => {
      await deleteVideoRecipe(detail!.key);
      refresh();
      navigate("/video-recipes");
    });
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">文稿配方</h1>
        <p className="text-sm text-text-muted">
          每个配方是 config/video_recipes 下的一份 YAML：怎么调研、怎么写稿和审计、出成什么样，以及自动流水线从哪挑题（手动建稿可以用任何题目）。改完先校验再保存；新配方从已有配方复制。
        </p>
      </div>
      {error ? <div className="text-sm text-danger">{error}</div> : null}
      <div className="grid gap-4 md:grid-cols-[18rem_1fr]">
        <nav className="card p-2 space-y-1" aria-label="配方列表">
          {list.map((r) => (
            <button
              key={r.key}
              onClick={() => navigate(`/video-recipes/${r.key}`)}
              className={`w-full text-left rounded px-2 py-1.5 text-sm ${r.key === key ? "bg-surface-2" : "hover:bg-surface-2"}`}
            >
              <div className="flex items-center gap-2">
                <span className="flex-1 truncate">{r.label}</span>
                <span className="text-xs text-text-muted">{r.error ? "有错" : STATUS_LABEL[r.status] ?? r.status}</span>
              </div>
              <div className="text-xs text-text-muted truncate">
                {r.key}
                {r.pipeline ? ` · ${PIPELINE_LABEL[r.pipeline]}` : ""}
                {r.target_minutes ? ` · ${r.target_minutes} 分钟` : ""}
                {` · ${r.scripts} 篇`}
              </div>
            </button>
          ))}
        </nav>
        {detail ? (
          <section className="card space-y-3" aria-label="配方内容">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-medium flex-1">
                {detail.label} <span className="text-xs text-text-muted">{detail.key}.yaml</span>
              </h2>
              <button className="btn" disabled={busy} onClick={validate}>
                校验
              </button>
              {canEdit ? (
                <>
                  <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
                    保存
                  </button>
                  <button className="btn" disabled={busy} onClick={copy}>
                    复制为新配方
                  </button>
                  {detail.scripts === 0 ? (
                    <button className="btn" disabled={busy} onClick={remove}>
                      删除
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>
            {detail.error ? <div className="text-sm text-danger">这份配方现在不能用：{detail.error}</div> : null}
            {check ? (
              <div className={`text-sm ${check.ok ? "text-success" : "text-danger"}`}>
                {check.ok ? "校验通过" : `校验没通过：${check.error}`}
              </div>
            ) : null}
            {detail.scripts > 0 ? (
              <div className="text-xs text-text-muted">
                已有 {detail.scripts} 篇文稿用这个配方。改动只影响之后新写的文稿；不想再用就把 status 改成 blocked（要写 reason）。
              </div>
            ) : null}
            {/* 纯文本框：不依赖外部 CDN（控制台的 CSP 会拦下 Monaco 编辑器） */}
            <textarea
              aria-label="配方 YAML"
              value={text}
              onChange={(e) => setText(e.target.value)}
              readOnly={!canEdit}
              spellCheck={false}
              className="input w-full font-mono text-xs leading-5 min-h-[40rem] whitespace-pre"
            />
          </section>
        ) : null}
      </div>
    </div>
  );
}
