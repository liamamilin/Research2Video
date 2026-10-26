import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/lib/toast";
import { Star, ThumbsUp, UserPlus } from "lucide-react";

type Brand = { id: string; name: string; tagline: string; accent: string; background: string; text: string; intro_seconds?: number; corner_reveal_seconds?: number; cta?: { enabled: boolean; message: string }; outro_seconds: number; assets?: Record<string, { file: string; sha256: string }> };
type Catalog = { active: { enabled: boolean; profile?: string }; profiles: Brand[] };
const ctaMessage = "喜欢这期内容，欢迎";
const demo: Brand = { id: "demo", name: "Demo Studio", tagline: "把知识，变成画面。", accent: "#28775b", background: "#f5f6f2", text: "#172b23", intro_seconds: 0, corner_reveal_seconds: .6, cta: { enabled: true, message: ctaMessage }, outro_seconds: 4 };

export function BrandingSettings() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [draft, setDraft] = useState<Brand>(demo);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async (choose = false) => {
    const data = await api<Catalog>("/api/video-brands"); setCatalog(data);
    if (choose) setDraft(data.profiles.find(p => p.id === data.active.profile) || data.profiles[0] || demo);
  }, []);
  useEffect(() => {
    void load(true).catch(err => setError(errorMessage(err))).finally(() => setBusy(false));
  }, [load]);
  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError(""); setMessage("");
    try { await fn(); await load(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  };
  const save = async () => {
    const { assets: _assets, ...body } = draft;
    void _assets;
    setDraft(await api<Brand>(`/api/video-brands/${draft.id}`, { method: "PUT", body }));
    setMessage("品牌已保存；新任务使用新配置，已有任务保留原快照。");
  };
  const upload = async (kind: string, file?: File) => {
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) throw new Error("每份品牌素材最大 4 MB");
    const encoded = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("无法读取文件"));
      reader.readAsDataURL(file);
    });
    setDraft(await api<Brand>(`/api/video-brands/${draft.id}/assets/${kind}`, { method: "POST", body: { base64: encoded } }));
    setMessage("品牌素材已保存。");
  };
  return <section className="card p-4 space-y-3" aria-label="视频品牌">
    <h2 className="font-semibold text-sm">视频品牌 <span className="text-text-muted text-xs font-normal">开场 · 角标 · 互动引导 · 品牌片尾</span></h2>
      <p className="text-xs text-text-muted">品牌配置和素材保存在本机。先试用 Demo；个人品牌可以之后添加。默认品牌用于新视频，也可在矩阵或栏目制作设置中覆盖。</p>
      {catalog && <div className="flex flex-wrap items-center gap-3 text-sm">
        <span>当前默认：{catalog.active.enabled ? catalog.profiles.find(p => p.id === catalog.active.profile)?.name || catalog.active.profile : "未启用"}</span>
        <button className="btn text-xs" disabled={busy} onClick={() => void action(async () => {
          await api("/api/video-brands/active", { method: "PUT", body: { enabled: false } });
        })}>关闭默认品牌</button>
      </div>}
      <fieldset disabled={busy} className="space-y-3">
        <div className="flex flex-wrap items-center gap-2"><label className="text-sm">编辑品牌<select className="input ml-2" value={catalog?.profiles.some(p => p.id === draft.id) ? draft.id : ""} onChange={e => {
          setDraft(catalog?.profiles.find(p => p.id === e.target.value) || { ...demo, id: "new_brand", name: "新品牌" }); setMessage("");
        }}><option value="">新建品牌</option>{catalog?.profiles.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}</select></label>
          <button className="btn text-xs ml-2" onClick={() => void action(async () => {
            setDraft(await api<Brand>("/api/video-brands/demo/install", { method: "POST" }));
            setMessage("Demo 已就绪，包含 Logo 和示例声标；可试听后设为默认。");
          })}>安装 Demo 示例</button></div>
        <div className="grid sm:grid-cols-2 gap-3">
          <label className="text-xs">品牌编号<input className="input w-full mt-1" value={draft.id} onChange={e => setDraft({ ...draft, id: e.target.value })} pattern="[a-z][a-z0-9_-]*" /></label>
          <label className="text-xs">品牌名称<input className="input w-full mt-1" value={draft.name} maxLength={48} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
          <label className="text-xs sm:col-span-2">片尾标语<input className="input w-full mt-1" value={draft.tagline} maxLength={64} onChange={e => setDraft({ ...draft, tagline: e.target.value })} /></label>
          <label className="text-xs">片头时长（0 为关闭）<select className="input w-full mt-1" value={draft.intro_seconds ?? 0} onChange={e => setDraft({ ...draft, intro_seconds: Number(e.target.value) })}>
            {[0, .5, .8, 1, 1.2, 1.5, 2].map(n => <option key={n} value={n}>{n ? `${n} 秒独立片头` : "直接进入正文（推荐）"}</option>)}
          </select></label>
          <label className="text-xs">片尾时长（3–6 秒）<input className="input w-full mt-1" type="number" min={3} max={6} step={0.1} value={draft.outro_seconds} onChange={e => setDraft({ ...draft, outro_seconds: Number(e.target.value) })} /></label>
        </div>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={Boolean(draft.corner_reveal_seconds)} onChange={e => setDraft({ ...draft, corner_reveal_seconds: e.target.checked ? .6 : 0 })} />开场角标轻柔渐显</label>
        <div className="rounded-lg border border-border p-3 space-y-2">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(draft.cta?.enabled)} onChange={e => setDraft({ ...draft, cta: { message: draft.cta?.message ?? ctaMessage, enabled: e.target.checked } })} />品牌片尾前显示「点赞 · 收藏 · 关注」</label>
          {draft.cta?.enabled && <>
            <label className="block text-xs">互动引导文案<input className="input w-full mt-1" value={draft.cta.message} maxLength={24} onChange={e => setDraft({ ...draft, cta: { enabled: true, message: e.target.value } })} /></label>
            <p className="text-xs text-text-muted">通用图标与短动画占用片尾前 1.5 秒，片尾总时长保持 {draft.outro_seconds} 秒。</p>
          </>}
        </div>
        <div className="flex gap-4">{([['accent', '强调色'], ['background', '底色'], ['text', '文字色']] as const).map(([key, title]) =>
          <label key={key} className="text-xs flex items-center gap-2">{title}<input type="color" value={draft[key]} onChange={e => setDraft({ ...draft, [key]: e.target.value })} /></label>)}</div>
        <div className="rounded-xl p-6 text-center space-y-2" style={{ background: draft.background, color: draft.text }}>
          {draft.cta?.enabled && <div className="pb-5 mb-4 border-b" style={{ borderColor: `${draft.accent}30` }}>
            <div className="text-sm mb-4">{draft.cta.message}</div>
            <div className="flex justify-center gap-10" style={{ color: draft.accent }}>{([[ThumbsUp, "点赞"], [Star, "收藏"], [UserPlus, "关注"]] as const).map(([Icon, label]) => <div className="flex flex-col items-center gap-2 text-sm" key={label}><Icon size={28} /><span>{label}</span></div>)}</div>
          </div>}
          {draft.assets?.logo && <img className="h-14 mx-auto object-contain" alt="品牌 Logo" src={`/api/video-brands/${draft.id}/assets/logo?v=${draft.assets.logo.sha256}`} />}
          <div className="text-2xl font-semibold">{draft.name}</div><div>{draft.tagline}</div>
          <div className="text-xs" style={{ color: draft.accent }}>统一片尾预览 · {draft.outro_seconds} 秒</div>
          <div className="text-xs" style={{ color: draft.accent }}>{draft.intro_seconds ? `${draft.intro_seconds} 秒片头 → ` : "正文直接开场 · "}角标{draft.corner_reveal_seconds ? "渐显" : "显示"} → {draft.cta?.enabled ? "点赞收藏关注 → " : ""}品牌片尾</div>
        </div>
        <div className="flex gap-2"><button className="btn btn-primary" onClick={() => void action(save)}>保存品牌</button>
          <button className="btn" onClick={() => void action(async () => {
            await save(); await api("/api/video-brands/active", { method: "PUT", body: { enabled: true, profile: draft.id } }); setMessage("已设为新视频的默认品牌。");
          })}>保存并设为默认</button></div>
        {catalog?.profiles.some(p => p.id === draft.id) && <div className="space-y-2 border-t border-border pt-3">
          <label className="block text-xs">Logo（PNG，支持透明背景）<input className="block mt-1" type="file" accept="image/png" onChange={e => void action(() => upload("logo", e.target.files?.[0]))} /></label>
          <label className="block text-xs">片尾声标（1–6 秒，16bit PCM WAV）<input className="block mt-1" type="file" accept="audio/wav,.wav" onChange={e => void action(() => upload("sonic", e.target.files?.[0]))} /></label>
          {draft.assets?.sonic && <audio controls preload="none" src={`/api/video-brands/${draft.id}/assets/sonic?v=${draft.assets.sonic.sha256}`} />}
        </div>}
      </fieldset>
      {message && <p role="status" className="text-sm">{message}</p>}
      {error && <div><p role="alert" className="text-sm text-red-600">{error}</p>
        <button className="btn text-xs mt-2" disabled={busy} onClick={() => void action(() => load(true))}>重新加载品牌</button></div>}
  </section>;
}
