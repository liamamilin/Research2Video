import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, ChevronDown, Layers3, Plus, RefreshCw } from "lucide-react";
import { getColumnScripts, listContentColumns, listPresentationRecipes, saveContentColumn,
  type ColumnScript, type ColumnType, type ContentColumn, type ContentColumnInput, type PresentationRecipe } from "@/api/content-columns";
import { listProduction, type ProductionConfig } from "@/api/production";
import { updateMatrix, type Matrix } from "@/api/matrices";
import { ErrorState } from "@/components/ErrorState";
import { TimeStamp } from "@/components/TimeStamp";
import { errorMessage, useToast } from "@/lib/toast";

function RecentScripts({ column }: { column: ContentColumn }) {
  const [data, setData] = useState<{ total: number; items: ColumnScript[] } | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getColumnScripts(column.key, controller.signal).then((next) => { if (!controller.signal.aborted) setData(next); })
      .catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [column.key, retry]);
  if (error) return <ErrorState error={error} compact onRetry={() => { setError(""); setRetry((n) => n + 1); }} />;
  if (!data) return <p role="status" className="text-xs text-text-muted">读取栏目内容…</p>;
  if (!data.items.length) return <p className="text-xs text-text-muted">这个栏目还没有关联文稿。</p>;
  return <div className="space-y-2"><p className="text-xs text-text-muted">最近内容 · 共 {data.total} 篇</p>
    <ul className="divide-y divide-border">{data.items.map((s) => <li key={s.id} className="py-2 text-xs flex gap-3 items-start">
      <Link to={`/video/${s.id}`} className="text-accent hover:underline flex-1">{s.title || "未命名文稿"}</Link>
      <span className="text-text-muted">{s.legacy_association ? "按生产绑定关联" : `v${s.version || 0}`}</span>
    </li>)}</ul></div>;
}

function PresentationRecipes() {
  const [recipes, setRecipes] = useState<PresentationRecipe[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    listPresentationRecipes(controller.signal).then((data) => {
      if (!controller.signal.aborted) { setRecipes(data.recipes); setError(data.reachable ? "" : data.error); }
    }).catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, []);
  return <section className="card p-4 space-y-3" aria-label="共享制作配方">
    <h2 className="text-sm font-medium">共享制作配方</h2>
    <p className="text-xs text-text-muted">同一套画面策略可以用于不同领域。在栏目中选择默认配方，单期按讲稿组合截图、图表、流程等画面。</p>
    {error ? <p role="alert" className="text-xs text-warning">制作引擎暂未连接：{error}</p> : recipes === null ? <p role="status" className="text-xs">读取制作配方…</p>
      : <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{recipes.map((r) => <article key={r.id} className="rounded-lg border border-border p-3 space-y-2">
        <h3 className="text-sm font-medium">{r.name}</h3><p className="text-xs leading-5 text-text-muted">{r.description}</p>
      </article>)}</div>}
  </section>;
}

function ColumnEditor({ matrix, types, column, onSaved, onCancel }: {
  matrix: Matrix; types: ColumnType[]; column?: ContentColumn; onSaved: () => void; onCancel: () => void;
}) {
  const [value, setValue] = useState<ContentColumnInput>(() => ({ name: column?.name || "", matrix: matrix.key,
    type: column?.type || types[0]?.key || "news", audience: column?.audience || matrix.content?.audience || "",
    promise: column?.promise || "", production_ids: column?.production_ids || [],
    render_inheritance: column?.render_inheritance || "content_matrix", render: column?.render || {},
    presentation: column?.presentation || {}, status: column?.status || "planned", notes: column?.notes || "" }));
  const [configs, setConfigs] = useState<ProductionConfig[] | null>(null);
  const [visualsInput, setVisualsInput] = useState(() => (column?.presentation.preferred_visuals || []).join("、"));
  const [recipes, setRecipes] = useState<PresentationRecipe[]>([]);
  const [error, setError] = useState("");
  const [resourceError, setResourceError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newKey] = useState(() => `${matrix.key}_${crypto.randomUUID().slice(0, 8)}`);
  const toast = useToast();
  useEffect(() => {
    const controller = new AbortController();
    listProduction(undefined, controller.signal).then((rows) => { if (!controller.signal.aborted) setConfigs(rows); })
      .catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    listPresentationRecipes(controller.signal).then((data) => { if (!controller.signal.aborted) {
      setRecipes(data.recipes); if (!data.reachable) setResourceError(data.error || "暂时无法读取制作配方");
    } }).catch((e) => { if (!controller.signal.aborted) setResourceError(errorMessage(e)); });
    return () => controller.abort();
  }, []);
  const setRender = (field: "production_recipe" | "video_speed" | "production_mode", next: string) => {
    const render = { ...value.render };
    delete render[field];
    if (next) Object.assign(render, { [field]: field === "video_speed" ? Number(next) : next });
    if (field === "production_mode") {
      if (next === "fast") Object.assign(render, { video_form: "information_cards", visual_style: "editorial_light" });
      else if (next === "standard") Object.assign(render, { video_form: "auto", visual_style: "legacy" });
      else if (value.render.production_mode) { delete render.video_form; delete render.visual_style; }
    }
    setValue({ ...value, render });
  };
  const save = async () => {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await saveContentColumn(column?.key || newKey, { ...value,
        presentation: { ...value.presentation, preferred_visuals: visualsInput.split(/[、，,]/).map((v) => v.trim()).filter(Boolean) },
        status: value.production_ids.length ? "configured" : "planned", expected_revision: column?.revision }, !column);
      toast.success("栏目已保存，之后的新文稿使用本次设置"); onSaved();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  return <section className="card p-4 space-y-4" aria-label={column ? "编辑栏目" : "新增栏目"}>
    <h2 className="font-medium text-sm">{column ? "编辑栏目" : "新增栏目"}</h2>
    <div className="grid gap-3 sm:grid-cols-2 text-xs">
      <label>栏目名称<input className="input mt-1" value={value.name} onChange={(e) => setValue({ ...value, name: e.target.value })} /></label>
      <label>栏目类型<select className="input mt-1" value={value.type} onChange={(e) => setValue({ ...value, type: e.target.value })}>
        {types.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
      </select></label>
      <label className="sm:col-span-2">持续交付什么价值<textarea className="input mt-1" rows={2} value={value.promise} onChange={(e) => setValue({ ...value, promise: e.target.value })} /></label>
      <label className="sm:col-span-2">面向哪些观众<input className="input mt-1" value={value.audience} onChange={(e) => setValue({ ...value, audience: e.target.value })} /></label>
    </div>
    <fieldset className="space-y-2 text-xs"><legend className="font-medium mb-2">供稿与生产绑定</legend>
      <p className="text-text-muted">复用已有研究任务和生产配置。关联栏目会保留原来的自动数量与研究周期。</p>
      {configs === null ? <p>读取生产配置…</p> : <div className="max-h-48 overflow-auto border border-border rounded-lg p-2 space-y-2">
        {configs.map((c) => { const taken = !!c.content_column_id && c.content_column_id !== column?.key; return <label key={c.key} className="flex items-start gap-2">
          <input type="checkbox" disabled={taken} checked={value.production_ids.includes(c.key)} onChange={(e) => setValue({ ...value,
            production_ids: e.target.checked ? [...value.production_ids, c.key] : value.production_ids.filter((id) => id !== c.key) })} />
          <span>{c.name || c.key}<span className="text-text-muted ml-2">{taken ? `属于 ${c.content_column_name}` : c.job}</span></span>
        </label>; })}</div>}
    </fieldset>
    <fieldset className="space-y-3 text-xs"><legend className="font-medium mb-2">默认制作方案</legend>
      <div className="grid sm:grid-cols-2 gap-3">
        <label>默认值来源<select className="input mt-1" value={value.render_inheritance} onChange={(e) => setValue({ ...value, render_inheritance: e.target.value as ContentColumn["render_inheritance"] })}>
          <option value="source_compatible">沿用原来源任务</option><option value="content_matrix">继承栏目所属领域</option>
        </select></label>
        <label>制作配方<select className="input mt-1" value={value.render.production_recipe || ""} onChange={(e) => setRender("production_recipe", e.target.value)}>
          <option value="">继承当前制作配方</option>
          {value.render.production_recipe && !recipes.some((r) => r.id === value.render.production_recipe) ? <option value={value.render.production_recipe}>{value.render.production_recipe}</option> : null}
          {recipes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select></label>
        <label>制作档位<select className="input mt-1" value={value.render.production_mode || ""} onChange={(e) => setRender("production_mode", e.target.value)}>
          <option value="">继承任务设置</option><option value="fast">快速混合制作</option><option value="standard">标准制作</option>
        </select></label>
        <label>播放倍速<select className="input mt-1" value={String(value.render.video_speed || "")} onChange={(e) => setRender("video_speed", e.target.value)}>
          <option value="">继承任务设置</option>{[0.5, 1, 1.25, 1.5, 2].map((s) => <option key={s} value={s}>{s} 倍</option>)}
        </select></label>
      </div>
      <p className="text-text-muted">制作配方组织画面与节奏，同一期可以混用截图、图表、流程和价格卡。任务的显式选择仍可覆盖栏目默认值。</p>
      <label className="block">画面偏好<input aria-label="画面偏好" className="input mt-1" value={visualsInput} onChange={(e) => setVisualsInput(e.target.value)} />
        <span className="block text-text-muted mt-1">用顿号分隔，例如：真实截图、性能图、价格卡。实际画面按讲稿和已有素材安排。</span></label>
      <label className="block">画面讲述方式<textarea aria-label="画面讲述方式" className="input mt-1" rows={3}
        value={value.presentation.strategy || ""} onChange={(e) => setValue({ ...value,
          presentation: { ...value.presentation, strategy: e.target.value } })} />
        <span className="block text-text-muted mt-1">说明怎样引导观众看重点，例如先展示页面，再放大相关区域；比较数据随旁白逐步出现。</span></label>
      {resourceError ? <p className="text-warning">制作配方目录暂未连接：{resourceError}。已有选择会保留。</p> : null}
    </fieldset>
    <label className="block text-xs">栏目创作方向<textarea aria-label="栏目创作方向" className="input mt-1" rows={4} value={value.notes} onChange={(e) => setValue({ ...value, notes: e.target.value })} />
      <span className="block text-text-muted mt-1">作为本栏目的写稿目标，说明选材重点、要讲清的问题和讲述方式；留空时沿用文稿配方。</span></label>
    {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    <div className="flex gap-2"><button className="btn btn-primary text-xs" disabled={busy || !value.name.trim() || !value.promise.trim()} onClick={save}>{busy ? "保存中…" : "保存栏目"}</button>
      <button className="btn text-xs" disabled={busy} onClick={onCancel}>取消</button></div>
  </section>;
}

export function ContentColumns({ matrix, canEdit }: { matrix: Matrix; canEdit: boolean }) {
  const [data, setData] = useState<{ types: ColumnType[]; columns: ContentColumn[] } | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [expanded, setExpanded] = useState("");
  const [retry, setRetry] = useState(0);
  const [showRecipes, setShowRecipes] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    listContentColumns(matrix.key, controller.signal).then((value) => {
      if (!Array.isArray(value.columns) || !Array.isArray(value.types)) throw new Error("栏目数据格式异常");
      if (!controller.signal.aborted) { setData(value); setError(""); }
    }).catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [matrix.key, retry]);
  const refresh = useCallback(() => setRetry((n) => n + 1), []);
  if (error) return <ErrorState error={error} onRetry={refresh} />;
  if (!data) return <p className="card p-6 text-sm text-text-muted" role="status">读取领域栏目…</p>;
  const configured = data.columns.filter((c) => c.bindings.length && !c.errors.length);
  const names = new Map(data.types.map((t) => [t.key, t.name]));
  return <section className="space-y-4" aria-label="领域栏目">
    <div className="card p-4 space-y-3"><div className="flex justify-between gap-3"><div><h2 className="font-medium text-sm">持续提供什么价值</h2>
      <p className="text-xs text-text-muted mt-2">{matrix.content?.audience || "在栏目中设置目标观众"}</p></div><Layers3 className="h-5 w-5 text-accent shrink-0" /></div>
      <p className="text-xs">{data.columns.length} 个栏目 · {configured.length} 个已关联来源 · {data.columns.reduce((n, c) => n + c.auto_bindings, 0)} 个绑定自动制作</p>
      <div className="flex gap-2 flex-wrap">{data.types.map((t) => <span className="rounded bg-bg-hover px-2 py-1 text-xs text-text-muted" key={t.key}>{t.name}</span>)}</div>
    </div>
    <div className="flex items-center justify-between"><p className="text-xs text-text-muted">栏目决定交付目标，制作配方决定画面组合。</p>
      <div className="flex gap-2"><button className="btn text-xs" aria-label="刷新栏目" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></button>
        <button className="btn text-xs" aria-expanded={showRecipes} onClick={() => setShowRecipes(!showRecipes)}>制作配方库</button>
        {canEdit ? <button className="btn btn-primary text-xs" onClick={() => setEditing("")}><Plus className="h-3.5 w-3.5" />新增栏目</button> : null}</div></div>
    {showRecipes ? <PresentationRecipes /> : null}
    {editing !== null ? <ColumnEditor key={editing || "new"} matrix={matrix} types={data.types} column={data.columns.find((c) => c.key === editing)}
      onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} /> : null}
    <div className="grid gap-4 xl:grid-cols-2 items-start">{data.columns.map((c) => <article key={c.key} className="card overflow-hidden" aria-label={`栏目 ${c.name}`}>
      <div className="p-4 space-y-3"><div className="flex justify-between gap-3 items-start"><div><span className="text-xs text-accent">{names.get(c.type) || c.type}</span><h3 className="font-medium mt-1">{c.name}</h3></div>
        <span className="text-xs text-text-muted">{c.status === "planned" || !c.bindings.length ? "待配置" : c.auto_bindings ? "已接自动制作" : "手动制作"}</span></div>
        <p className="text-sm leading-6">{c.promise}</p><p className="text-xs text-text-muted">观众：{c.audience || matrix.content?.audience || "待设置"}</p>
        {c.presentation.preferred_visuals?.length ? <p className="text-xs text-text-muted">画面偏好：{c.presentation.preferred_visuals.join("、")}</p> : null}
        {c.presentation.strategy ? <p className="text-xs leading-5 text-text-muted">画面讲述：{c.presentation.strategy}</p> : null}
        {c.notes ? <p className="text-xs leading-5 text-text-muted">创作方向：{c.notes}</p> : null}
        {c.errors.length ? <p className="text-xs text-warning">{c.errors.join("；")}</p> : null}
        {c.bindings.length ? <div className="border-t border-border pt-3 space-y-2">{c.bindings.map((b) => <div key={b.key} className="text-xs flex justify-between gap-3">
          <div className="min-w-0"><p>{b.name || b.key}</p><p className="text-text-muted mt-1">当前写法：{b.label || b.recipe} · {b.auto ? "自动制作" : "手动制作"}</p></div>
          {b.source_matrix && b.job ? <Link className="text-accent shrink-0 hover:underline" to={`/matrices/${b.source_matrix}/video/${encodeURIComponent(b.job)}`}>来源与制作<ArrowUpRight className="inline h-3 w-3" /></Link> : null}
        </div>)}</div> : null}
        <div className="flex justify-between items-center text-xs"><span className="text-text-muted">{c.scripts} 篇关联文稿</span>
          {canEdit ? <button className="text-accent hover:underline" onClick={() => setEditing(c.key)}>编辑栏目</button> : null}</div>
        {c.updated_at ? <div className="text-xs text-text-muted"><TimeStamp value={c.updated_at} label="最近内容" staleDays={Infinity} /></div> : null}
      </div>
      <button className="w-full border-t border-border p-3 text-xs text-left text-accent flex justify-between" aria-expanded={expanded === c.key} onClick={() => setExpanded(expanded === c.key ? "" : c.key)}>最近内容<ChevronDown className="h-3.5 w-3.5" /></button>
      {expanded === c.key ? <div className="p-4 pt-1"><RecentScripts column={c} /></div> : null}
    </article>)}</div>
    {!data.columns.length ? <p className="card p-6 text-sm text-text-muted">尚未配置具名栏目。可以从六种共享类型建立栏目，再关联现有生产配置。</p> : null}
  </section>;
}

export function MatrixContentSettings({ matrix, canEdit, onSaved }: { matrix: Matrix; canEdit: boolean; onSaved: (m: Matrix) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(matrix.content?.name || matrix.name);
  const [audience, setAudience] = useState(matrix.content?.audience || "");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    setBusy(true);
    try { onSaved(await updateMatrix(matrix.key, { name: matrix.name, domain: matrix.domain, keywords: matrix.keywords,
      content: { ...matrix.content, name, audience, role: matrix.content?.role || "domain" } })); setEditing(false); toast.success("领域定位已保存"); }
    catch (e) { toast.error(errorMessage(e)); } finally { setBusy(false); }
  };
  return <section className="card p-4 space-y-3" aria-label="领域定位"><h2 className="font-medium text-sm">领域与观众</h2>
    {editing ? <div className="text-xs space-y-3"><label className="block">领域显示名称<input className="input mt-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="block">面向哪些观众<input className="input mt-1" value={audience} onChange={(e) => setAudience(e.target.value)} /></label>
      <div className="flex gap-2"><button className="btn btn-primary text-xs" disabled={busy || !name.trim()} onClick={save}>保存定位</button><button className="btn text-xs" disabled={busy} onClick={() => setEditing(false)}>取消</button></div></div>
      : <><p className="text-sm">{matrix.content?.name || matrix.name}</p><p className="text-xs text-text-muted">{matrix.content?.audience || "目标观众尚未设置"}</p>
        {canEdit ? <button className="btn text-xs" onClick={() => { setName(matrix.content?.name || matrix.name); setAudience(matrix.content?.audience || ""); setEditing(true); }}>编辑领域定位</button> : null}</>}
  </section>;
}
