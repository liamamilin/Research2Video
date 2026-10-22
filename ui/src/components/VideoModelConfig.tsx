import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { recallModel, rememberModel } from "@/lib/modelMemory";
import { isOffered, ProviderModels } from "@/components/ProviderModels";
import { CheckCircle2, Plug, RotateCcw, Save, XCircle } from "lucide-react";

export interface ProviderPreset {
  id: string;
  label: string;
  base_url: string;
  api_key_env: string;
  models: string[];
  free_models: string[];
  note: string;
}

export interface SecretInfo {
  env: string;
  configured: boolean;
}

interface VideoModel {
  label: string;
  base_url: string;
  model: string;
  key_env: string;
  source: string;
  needs_key: boolean;
  key_ready: boolean;
  error: string;
}

interface Form {
  base_url: string;
  model: string;
  key_env: string;
}

const CUSTOM = "__custom__";

function hostOf(url: string): string {
  try {
    return new URL(url.includes("//") ? url : `//${url}`, "http://x").hostname.toLowerCase();
  } catch {
    return "";
  }
}

// The video model: which service writes a video's storyboard inside AutoVideo. Same provider list as the AI model above,
// but the choice is saved in AutoVideo (through /api/video/model) and applies to videos rendered from now on.
// See spec/llm-providers.md.
export function VideoModelConfig({ providers, secrets }: { providers: ProviderPreset[]; secrets: SecretInfo[] }) {
  const [current, setCurrent] = useState<VideoModel | null>(null);
  const [reachable, setReachable] = useState(true);
  const [unreachableWhy, setUnreachableWhy] = useState("");
  const [form, setForm] = useState<Form>({ base_url: "", model: "", key_env: "" });
  const [busy, setBusy] = useState<"" | "save" | "test" | "reset">("");
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const apply = (config: VideoModel) => {
    setCurrent(config);
    setForm({ base_url: config.base_url, model: config.model, key_env: config.key_env });
  };

  const load = useCallback(() => {
    api<any>("/api/video/model")
      .then((data) => {
        setReachable(Boolean(data.reachable));
        setUnreachableWhy(data.error || "");
        if (data.config) apply(data.config);
      })
      .catch((err) => { setReachable(false); setUnreachableWhy(err.message); });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const active = providers.find((p) => hostOf(p.base_url) === hostOf(form.base_url));
  const selected = active?.id ?? CUSTOM;
  const keySlot = secrets.find((s) => s.env === form.key_env);
  const keyMissing = Boolean(form.key_env) && keySlot !== undefined && !keySlot.configured;
  const dirty = !current || current.base_url !== form.base_url || current.model !== form.model
    || current.key_env !== form.key_env;

  const pick = (id: string) => {
    const preset = providers.find((p) => p.id === id);
    if (!preset) return;
    // another vendor's model id is a 404: put back what this card had with that provider, or leave the model empty
    if (active && isOffered(active.id, form.model)) rememberModel("video", active.id, { model: form.model, fallbacks: [] });
    const back = recallModel("video", id);
    setForm({ base_url: preset.base_url, key_env: preset.api_key_env, model: back?.model ?? "" });
    setNotice(null);
  };

  const run = async (what: "save" | "test" | "reset") => {
    setBusy(what);
    setNotice(null);
    try {
      if (what === "test") {
        const r = await api<any>("/api/video/model/test", { method: "POST", body: form });
        setNotice({ ok: Boolean(r.ok), text: r.ok ? "可用" : `${r.status ?? ""} ${r.message || "失败"}`.trim() });
      } else if (what === "save") {
        const config = await api<VideoModel>("/api/video/model", { method: "PUT", body: form });
        apply(config);
        setNotice({ ok: true, text: "已保存，之后出的片用这个模型（正在出的片不受影响）" });
      } else {
        apply(await api<VideoModel>("/api/video/model", { method: "DELETE" }));
        setNotice({ ok: true, text: "已恢复为 AutoVideo 自带的默认" });
      }
    } catch (err: any) {
      setNotice({ ok: false, text: err.message || "失败" });
    } finally {
      setBusy("");
    }
  };

  const freeModels = active?.free_models ?? [];
  return (
    <div className="card p-4 space-y-3" data-testid="video-model-config">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm font-medium flex-1">视频模型（AutoVideo 出片，写分镜）</div>
        <button className="btn text-xs" disabled={busy !== "" || !reachable} onClick={() => run("test")}>
          <Plug className="w-3 h-3" />
          测试视频模型
        </button>
        {notice && (
          <span className={cn("inline-flex items-center gap-1 text-[11px]", notice.ok ? "text-success" : "text-danger")}>
            {notice.ok ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
            {notice.text}
          </span>
        )}
      </div>
      {!reachable ? (
        <div role="alert" className="text-[11px] text-danger">
          连不上 AutoVideo，无法读取或修改：{unreachableWhy || "未启动"}
        </div>
      ) : current && (
        <div className="text-[11px] text-text-muted">
          现在用：{current.label} · {current.model}（来源：{current.source}）
          {current.error && <span className="text-danger"> · {current.error}</span>}
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1">
          <div className="text-xs text-text-muted">服务商<span className="ml-1 opacity-70">选择后自动填入地址与密钥变量</span></div>
          <select className="input" value={selected} disabled={!reachable} aria-label="视频模型服务商"
            onChange={(e) => e.target.value !== CUSTOM && pick(e.target.value)}>
            {selected === CUSTOM && <option value={CUSTOM}>自定义（当前地址未匹配预设）</option>}
            {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>
        <label className="block space-y-1">
          <div className="text-xs text-text-muted">模型 ID</div>
          <input className="input font-mono text-xs" value={form.model} disabled={!reachable} aria-label="视频模型 ID"
            placeholder="deepseek-v4-flash"
            onChange={(e) => setForm({ ...form, model: e.target.value })} />
        </label>
        {freeModels.length > 0 && (
          <div className="sm:col-span-2 flex flex-wrap items-center gap-1.5 -mt-1">
            <span className="text-[11px] text-text-muted">免费模型：</span>
            {freeModels.map((id) => (
              <button key={id} type="button" className="badge border border-border bg-bg-card font-mono text-[11px] hover:bg-bg-hover"
                onClick={() => setForm({ ...form, model: id })}>
                {id}
              </button>
            ))}
          </div>
        )}
        {active && (
          <div className="sm:col-span-2 -mt-1">
            <ProviderModels providerId={active.id} label={active.label} model={form.model}
              onPick={(id) => setForm({ ...form, model: id })} />
          </div>
        )}
        <label className="block space-y-1">
          <div className="text-xs text-text-muted">接口地址<span className="ml-1 opacity-70">OpenAI 兼容</span></div>
          <input className="input font-mono text-xs" value={form.base_url} disabled={!reachable} aria-label="视频模型接口地址"
            placeholder="https://.../v1"
            onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
        </label>
        <label className="block space-y-1">
          <div className="text-xs text-text-muted">密钥环境变量<span className="ml-1 opacity-70">只填变量名，不填密钥</span></div>
          <input className="input font-mono text-xs" value={form.key_env} disabled={!reachable} aria-label="视频模型密钥变量"
            placeholder="留空 = 不需要密钥"
            onChange={(e) => setForm({ ...form, key_env: e.target.value })} />
        </label>
      </div>
      {active?.note && <div className="text-[11px] text-text-muted">{active.note}</div>}
      {keyMissing && (
        <div role="alert" className="text-[11px] text-danger">
          密钥 {form.key_env} 还没填：请到上面的“API 密钥”卡片填写并保存，AutoVideo 从同一个 .env 里按这个名字读取。
        </div>
      )}
      {current && !current.key_ready && !keyMissing && current.needs_key && (
        <div role="alert" className="text-[11px] text-danger">AutoVideo 里还找不到密钥 {current.key_env}。</div>
      )}
      <div className="flex items-center gap-2">
        <button className="btn btn-primary text-xs" disabled={busy !== "" || !reachable || !dirty || !form.model.trim() || !form.base_url.trim()}
          onClick={() => run("save")}>
          <Save className="w-3 h-3" />
          保存并应用
        </button>
        <button className="btn text-xs" disabled={busy !== "" || !reachable || current?.source !== "设置页"} onClick={() => run("reset")}>
          <RotateCcw className="w-3 h-3" />
          恢复默认
        </button>
        <span className="text-[11px] text-text-muted">保存后新出的片用这个模型；密钥仍在 AREC 的 .env 里，不会发给浏览器或存进 AutoVideo。</span>
      </div>
    </div>
  );
}
