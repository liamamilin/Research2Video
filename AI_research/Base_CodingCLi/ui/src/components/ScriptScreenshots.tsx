import { useEffect, useRef, useState } from "react";
import {
  captureScreenshot, getScreenshots, registerScreenshot, saveScreenshotSelection,
  type MaterialForm, type MaterialRole, type Placement, type ScreenshotMaterial,
  type ScreenshotMode, type ScreenshotOverview,
} from "@/api/screenshots";
import { errorMessage } from "@/lib/toast";

const MODES: Record<ScreenshotMode, string> = { viewport: "当前首屏", full_page: "完整网页", element: "指定元素", crop: "矩形裁切" };
const ROLES: Record<MaterialRole, string> = { reference: "参考画面", overall: "整体界面", detail: "功能细节", comparison: "对比画面" };

export function ScriptScreenshots({ id, version, canEdit }: { id: string; version: number; canEdit: boolean }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ScreenshotOverview | null>(null);
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<ScreenshotMode>("viewport");
  const [selector, setSelector] = useState("");
  const [clip, setClip] = useState({ x: 0, y: 0, width: 1280, height: 720 });
  const [form, setForm] = useState<MaterialForm>("object_showcase");
  const [materials, setMaterials] = useState<ScreenshotMaterial[]>([]);
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [reload, setReload] = useState(0);
  const loaded = useRef(false);

  useEffect(() => {
    if (!open || loaded.current) return;
    let stopped = false;
    setBusy("load");
    getScreenshots(id, version).then(value => {
      if (stopped) return;
      loaded.current = true;
      setData(value); setMaterials(value.selection.materials); setForm(value.selection.form);
      setUrl(value.sources[0]?.url ?? ""); setProblem(value.problem);
    }).catch(e => { if (!stopped) setProblem(errorMessage(e, "加载截图素材失败")); })
      .finally(() => { if (!stopped) setBusy(""); });
    return () => { stopped = true; };
  }, [id, version, open, reload]);

  const preparing = data?.automatic?.state === "pending" || data?.automatic?.state === "running";
  useEffect(() => {
    if (!open || !preparing || dirty) return;
    let stopped = false;
    const timer = setInterval(() => {
      getScreenshots(id, version).then(value => {
        if (stopped) return;
        setData(value); setMaterials(value.selection.materials); setForm(value.selection.form);
      }).catch(() => undefined);
    }, 3000);
    return () => { stopped = true; clearInterval(timer); };
  }, [id, version, open, preparing, dirty]);

  const changed = () => { setDirty(true); setSaved(false); };
  const update = (assetId: string, values: Partial<ScreenshotMaterial>) => {
    setMaterials(rows => rows.map(row => row.screenshot_id === assetId ? { ...row, ...values } : row)); changed();
  };
  async function capture() {
    setBusy("capture"); setProblem("");
    try {
      const result = await captureScreenshot(id, { version, url, mode,
        ...(mode === "element" ? { selector } : {}), ...(mode === "crop" ? { clip } : {}) });
      setData(value => value ? { ...value, assets: [result.asset, ...value.assets] } : value);
      setProblem(result.problem);
    } catch (e) { setProblem(errorMessage(e, "截图失败")); }
    finally { setBusy(""); }
  }
  async function register(assetId: string) {
    setBusy(assetId); setProblem("");
    try {
      const asset = await registerScreenshot(id, version, assetId);
      setData(value => value ? { ...value, assets: value.assets.map(row => row.id === assetId ? asset : row) } : value);
      if (materials.some(row => row.screenshot_id === assetId)) changed();
    } catch (e) { setProblem(errorMessage(e, "登记失败，截图已保留")); }
    finally { setBusy(""); }
  }
  async function save() {
    setBusy("save"); setProblem("");
    try {
      await saveScreenshotSelection(id, version, { form, materials });
      setDirty(false); setSaved(true);
    } catch (e) { setProblem(errorMessage(e, "保存配图失败")); }
    finally { setBusy(""); }
  }

  return <section className="card p-0 overflow-hidden">
    <button type="button" className="w-full p-3 text-left text-sm font-medium" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      网页截图与段落配图 <span className="text-text-muted font-normal">· v{version} {open ? "收起" : "展开"}</span>
    </button>
    {open ? <div className="p-3 pt-0 space-y-4">
      <p className="text-xs text-text-muted">{data?.automation_enabled ? "标准制作会自动取得来源截图、检查画面并绑定段落，无需在这里逐张操作。也可以手动调整下次制作的配图。" : "从研究来源获取真实页面画面。预览后选择对应段落。"} 截图记录页面当时的内容，不能代替事实核验；新版本会重新计算自动配图。</p>
      {data?.automatic ? <div className="border border-border rounded p-3 text-xs space-y-1" aria-label="自动配图结果">
        <p className="font-medium">{preparing ? "正在自动准备配图…" : "本次制作配图"} · {data.automatic.reason}</p>
        {data.automatic.decisions?.length ? <details><summary className="cursor-pointer text-text-muted">查看各段处理结果</summary><ul className="mt-2 space-y-2">{data.automatic.decisions.map(row => <li key={row.need_id}>
          <p>{row.need_id} · {row.status === "accepted" ? "已配图" : "使用文字画面"} · {row.reason}</p>
          {row.understanding?.summary ? <p className="text-text-muted">图片内容：{row.understanding.summary}</p> : null}
          {row.understanding?.focus_reason ? <p className="text-text-muted">聚焦建议：{row.understanding.focus_reason}；镜头使用页面实测位置强调重点。</p> : null}
        </li>)}</ul></details> : null}
      </div> : null}
      {problem ? <div role="alert" className="text-sm text-warning">{problem}{!data ? <button className="btn text-xs ml-2" onClick={() => setReload(value => value + 1)}>重试加载</button> : null}</div> : null}
      {busy === "load" ? <p className="text-xs text-text-muted">正在加载素材…</p> : null}
      {data && canEdit ? <div className="space-y-2 border border-border rounded p-3">
        <label className="block text-xs">来源网址
          <input className="input mt-1" aria-label="截图来源网址" type="url" maxLength={2048} list={`screenshot-sources-${id}`} value={url} onChange={e => setUrl(e.target.value)} placeholder="选择报告来源，或填写产品、功能、价格页面网址" />
        </label>
        <datalist id={`screenshot-sources-${id}`}>{data.sources.map(source => <option key={source.url} value={source.url}>{source.title}</option>)}</datalist>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">截图范围<select aria-label="截图范围" className="input mt-1" value={mode} onChange={e => setMode(e.target.value as ScreenshotMode)}>{Object.entries(MODES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          {mode === "element" ? <label className="text-xs flex-1">CSS 选择器<input aria-label="截图元素选择器" className="input mt-1" maxLength={500} placeholder="例如 main 或 #pricing" value={selector} onChange={e => setSelector(e.target.value)} /></label> : null}
          <button className="btn" disabled={Boolean(busy) || !url.trim() || (mode === "element" && !selector.trim())} onClick={() => void capture()}>{busy === "capture" ? "正在截取并保存…" : "获取网页截图"}</button>
        </div>
        {mode === "crop" ? <div className="flex flex-wrap gap-2">{(["x", "y", "width", "height"] as const).map(key => <label key={key} className="text-xs">{{ x: "左边距", y: "上边距", width: "宽度", height: "高度" }[key]}<input aria-label={`裁切${key}`} className="input w-24 mt-1" type="number" min={key === "width" || key === "height" ? 128 : 0} max={key === "x" || key === "width" ? 1440 : 900} value={clip[key]} onChange={e => setClip({ ...clip, [key]: Number(e.target.value) })} /></label>)}<p className="w-full text-xs text-text-muted">坐标按 1440×900 首屏计算。过长网页建议截取元素，保证视频中的文字可读。</p></div> : null}
      </div> : null}
      {data && !data.assets.length ? <p className="text-xs text-text-muted">还没有网页截图。</p> : null}
      <div className="space-y-3">{data?.assets.map(asset => {
        const selected = materials.find(row => row.screenshot_id === asset.id);
        return <article key={asset.id} className="border border-border rounded p-3 space-y-2">
          <div className="flex gap-3 flex-wrap sm:flex-nowrap">
            <a href={asset.preview_url} target="_blank" rel="noreferrer" aria-label={`查看原图：${asset.title}`} className="shrink-0"><img src={asset.preview_url} alt={asset.title || "网页截图"} className="w-48 h-28 object-contain bg-bg-card rounded" loading="lazy" /></a>
            <div className="min-w-0 text-xs space-y-1 flex-1"><p className="font-medium">{asset.title || "网页截图"}</p><a className="text-accent break-all" href={asset.final_url} target="_blank" rel="noreferrer">{asset.final_url}</a>
              <p className="text-text-muted">{new Date(asset.captured_at).toLocaleString("zh-CN")} · {asset.width}×{asset.height} · {MODES[asset.mode]}</p>
              {asset.url !== asset.final_url ? <p className="text-text-muted break-all">原来源：{asset.url}</p> : null}
              {asset.selector ? <p>元素：{asset.selector}</p> : null}
              {asset.clip ? <p>裁切：{asset.clip.x}, {asset.clip.y} / {asset.clip.width}×{asset.clip.height}</p> : null}
              {canEdit ? <label className="flex items-center gap-2 pt-1"><input type="checkbox" aria-label={`使用截图：${asset.title}`} checked={Boolean(selected)} disabled={Boolean(busy) || !asset.image_id || (!selected && materials.length >= 12)} onChange={e => {
                setMaterials(rows => e.target.checked ? [...rows, { screenshot_id: asset.id, caption: "", role: "reference", placements: [] }] : rows.filter(row => row.screenshot_id !== asset.id)); changed();
              }} />用于当前版本</label> : <p>{selected ? "此版本已使用" : "此版本未使用"}</p>}
              {canEdit ? <button className="btn text-xs" disabled={Boolean(busy)} onClick={() => void register(asset.id)}>{asset.image_id ? "重新登记到当前引擎" : "重新登记图片"}</button> : null}
            </div>
          </div>
          {selected ? <div className="space-y-2">
            <div className="flex gap-2"><label className="text-xs flex-1">画面说明<input className="input mt-1" aria-label={`画面说明：${asset.title}`} maxLength={80} disabled={!canEdit || Boolean(busy)} value={selected.caption} onChange={e => update(asset.id, { caption: e.target.value })} placeholder="例如：官方价格表，截图于当日，具体费用以官网为准" /></label>
              <label className="text-xs">用途<select className="input mt-1" disabled={!canEdit || Boolean(busy)} value={selected.role} onChange={e => update(asset.id, { role: e.target.value as MaterialRole })}>{Object.entries(ROLES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
            <fieldset className="space-y-1 max-h-52 overflow-auto"><legend className="text-xs mb-1">对应讲稿段落（至少选择一段）</legend>{data.chapters.flatMap((chapter, ci) => chapter.paragraphs.map((text, pi) => {
              const isSelected = selected.placements.some(p => p.chapter_index === ci && p.paragraph_index === pi);
              const placement: Placement = { chapter_index: ci, paragraph_index: pi };
              return <label key={`${ci}-${pi}`} className="flex gap-2 text-xs p-1"><input type="checkbox" disabled={!canEdit || Boolean(busy)} checked={isSelected} onChange={e => update(asset.id, { placements: e.target.checked ? [...selected.placements, placement] : selected.placements.filter(p => p.chapter_index !== ci || p.paragraph_index !== pi) })} /><span><b>{chapter.title} · 第{pi + 1}段</b> {text}</span></label>;
            }))}</fieldset>
          </div> : null}
        </article>;
      })}</div>
      {data && canEdit ? <div className="flex flex-wrap items-center gap-3 text-xs">
        <label>配图形式<select aria-label="段落配图形式" className="input mt-1" disabled={Boolean(busy)} value={form} onChange={e => { setForm(e.target.value as MaterialForm); changed(); }}><option value="object_showcase">对象展示</option><option value="dynamic_collage">动态拼贴</option></select></label>
        <button className="btn" disabled={Boolean(busy) || !dirty || materials.some(row => !row.placements.length)} onClick={() => void save()}>{busy === "save" ? "保存中…" : "确认并保存段落配图"}</button>
        <span className={dirty ? "text-warning" : "text-text-muted"}>{dirty ? "配图更改尚未保存" : saved ? "已保存，下次标准制作用于此版本" : data.selection.origin === "automatic" ? "自动配图已用于本次制作；手动修改后保存仅影响下次制作" : "保存后用于此版本的下次标准制作"}</span>
        <p className="w-full text-text-muted">已选 {materials.length}/12 张。未配图的段落显示文字。快速卡片不支持图片；改稿后自动重算配图，人工选择须重新保存。</p>
      </div> : null}
    </div> : null}
  </section>;
}
