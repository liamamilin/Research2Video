import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarClock, ExternalLink, Lightbulb, Plus, RefreshCw } from "lucide-react";

import { createTheme, discoverThemes, dismissTheme, generateThemes, getThemeHistory, getThemeWorkbench, markThemeUsed, sendThemeFeedback, setThemeSchedule, startTheme, updateTheme,
  type Theme, type ThemeCategory, type ThemeInput, type ThemeWorkbenchData } from "@/api/themes";
import { ErrorState } from "@/components/ErrorState";
import { TimeStamp } from "@/components/TimeStamp";
import { ThemeUsage } from "@/components/ThemeUsage";
import { ThemeProductionSettings } from "@/components/ThemeProductionSettings";
import { errorMessage, useToast } from "@/lib/toast";

const PLATFORM_NAMES: Record<string, string> = { bilibili: "B站", zhihu: "知乎", youtube: "YouTube", xiaohongshu: "小红书", reddit: "Reddit" };
const METRIC_NAMES: Record<string, string> = { view: "播放", like: "点赞", reply: "评论", favorite: "收藏", coin: "投币", share: "分享" };
const EMPTY: ThemeInput = { title: "", category: "", audience: "", angle: "", notes: "" };
const FEEDBACK_REASONS: Record<string, string> = { not_now: "暂时不做", not_relevant: "不适合目标观众", too_generic: "题目太宽泛", hard_to_show: "不容易用画面讲清楚" };

function HeatSummary({ theme }: { theme: Theme }) {
  const ranking = theme.ranking;
  const primary = ranking?.primary;
  if (!primary) return <p className="text-xs text-text-muted">内容线索 · 热度待采集</p>;
  const source = theme.signals.find((s) => s.id === primary.signal_id);
  return <div className="rounded-md border border-border p-3 space-y-2 text-xs" aria-label="主题热度依据">
    <div className="flex flex-wrap justify-between gap-2"><span className={primary.status === "stale" ? "text-warning" : "text-accent"}>{primary.status === "stale" ? "历史计数" : "B站实际计数"}</span>
      <span title={ranking.formula}>综合推荐 {ranking.score.toFixed(1)}</span></div>
    {source && <a className="text-text-muted hover:underline block truncate" href={source.url} target="_blank" rel="noreferrer">热度参考作品：{source.title}</a>}
    <div className="flex flex-wrap gap-4">{["view", "like", "favorite"].filter((key) => typeof primary.metrics[key] === "number").map((key) =>
      <span key={key}>{METRIC_NAMES[key]} <strong className="font-medium tabular-nums">{primary.metrics[key].toLocaleString()}</strong></span>)}</div>
    {primary.observed_at && <TimeStamp value={new Date(primary.observed_at * 1000).toISOString()} label="指标采集" staleDays={Infinity} />}
    {source?.video_published_at && <TimeStamp value={new Date(source.video_published_at * 1000).toISOString()} label="作品发布" staleDays={Infinity} />}
    {primary.views_per_day != null && <p className="text-text-muted">{primary.speed_kind === "observed_growth" ? "两次观测间日均新增播放" : "发布以来日均播放"}：{primary.views_per_day.toLocaleString()}
      {primary.speed_kind === "lifetime_average" && "（并非近期增量）"}</p>}
    <details className="text-text-muted"><summary className="cursor-pointer">排序依据</summary><p className="mt-2 leading-5">{ranking.formula}。按每类候选选择一条；该分数用于本工作台选题。</p></details>
  </div>;
}

function ThemeForm({ categories, value, saving, onSave, onClose }: {
  categories: ThemeCategory[]; value: Theme | null; saving: boolean;
  onSave: (body: ThemeInput) => Promise<void>; onClose: () => void;
}) {
  const [fields, setFields] = useState<ThemeInput>(value ? {
    title: value.title, category: value.category, audience: value.audience, angle: value.angle, notes: value.notes,
  } : { ...EMPTY, category: categories[0]?.key || "", audience: categories[0]?.audience || "" });
  const field = (key: keyof ThemeInput, text: string) => setFields((old) => ({ ...old, [key]: text }));
  return <form className="card p-4 space-y-3" aria-label={value ? "修改主题" : "自己写主题"}
    onSubmit={(event) => { event.preventDefault(); void onSave(fields); }}>
    <h3 className="text-sm font-medium">{value ? "修改主题和角度" : "从一个想讲清楚的问题开始"}</h3>
    <label className="block text-xs space-y-1">主题标题<input required minLength={4} maxLength={180} className="input" value={fields.title}
      placeholder="例如：ChatGPT 免费版用了一个月，应该升级 Plus 吗？" onChange={(e) => field("title", e.target.value)} /></label>
    <div className="grid sm:grid-cols-2 gap-3">
      <label className="text-xs space-y-1">所属栏目<select className="input" value={fields.category} disabled={!!value}
        onChange={(e) => { const c = categories.find((row) => row.key === e.target.value); setFields((old) => ({ ...old, category: e.target.value, audience: c?.audience || old.audience })); }}>
        {categories.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
      </select></label>
      <label className="text-xs space-y-1">目标观众<input className="input" maxLength={1000} value={fields.audience} onChange={(e) => field("audience", e.target.value)} /></label>
    </div>
    <label className="block text-xs space-y-1">希望展开的角度<input className="input" maxLength={1000} value={fields.angle} onChange={(e) => field("angle", e.target.value)} /></label>
    <label className="block text-xs space-y-1">补充材料<textarea className="input min-h-24" maxLength={20000} value={fields.notes}
      placeholder="自己的经历、具体场景、已知数据或参考链接，选填" onChange={(e) => field("notes", e.target.value)} /></label>
    <div className="flex gap-2"><button className="btn btn-primary text-xs" disabled={saving || fields.title.trim().length < 4 || !fields.category}>{saving ? "保存中…" : "保存主题"}</button>
      <button type="button" className="btn text-xs" disabled={saving} onClick={onClose}>取消</button></div>
  </form>;
}

function status(theme: Theme) {
  if (theme.automatic_production?.state === 'skipped' || theme.status === 'skipped') return '已跳过';
  if (theme.automatic_production?.state === 'queued' || theme.status === 'queued') return '已排队，等待制作';
  if (theme.renders?.some((r) => r.status === "COMPLETED")) return "视频已完成";
  if (theme.renders?.some((r) => !["FAILED", "CANCELLED"].includes(r.status))) return "视频制作中";
  if (theme.renders?.length) return "视频需要重试";
  if (theme.script_status === "RESEARCHING") return "正在研究";
  if (theme.script_status === "WRITING") return "正在写稿";
  if (theme.script_status === "FAILED") return "需要重试";
  if (theme.status === "attention") return "制作需要处理";
  if (theme.script_id) return theme.auto_video ? "文稿已生成 · 自动制作" : "文稿已生成";
  if (theme.used_at) return "已使用";
  if (theme.origin === "generated") return theme.inspiration?.basis === "matrix" ? "矩阵灵感" : "参考题型推荐";
  return theme.origin === "manual" ? "自己命题" : "推荐主题";
}

export function ThemeWorkbench({ matrix, canEdit }: { matrix: string; canEdit: boolean }) {
  const toast = useToast();
  const [data, setData] = useState<ThemeWorkbenchData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Theme | null | undefined>(undefined);
  const [category, setCategory] = useState("");
  const [day, setDay] = useState<string | null>(null);
  const [minutes, setMinutes] = useState(4);
  const [sort, setSort] = useState("score");
  const [view, setView] = useState("recommendations");
  const [libraryCategory, setLibraryCategory] = useState("");
  useEffect(() => { setLibraryCategory(""); }, [matrix]);
  const [ideasOpen, setIdeasOpen] = useState(false);
  const [brief, setBrief] = useState("");
  const [ideaCategory, setIdeaCategory] = useState("");
  const [patternId, setPatternId] = useState("");
  const [history, setHistory] = useState<Theme[]>([]);
  const [nextHistory, setNextHistory] = useState<number | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [at, setAt] = useState("09:00");
  const [mode, setMode] = useState("ideas");
  const [direction, setDirection] = useState("");
  const [feedbackId, setFeedbackId] = useState("");
  const [feedbackReason, setFeedbackReason] = useState("not_now");
  const [feedbackNote, setFeedbackNote] = useState("");
  const [platforms, setPlatforms] = useState<string[]>(["bilibili", "zhihu", "youtube"]);
  const flight = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true);
    try {
      const next = await getThemeWorkbench(matrix, controller.signal);
      if (!Array.isArray(next.themes) || !Array.isArray(next.categories)) throw new Error("主题数据异常，请重试");
      if (!controller.signal.aborted) { setData(next); setError(""); }
    } catch (e) { if (!controller.signal.aborted) setError(errorMessage(e)); }
    finally { if (flight.current === controller) { flight.current = null; setLoading(false); } }
  }, [matrix]);
  useEffect(() => { void load(); const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 15000);
    return () => { clearInterval(timer); flight.current?.abort(); flight.current = null; }; }, [load]);
  const action = async (key: string, operation: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(key);
    try { await operation(); await load(); }
    catch (e) { toast.error(errorMessage(e)); }
    finally { setBusy(""); }
  };
  const save = async (body: ThemeInput) => {
    await action("save", async () => {
      if (editing) { const { category: unused, ...fields } = body; void unused; await updateTheme(matrix, editing.id, fields); }
      else await createTheme(matrix, body);
      setEditing(undefined);
    });
  };
  const loadHistory = async (offset = 0) => {
    await action("history", async () => {
      const result = await getThemeHistory(matrix, offset);
      setHistory((old) => offset ? [...old, ...result.themes] : result.themes);
      setNextHistory(result.next_offset);
    });
  };
  const selectPattern = (id: string, column: string) => {
    setPatternId(id); setIdeaCategory(column); setIdeasOpen(true); setView("recommendations");
  };
  const beginTheme = (theme: Theme, autoVideo: boolean) => action(theme.id, async () => {
    await startTheme(matrix, theme.id, autoVideo, minutes);
    const result = await getThemeHistory(matrix);
    setHistory(result.themes); setNextHistory(result.next_offset); setView("history");
  });
  useEffect(() => {
    if (view !== "history") return;
    let live = true;
    const timer = window.setInterval(() => {
      if (!document.hidden) void getThemeHistory(matrix).then((result) => {
        if (live) {
          const updates = new Map(result.themes.map((theme) => [theme.id, theme]));
          setHistory((old) => old.map((theme) => updates.get(theme.id) || theme));
        }
      }).catch(() => { /* Keep the last readable production records. */ });
    }, 15000);
    return () => { live = false; clearInterval(timer); };
  }, [matrix, view]);
  const selectedDay = day ?? data?.today ?? "";
  const supportsFeedback = data?.feedback != null;
  const allCategories = [...(data?.categories || []), ...(data?.legacy_categories || [])];
  const canConfigure = supportsFeedback || data?.configuration.mode !== "ideas";
  const shown = (view === "history" ? history : data?.themes || []).filter((theme) => (view === "history" || ((view === "dismissed" ? theme.status === "dismissed" : theme.status !== "dismissed") && !theme.used_at && theme.status !== "used")) && (!category || category === theme.category)
    && (view === "history" || theme.origin === "manual" || theme.script_id || !selectedDay || theme.day === selectedDay)).sort((a, b) => {
      if (view === "history") return (b.used_at || 0) - (a.used_at || 0);
      if (sort === "score" && (a.feedback?.action === "interested") !== (b.feedback?.action === "interested")) return a.feedback?.action === "interested" ? -1 : 1;
      const value = (theme: Theme) => sort === "latest" ? theme.created_at : sort === "score" ? (theme.ranking?.score || 0) : (theme.ranking?.primary?.metrics[sort] || 0);
      return value(b) - value(a) || b.created_at - a.created_at;
    });
  const latest = data?.runs[0];
  const days = [...new Set((data?.themes || []).map((t) => t.day).filter(Boolean))].sort().reverse();
  return <section aria-label="主题推荐工作台" className="space-y-4">
    <div className="flex flex-wrap gap-3 items-start">
      <div className="w-full sm:w-auto sm:flex-1"><h2 className="text-lg font-semibold flex items-center gap-2"><Lightbulb className="h-5 w-5 shrink-0" />每天找一些值得讲的问题</h2>
        <p className="text-xs text-text-muted mt-1">参考已积累的题目与题型，按栏目推荐新问题。也可以发现平台线索，或自己写题。</p></div>
      <button className="btn text-xs" disabled={loading} onClick={load}><RefreshCw className="h-3.5 w-3.5" />刷新</button>
      {canEdit && <><button className="btn btn-primary text-xs" disabled={!data || !!busy || data.ideas_running} onClick={() => { setIdeasOpen(!ideasOpen); setView("recommendations"); }}>
        {data?.ideas_running ? "正在生成题目…" : "帮我出一批题"}</button>
        <button className="btn text-xs" disabled={!!busy || data?.discovery_running} onClick={() => action("discover", () => discoverThemes(matrix))}>
        {data?.discovery_running || busy === "discover" ? "正在发现主题…" : "联网补充参考"}</button>
        <button className="btn text-xs" disabled={!data} onClick={() => { setEditing(null); setView("recommendations"); }}><Plus className="h-3.5 w-3.5" />自己写主题</button></>}
    </div>
    {error && <ErrorState error={error} onRetry={load} compact />}
    {!data && !error && <p role="status" className="text-sm text-text-muted">正在读取主题…</p>}
    {data && <>
      {data.automatic_production ? <ThemeProductionSettings
        key={matrix + JSON.stringify({ ...data.automatic_production, daily_used: undefined })}
        matrix={matrix} config={data.automatic_production} canEdit={canEdit} onSaved={() => void load()} /> : null}
      <nav className="flex flex-wrap gap-2 text-xs" aria-label="主题工作区">
        <button className={`btn ${view === "recommendations" ? "btn-primary" : ""}`} onClick={() => setView("recommendations")}>待选主题</button>
        <button className={`btn ${view === "library" ? "btn-primary" : ""}`} onClick={() => setView("library")}>参考库 · {data.library?.examples.length || 0} 个例题</button>
        <button className={`btn ${view === "history" ? "btn-primary" : ""}`} disabled={!!busy} onClick={() => { setView("history"); void loadHistory(); }}>使用记录 · {data.used_count || 0}</button>
        <button className={`btn ${view === "dismissed" ? "btn-primary" : ""}`} onClick={() => setView("dismissed")}>已忽略</button>
      </nav>
      {ideasOpen && canEdit && <form className="card p-4 space-y-3 text-xs" aria-label="参考题型出题" onSubmit={(event) => {
        event.preventDefault(); void action("generate", async () => { await generateThemes(matrix, brief, ideaCategory, patternId); setIdeasOpen(false); setDay(null); setCategory(""); setView("recommendations"); });
      }}>
        <h3 className="font-medium text-sm">根据矩阵和参考库，帮你想到具体题目</h3>
        <p className="text-text-muted">留空也能生成。每类最多一条；已推荐和已使用的问题会避开，题型可以继续参考。</p>
        {data.configuration.direction && <p className="text-text-muted">长期方向：{data.configuration.direction}。本次方向留空时沿用。</p>}
        {!!data.feedback?.topics && <p className="text-text-muted">后续出题会参考已记录的 {data.feedback.topics} 个主题选择，仍保持栏目多样性。</p>}
        <label className="block space-y-1">补充出题方向（选填）<textarea className="input min-h-20" maxLength={4000} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="例如：面向用了免费版一段时间的职场用户，多讲办公任务和升级决策。" /></label>
        <div className="grid sm:grid-cols-2 gap-3">
          <label className="space-y-1">出题栏目<select className="input" value={ideaCategory} onChange={(e) => { setIdeaCategory(e.target.value); setPatternId(""); }}><option value="">各栏目分别推荐</option>{(patternId ? allCategories : data.categories).map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}</select></label>
          <label className="space-y-1">参考题型<select className="input" value={patternId} onChange={(e) => {
            setPatternId(e.target.value); const found = data.library?.patterns.find((p) => p.id === e.target.value);
            if (found) setIdeaCategory(found.category);
            else if (data.legacy_categories?.some((c) => c.key === ideaCategory)) setIdeaCategory("");
          }}>
            <option value="">自动选择或从例题提炼</option>{(data.library?.patterns || []).filter((p) => !ideaCategory || p.category === ideaCategory).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        </div>
        <div className="flex gap-2"><button className="btn btn-primary" disabled={!!busy || data.ideas_running}>{busy === "generate" ? "正在启动…" : "生成候选题目"}</button><button type="button" className="btn" onClick={() => setIdeasOpen(false)}>取消</button></div>
      </form>}
      {data.library?.runs[0] && <p role="status" className="text-xs text-text-muted">上次出题：{data.ideas_running ? "正在参考题型生成" : data.library.runs[0].status === "success" ? `新增 ${data.library.runs[0].result.recommended || 0} 个候选题目` : "尚未完成"}
        {data.library.runs[0].error && <span className="text-warning ml-2">{data.library.runs[0].error}</span>}</p>}
      {view === "library" ? <div className="space-y-4">
        <p className="text-xs text-text-muted">参考题目按栏目长期保留；题型记录提问格式和讲述结构。使用过具体问题后，仍可参考同一题型出新题。</p>
        <label className="block text-xs space-y-1">参考库分类<select className="input sm:w-auto" aria-label="参考库分类" value={libraryCategory} onChange={(e) => setLibraryCategory(e.target.value)}><option value="">全部分类</option>{allCategories.map((c) => <option key={c.key} value={c.key}>{c.name}{c.legacy ? " · 早期分类" : ""}</option>)}</select></label>
        <div className="grid xl:grid-cols-2 gap-4">{(data.library?.patterns || []).filter((p) => !libraryCategory || p.category === libraryCategory).map((pattern) => <article className="card p-4 space-y-3" key={pattern.id} aria-label={`题型 ${pattern.name}`}>
          <p className="text-xs text-text-muted">{allCategories.find((c) => c.key === pattern.category)?.name || pattern.category}</p><h3 className="font-medium">{pattern.name}</h3>
          <p className="text-sm leading-6">{pattern.title_pattern}</p><ol className="list-decimal pl-5 text-xs text-text-muted space-y-1">{pattern.outline.map((step, index) => <li key={index}>{step}</li>)}</ol>
          <div className="text-xs space-y-2">{(data.library?.examples || []).filter((r) => pattern.example_ids.includes(r.id)).map((r) => <a className="text-accent block hover:underline" href={r.url} target="_blank" rel="noreferrer" key={r.id}>{r.title}<ExternalLink className="inline h-3 w-3 ml-1" /></a>)}</div>
          {canEdit && <button className="btn text-xs" onClick={() => selectPattern(pattern.id, pattern.category)}>参考这个题型</button>}
        </article>)}</div>
        {allCategories.filter((c) => !libraryCategory || c.key === libraryCategory).map((c) => { const refs = (data.library?.examples || []).filter((r) => r.category === c.key); return refs.length ? <details key={c.key} className="card p-4 text-xs">
          <summary className="cursor-pointer">{c.name} · {refs.length} 个原始参考题</summary><ul className="mt-3 space-y-3">{refs.map((r) => <li key={r.id}><a className="text-accent hover:underline" href={r.url} target="_blank" rel="noreferrer">{r.title}</a><p className="text-text-muted mt-1">{r.summary}</p></li>)}</ul>
        </details> : null; })}
        {!data.library?.examples.length && <p className="text-sm text-text-muted">参考库还没有例题。可以先发现一轮平台线索，也可以直接根据矩阵定位出题。</p>}
      </div> : <>
      <div className="card p-3 text-xs flex flex-wrap gap-3 items-center">
        <CalendarClock className="h-4 w-4" /><span>{data.schedule?.enabled ? `每天 ${data.configuration.time} 推荐 · 北京时间` : "每日推荐未开启"}</span>
        <span className="text-text-muted">{data.configuration.mode === "ideas" ? "参考库出题" : "联网发现"}</span>
        {data.schedule?.enabled && data.schedule.next_run && <TimeStamp value={data.schedule.next_run} label="下次" staleDays={Infinity} />}
        {data.schedule?.enabled && !data.scheduler.online && <span className="text-warning">{data.scheduler.detail}</span>}
        {canEdit && <button className="text-accent hover:underline ml-auto disabled:opacity-50" disabled={!canConfigure}
          title={canConfigure ? undefined : "推荐设置更新中，请稍后重试"} onClick={() => {
          setScheduleOpen(!scheduleOpen); setAt(data.configuration.time); setPlatforms(data.configuration.platforms);
          setMode(data.configuration.mode || "ideas"); setDirection(data.configuration.direction || "");
        }}>设置每日推荐</button>}
      </div>
      {scheduleOpen && canEdit && <div className="card p-4 space-y-3 text-xs" aria-label="每日推荐设置">
        {supportsFeedback && <><label className="block space-y-1">每日推荐方式<select className="input" value={mode} onChange={(e) => setMode(e.target.value)}><option value="ideas">参考库出题 · 不额外搜索</option><option value="discovery">联网发现 · 收集平台线索</option></select></label>
        <label className="block space-y-1">长期出题方向<textarea className="input min-h-20" maxLength={4000} value={direction} onChange={(e) => setDirection(e.target.value)} placeholder="例如：面向职场新人，多给可演示的具体办公任务，各栏目保持多样。" /></label></>}
        <label className="flex items-center gap-2">每天的时间<input aria-label="每天的时间" className="input w-32" type="time" value={at} onChange={(e) => setAt(e.target.value)} />北京时间</label>
        {mode === "discovery" && <div className="flex flex-wrap gap-4">{Object.entries(PLATFORM_NAMES).map(([key, name]) => <label key={key} className="flex items-center gap-1.5">
          <input type="checkbox" checked={platforms.includes(key)} onChange={(e) => setPlatforms((old) => e.target.checked ? [...old, key] : old.filter((p) => p !== key))} />{name}
        </label>)}</div>}
        <div className="flex gap-2"><button className="btn btn-primary text-xs" disabled={!!busy || (mode === "discovery" && !platforms.length)} onClick={() => action("schedule", async () => {
          await setThemeSchedule(matrix, true, at, platforms, supportsFeedback ? mode : undefined, supportsFeedback ? direction : undefined); setScheduleOpen(false);
        })}>保存并开启</button>{data.schedule?.enabled && <button className="btn text-xs" disabled={!!busy} onClick={() => action("schedule", async () => {
          await setThemeSchedule(matrix, false, at, platforms, supportsFeedback ? mode : undefined, supportsFeedback ? direction : undefined); setScheduleOpen(false);
        })}>暂停推荐</button>}</div>
      </div>}
      {editing !== undefined && canEdit && <ThemeForm key={editing?.id || "new"} categories={editing ? allCategories : data.categories} value={editing} saving={busy === "save"} onSave={save} onClose={() => setEditing(undefined)} />}
      <div className="flex flex-wrap gap-2 items-center text-xs">
        <select className="input w-auto" aria-label="筛选主题栏目" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">全部栏目</option>{allCategories.map((c) => <option key={c.key} value={c.key}>{c.name}{c.legacy ? " · 早期分类" : ""}</option>)}</select>
        {view !== "history" && <><select className="input w-auto" aria-label="推荐日期" value={selectedDay} onChange={(e) => setDay(e.target.value)}>
          <option value={data.today}>今天</option>{days.filter((d) => d !== data.today).map((d) => <option key={d} value={d}>{d}</option>)}<option value="">全部日期</option>
        </select>
        <select className="input w-auto" aria-label="主题排序" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="score">综合推荐</option><option value="view">参考作品播放量</option><option value="like">参考作品点赞量</option><option value="favorite">参考作品收藏量</option><option value="latest">最新推荐</option>
        </select>
        {canEdit && <label className="ml-auto flex items-center gap-2" title="用于指导写作，成片按完整文稿和实测旁白时长生成">参考时长<select className="input w-auto" aria-label="主题视频时长" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>{[2, 3, 4, 5, 6, 8].map((n) => <option key={n} value={n}>{n} 分钟</option>)}</select></label>}</>}
      </div>
      {view !== "history" && latest && <div className="text-xs text-text-muted" role="status">上次发现：{latest.status === "running" ? "正在收集与筛选" : latest.status === "success" ? `推荐 ${latest.result.recommended ?? 0} 个主题，收集 ${latest.result.signals ?? 0} 条线索` : "未完成"}
        {latest.result.metrics && <span className="ml-2">{latest.result.metrics.measured_signals} 条线索取得真实指标，覆盖 {latest.result.metrics.unique_videos} 个作品。</span>}
        {latest.result.metrics?.error && <span className="text-warning ml-2">{latest.result.metrics.error} <Link className="text-accent underline" to="/platforms">检查B站连接</Link></span>}
        {latest.error && <span className="text-warning ml-2">{latest.error}</span>}
        {!!Object.keys(latest.result.failures || {}).length && <span className="ml-2">部分栏目本轮未取得资料，已有推荐保留。</span>}
      </div>}
      <div className="grid xl:grid-cols-2 gap-4">{shown.map((theme) => <article key={theme.id} className="card p-4 space-y-3 min-w-0 break-words" aria-label={`主题 ${theme.title}`}>
        <div className="flex justify-between gap-2 text-xs text-text-muted"><span>{allCategories.find((c) => c.key === theme.category)?.name || theme.category}</span><span>{status(theme)}</span></div>
        <h3 className="font-medium leading-6">{theme.title}</h3>
        {theme.reason && <p className="text-sm leading-6">{theme.reason}</p>}
        {theme.angle && <p className="text-xs text-text-muted leading-5">讲述角度：{theme.angle}</p>}
        {theme.audience && <p className="text-xs text-text-muted">面向：{theme.audience}</p>}
        {theme.inspiration?.pattern_name && <details className="text-xs text-text-muted"><summary className="cursor-pointer">参考题型：{theme.inspiration.pattern_name}</summary><p className="mt-2">{theme.inspiration.title_pattern}</p><ol className="list-decimal pl-5 mt-2 space-y-1">{theme.inspiration.outline.map((step, index) => <li key={index}>{step}</li>)}</ol></details>}
        {theme.used_at ? <TimeStamp value={new Date(theme.used_at * 1000).toISOString()} label="已记录使用" staleDays={Infinity} /> : null}
        {(theme.origin === "discovered" || theme.signals.some((s) => Object.keys(s.metrics).length)) && <HeatSummary theme={theme} />}
        {!!theme.signals.length && <details className="text-xs"><summary className="cursor-pointer text-text-muted">{theme.origin === "generated" ? `查看 ${theme.signals.length} 个参考例题` : `查看 ${theme.signals.length} 条推荐线索`}</summary>
          <ul className="space-y-3 mt-3">{theme.signals.map((signal) => <li key={signal.id} className="space-y-1">
            <a href={signal.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{signal.platform} · {signal.title}<ExternalLink className="inline h-3 w-3 ml-1" /></a>
            <p className="text-text-muted">{signal.signal_kind === "reference_example" ? "用于借鉴提问方式和讲述结构" : Object.keys(signal.metrics).length ? Object.entries(signal.metrics).map(([key, n]) => `${METRIC_NAMES[key] || key} ${n.toLocaleString()}`).join(" · ") : "公开内容线索，未取得互动指标"}</p>
            {signal.metrics_status === "stale" && <p className="text-warning">历史计数 · {signal.metrics_error}</p>}
            {!Object.keys(signal.metrics).length && signal.metrics_error && <p className="text-warning">{signal.metrics_error}</p>}
            <TimeStamp value={new Date((signal.metrics_observed_at || signal.collected_at) * 1000).toISOString()} label={signal.metrics_observed_at ? "指标采集" : "线索采集"} staleDays={Infinity} />
          </li>)}</ul>
        </details>}
        {theme.automatic_production?.note ? <p className="text-xs text-text-muted">{theme.automatic_production.note}</p> : null}
        {theme.error && theme.automatic_production?.state !== 'skipped' && theme.status !== 'skipped' && <p className="text-xs text-warning">{theme.error}</p>}
        {theme.feedback?.action === "interested" && <p className="text-xs text-accent">已标记感兴趣</p>}
        {theme.feedback?.action === "dismissed" && <p className="text-xs text-text-muted">忽略原因：{FEEDBACK_REASONS[theme.feedback.reason || "not_now"]}{theme.feedback.note ? ` · ${theme.feedback.note}` : ""}</p>}
        {supportsFeedback && (theme.origin === "generated" || theme.script_id) && <ThemeUsage matrix={matrix} id={theme.id} />}
        <div className="flex flex-wrap gap-2 border-t border-border pt-3">
          {theme.automatic_production?.state === 'skipped' && theme.automatic_production.script_id ? <Link className="btn text-xs" to={`/video/${theme.automatic_production.script_id}`}>查看已有内容</Link> : null}
          {theme.script_id ? <Link className="btn btn-primary text-xs" to={`/video/${theme.script_id}`}>查看文稿与视频</Link> : canEdit && !theme.used_at && theme.automatic_production?.state !== 'queued' && theme.status !== 'queued' && <>
            <button className="btn btn-primary text-xs" disabled={!!busy} onClick={() => beginTheme(theme, true)}>{busy === theme.id ? "正在启动…" : "研究并制作视频"}</button>
            <button className="btn text-xs" disabled={!!busy} onClick={() => beginTheme(theme, false)}>先研究写稿</button>
            <button className="text-xs text-accent px-1" onClick={() => setEditing(theme)}>改角度</button>
            <button className="text-xs text-text-muted px-1" disabled={!!busy} onClick={() => action("used", () => markThemeUsed(matrix, theme.id))}>标记已用</button>
            {supportsFeedback && <button className="text-xs text-accent" disabled={!!busy} onClick={() => action("feedback", () => sendThemeFeedback(matrix, theme.id, theme.feedback?.action === "interested" ? "clear" : "interested"))}>{theme.feedback?.action === "interested" ? "取消感兴趣" : "感兴趣"}</button>}
            {theme.status === "dismissed" ? supportsFeedback && <button className="text-xs text-accent" disabled={!!busy} onClick={() => action("feedback", () => sendThemeFeedback(matrix, theme.id, "clear"))}>恢复待选</button> : <button className="text-xs text-text-muted ml-auto" disabled={!!busy} onClick={() => {
              if (!supportsFeedback) { void action(theme.id, () => dismissTheme(matrix, theme.id)); return; }
              setFeedbackId(theme.id); setFeedbackReason("not_now"); setFeedbackNote("");
            }}>暂不感兴趣</button>}
          </>}
          {canEdit && theme.inspiration?.pattern_id && <button className="text-xs text-accent" onClick={() => selectPattern(theme.inspiration!.pattern_id, theme.category)}>沿用题型出新题</button>}
          {canEdit && !!theme.used_at && !theme.inspiration?.pattern_id && <button className="text-xs text-accent" onClick={() => {
            const pattern = data.library?.patterns.find((p) => p.category === theme.category); selectPattern(pattern?.id || "", theme.category);
          }}>参考同类题型</button>}
        </div>
        {feedbackId === theme.id && supportsFeedback && <form aria-label="忽略主题原因" className="space-y-2 text-xs" onSubmit={(e) => { e.preventDefault(); void action("feedback", async () => { await sendThemeFeedback(matrix, theme.id, "dismissed", feedbackReason, feedbackNote); setFeedbackId(""); }); }}>
          <label className="block space-y-1">忽略原因<select className="input" value={feedbackReason} onChange={(e) => setFeedbackReason(e.target.value)}>{Object.entries(FEEDBACK_REASONS).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
          <label className="block space-y-1">补充说明（选填）<input className="input" maxLength={1000} value={feedbackNote} onChange={(e) => setFeedbackNote(e.target.value)} /></label>
          <p className="text-text-muted">暂时不做不会降低该题型的偏好。</p><div className="flex gap-2"><button className="btn text-xs" disabled={!!busy}>保存忽略</button><button type="button" className="btn text-xs" onClick={() => setFeedbackId("")}>取消</button></div>
        </form>}
      </article>)}</div>
      {view === "history" && nextHistory != null && <button className="btn text-xs" disabled={!!busy} onClick={() => loadHistory(nextHistory)}>更多使用记录</button>}
      {!shown.length && <div className="card p-8 text-center text-sm text-text-muted">{view === "history" ? "还没有使用记录。开始研究制作，或将已经用过的主题标记已用。" : "当前没有合适的主题。可以让系统帮你出题、发现平台线索，或写下自己的问题。"}</div>}
      </>}
    </>}
  </section>;
}
