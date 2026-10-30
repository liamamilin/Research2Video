import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { FilterBar, matchesText, useFilters, type Facet } from "@/components/FilterBar";
import { ErrorState } from "@/components/ErrorState";
import { SelectionSkipLinks, skipLabel } from "@/components/SelectionSkip";
import { RefreshCw } from "lucide-react";
import { TimeStamp } from "@/components/TimeStamp";
import {
  addVideoTopic, createVideoScript, dismissTopic, getDiscovery, getTopicBrowser, listVideoTopics, renderVideoScript, startDiscovery,
  type CellCoverage, type DiscoveryStatus, type TopicBrowser, type TopicListing, type TopicScript, type VideoCell, type VideoTopic,
} from "@/api/video";
import { useAuthStore } from "@/lib/auth-store";
import { cellOf, scriptsHere, scriptFromTopic, TASK_LABEL, topicTaken, topicTask, videoState, type TopicTask } from "@/lib/topic-progress";
import { useTopicPosition } from "@/lib/use-topic-position";
import { errorMessage } from "@/lib/toast";

const MANUAL = "manual";
const WRITING_LABEL = {
  RESEARCHING: "研究中", WRITING: "写稿中", PLAN_READY: "方案待确认", DRAFT: "待审稿", APPROVED: "已定稿", FAILED: "失败",
};
const VIDEO_LABEL = { none: "未出片", working: "出片中", done: "已出片", failed: "失败" };
const VIDEO_CLASS = { none: "border-border text-text-muted", working: "border-accent text-accent", done: "border-success text-success", failed: "border-danger text-danger" };
type Preview = { topicId: string; url: string; label: string; error?: boolean };
const FORM_LABEL: Record<string, string> = { auto: "自动选择", information_motion: "信息动效", process_diagram: "流程图解" };

function TopicProgress({ scripts, title, back, preview, show, close, onError }: {
  scripts: TopicScript[]; title: string; back: string; preview: Preview | null;
  show: (url: string, label: string) => void; close: () => void; onError: () => void;
}) {
  const line = (s?: TopicScript, older = false) => {
    const state = videoState(s);
    return <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
      {older ? <span className="text-text-muted">较早关联文稿</span> : null}
      {s?.selection_skip ? <span className="badge border border-border text-text-muted">{skipLabel(s.selection_skip)}</span> : <>
        <span className={`badge border ${s?.status === "FAILED" ? "border-danger text-danger" : "border-border text-text-muted"}`}>写稿：{s ? WRITING_LABEL[s.status] : "未写稿"}</span>
        <span className={`badge border ${VIDEO_CLASS[state]}`}>出片：{VIDEO_LABEL[state]}</span>
      </>}
      {s ? <>
        <Link className="text-accent hover:underline" to={scriptFromTopic(s.id, back)}>{s.selection_skip ? "跳过记录" : older ? "文稿" : "已有文稿"}{s.version ? ` v${s.version}` : ""}</Link>
        {s.archived ? <span className="text-text-muted">已归档</span> : null}
        <TimeStamp value={s.created_at} />
        {older && s.video_url && state === "done" ? <button className="btn text-xs" aria-label={`查看成片：${title}（${s.id}）`} onClick={() => show(s.video_url!, `较早文稿 v${s.version} 成片`)}>查看成片</button> : null}
        {state === "done" && s.render && ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"].includes(s.render.status) ? <span className="text-accent">另一次出片进行中</span> : null}
        {s.previous_video ? <>
          <span className="text-warning">有旧版成片 v{s.previous_video.version}（不计入当前版本）</span>
          <button className="text-accent hover:underline" onClick={() => show(s.previous_video!.url, `旧版 v${s.previous_video!.version} 成片`)}>查看旧版成片</button>
        </> : null}
        {state === "failed" && s.render?.status === "COMPLETED" ? <span className="text-danger">成片文件不可用</span> : null}
      </> : null}
    </div>;
  };
  return <div className="space-y-2 pt-2" aria-label={`${title}的制作进度`}>
    {line(scripts[0])}
    {scripts[0]?.selection_skip ? <SelectionSkipLinks skip={scripts[0].selection_skip} back={back} show={show} /> : null}
    {scripts.length > 1 ? <details className="space-y-2 text-xs text-text-muted"><summary className="cursor-pointer">其他关联文稿（{scripts.length - 1}）</summary>{scripts.slice(1).map(s => <div key={s.id}>{line(s, true)}</div>)}</details> : null}
    {preview ? <div className="rounded-md border border-border p-3 space-y-2">
      <div className="flex items-center justify-between gap-3 text-xs"><span>{preview.label}</span><button className="text-accent hover:underline" onClick={close}>关闭成片</button></div>
      <video key={preview.url} className="w-full max-h-[65vh] rounded bg-black" controls preload="metadata" src={preview.url} aria-label={`${title}：${preview.label}`} onError={onError} />
      {preview.error ? <p className="text-xs text-danger" role="alert">成片暂时无法播放，请刷新选题后重试。</p> : null}
    </div> : null}
  </div>;
}

/** 手动添加一个选题：进选题池，和 Job 产出的选题一样可以写稿、被自动流水线挑（配方的 topic.sources 写了 manual 时）。 */
function AddTopicForm({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [angle, setAngle] = useState("");
  const [audience, setAudience] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) {
    return (
      <button className="btn text-xs" onClick={() => setOpen(true)}>
        手动添加选题
      </button>
    );
  }
  return (
    <form
      className="card p-4 space-y-2 w-full"
      aria-label="手动添加选题"
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return; setBusy(true);
        try {
          await addVideoTopic({ title, angle, audience });
          setTitle("");
          setAngle("");
          setAudience("");
          setOpen(false);
          setError("");
          onAdded();
        } catch (err) {
          setError(err instanceof Error ? err.message : "没加上");
        } finally { setBusy(false); }
      }}
    >
      <input className="input w-full text-sm" placeholder="标题（至少 4 个字）" value={title} onChange={(e) => setTitle(e.target.value)} />
      <input className="input w-full text-sm" placeholder="核心角度（可空）" value={angle} onChange={(e) => setAngle(e.target.value)} />
      <input className="input w-full text-sm" placeholder="目标受众（可空）" value={audience} onChange={(e) => setAudience(e.target.value)} />
      {error ? <p className="text-xs text-danger">{error}</p> : null}
      <div className="flex gap-2">
        <button className="btn btn-primary text-xs" type="submit" disabled={busy || title.trim().length < 4}>
          添加
        </button>
        <button className="btn text-xs" type="button" onClick={() => setOpen(false)}>
          取消
        </button>
      </div>
    </form>
  );
}

type GridProps = { cells: VideoCell[]; coverage: CellCoverage[]; current?: string; onPick: (key: string) => void };

/** The content matrix as a grid: a table per domain (one domain: just the table), a row per type, a column per audience;
 *  a click picks the cell. */
function MatrixGrid({ cells, ...rest }: GridProps) {
  // a recipe with no coverage row is written from a whole report (the Job's page), not from items picked here
  const usable = cells.filter((c) => c.usable && c.parts && rest.coverage.some((r) => r.cell === c.key));
  // fixtures and old catalogs may leave out the domain: they all go into one table
  const domains = [...new Map(usable.map((c) => [c.parts!.domain?.key ?? "", c.parts!.domain?.name ?? ""])).entries()];
  if (!usable.length) return null;
  if (domains.length < 2) return <DomainGrid cells={usable} {...rest} />;
  return (
    <div className="space-y-2">
      {domains.map(([key, name]) => (
        <section key={key} aria-label={`${name}领域`}>
          <h3 className="text-xs text-text-muted mb-1">{name}</h3>
          <DomainGrid cells={usable.filter((c) => (c.parts!.domain?.key ?? "") === key)} domain={name} {...rest} />
        </section>
      ))}
    </div>
  );
}

function DomainGrid({ cells: usable, coverage, current, onPick, domain }: GridProps & { domain?: string }) {
  const types = [...new Map(usable.map((c) => [c.parts!.type.key, c.parts!.type.name])).entries()];
  const audiences = [...new Map(usable.map((c) => [c.parts!.audience.key, c.parts!.audience.name])).entries()];
  return (
    <div className="overflow-x-auto" aria-label={domain ? `${domain}内容矩阵` : "内容矩阵"}>
      <table className="text-xs border-separate border-spacing-1">
        <thead>
          <tr>
            <th />
            {audiences.map(([key, name]) => (
              <th key={key} className="font-normal text-text-muted px-2">{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {types.map(([tkey, tname]) => (
            <tr key={tkey}>
              <th className="font-normal text-text-muted text-left pr-2">{tname}</th>
              {audiences.map(([akey]) => {
                const cell = usable.find((c) => c.parts!.type.key === tkey && c.parts!.audience.key === akey);
                const row = coverage.find((c) => c.cell === cell?.key);
                if (!cell || !row) return <td key={akey} className="px-2 py-1 text-text-muted text-center">—</td>;
                const on = row.cell === current;
                return (
                  <td key={akey}>
                    <button
                      aria-label={`${domain ? `${domain} ` : ""}${tname} ${cell.parts!.audience.name}`}
                      aria-pressed={on}
                      className={`${on ? "btn btn-primary" : "btn"} text-xs w-full`}
                      onClick={() => onPick(row.cell)}
                    >
                      {row.sources.length ? `${row.open} 条候选` : "手选"}
                      {row.status !== "enabled" ? "（实验）" : ""}
                      {row.autopilot && row.sources.length ? " · 自动" : ""}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TopicsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const root = useRef<HTMLDivElement>(null);
  const selected = params.get("cell");
  const chosenRound = params.get("round") ?? "";
  const chosenSource = params.get("source");
  const [catalogError, setCatalogError] = useState("");
  const [catalog, setCatalog] = useState<TopicBrowser | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogReadyTick, setCatalogReadyTick] = useState(-1);
  const [listing, setListing] = useState<{ source: string; requestedRound: string; data: TopicListing } | null>(null);
  const usedBootstrap = useRef<TopicBrowser | null>(null);
  const [error, setError] = useState("");
  const [discovery, setDiscovery] = useState<DiscoveryStatus | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [tick, setTick] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [forms, setForms] = useState<Record<string, string>>({});
  const [busyTopic, setBusyTopic] = useState<string | null>(null);
  const operationLock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [operationError, setOperationError] = useState<{ topicId: string; message: string } | null>(null);
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const cells = catalog?.cells ?? [];
  const coverage = catalog?.coverage ?? [];
  const sources = catalog?.sources ?? [];

  useEffect(() => {
    const controller = new AbortController();
    setCatalogLoading(true);
    getTopicBrowser(controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setCatalog(data);
      setCatalogError("");
      setCatalogReadyTick(tick);
    }).catch(() => {
      if (!controller.signal.aborted) {
        setCatalogError("选题无法读取，请重试");
        setCatalogReadyTick(tick);
      }
    }).finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => controller.abort();
  }, [tick]);

  const current =
    coverage.find((c) => c.cell === selected) ?? coverage.find((c) => c.cell === "flash") ?? coverage.find((c) => c.sources.length) ?? coverage[0];
  // the cell's own sources, then the topics added by hand (any single-topic cell can be written from those)
  // (a roundup is put together from a report's items, a comparison from two subjects typed on the scripts page)
  const tabs = [...(current?.sources ?? []), ...(current?.sources.includes(MANUAL) || !current?.autopilot ? [] : [MANUAL])];
  const source = chosenSource && tabs.includes(chosenSource) ? chosenSource : tabs[0] ?? "";
  const sourceInfo = sources.find((s) => s.name === source);
  const sourceLabel = (name: string) => sources.find((s) => s.name === name)?.label ?? name;
  const topicData = listing?.source === source && listing.requestedRound === chosenRound ? listing.data : null;
  const topics = topicData?.topics ?? null;
  const round = topicData?.round ?? null;
  const rounds = topicData?.rounds ?? [];
  useTopicPosition(root, topics != null);

  function setSelected(key: string) {
    const next = coverage.find(c => c.cell === key);
    const available = [...(next?.sources ?? []), ...(next?.autopilot ? [MANUAL] : [])];
    setParams(prev => {
      const p = new URLSearchParams(prev); p.set("cell", key);
      if (!available.includes(source)) { p.set("source", available[0] ?? ""); p.delete("round"); }
      return p;
    }, { replace: true });
    setPreview(null); setChecked([]);
  }
  function setChosenSource(name: string) {
    setParams(prev => { const p = new URLSearchParams(prev); p.set("source", name); p.delete("round"); return p; }, { replace: true });
    setPreview(null);
  }
  function backToTopic(t: VideoTopic) {
    const p = new URLSearchParams(location.search);
    if (current) p.set("cell", current.cell);
    p.set("source", source);
    if (round) p.set("round", round);
    return `/topics?${p}#${encodeURIComponent(`topic-${t.id}`)}`;
  }
  async function start(t: VideoTopic, script?: TopicScript) {
    if (operationLock.current || !canEdit || !cell?.key) return;
    operationLock.current = true; setBusyTopic(t.id); setOperationError(null);
    try {
      if (script) {
        const made = await renderVideoScript(script.id);
        const video = made.status === "COMPLETED" ? made.files?.find(f => f.name === "video.mp4") : null;
        setListing(prev => prev ? { ...prev, data: { ...prev.data, topics: prev.data.topics.map(item => item.id !== t.id ? item : {
          ...item, scripts: item.scripts?.map(s => s.id !== script.id ? s : { ...s, render_state: video ? "done" : ["FAILED", "CANCELLED", "COMPLETED"].includes(made.status) ? "failed" : "working", render: made, ...(video ? { video_url: video.url } : {}) }),
        }) } } : prev);
      } else {
        const made = await createVideoScript({ column: cell.key, topic_id: t.id, ...(forms[t.id] ? { video_form: forms[t.id] } : {}) });
        if (mounted.current) navigate(scriptFromTopic(made.id, backToTopic(t)));
      }
    } catch (e) { setOperationError({ topicId: t.id, message: errorMessage(e, "操作没有开始，请重试") }); }
    finally { operationLock.current = false; setBusyTopic(null); }
  }

  useEffect(() => {
    setDiscovery(null);
    if (!sourceInfo?.refresh) return;
    let active = true; let pending = false;
    const load = async () => {
      if (!active || pending || document.hidden) return;
      pending = true;
      try { const data = await getDiscovery(source); if (active) setDiscovery(data); } catch { /* Source errors remain in the source catalog. */ }
      finally { pending = false; }
    };
    void load();
    const timer = window.setInterval(load, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [source, sourceInfo?.refresh, tick]);

  useEffect(() => {
    let alive = true; const controller = new AbortController();
    if (!catalog || catalogReadyTick !== tick) return;
    setError("");
    const firstRead = usedBootstrap.current !== catalog;
    usedBootstrap.current = catalog;
    if (firstRead && source === catalog.initial.source && !chosenRound) {
      setListing({ source, requestedRound: chosenRound, data: catalog.initial });
      return;
    }
    if (!source) {
      setListing({ source, requestedRound: chosenRound, data: { round: null, rounds: [], topics: [] } });
      return;
    }
    listVideoTopics(source, chosenRound || undefined, controller.signal)
      .then((data) => alive && (setListing({ source, requestedRound: chosenRound, data }), setError("")))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    return () => {
      alive = false; controller.abort();
    };
  }, [source, chosenRound, tick, catalog, catalogReadyTick]);
  useEffect(() => {
    if (!catalog || catalogReadyTick !== tick || !source) return;
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (document.hidden || pending) return;
      pending = true;
      try {
        const data = await listVideoTopics(source, chosenRound || undefined, controller.signal);
        if (!controller.signal.aborted) { setListing({ source, requestedRound: chosenRound, data }); setError(""); }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "进度刷新失败"); }
      finally { pending = false; }
    };
    const timer = window.setInterval(refresh, 20000);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [source, chosenRound, tick, catalog, catalogReadyTick]);
  useEffect(() => { setChecked([]); }, [source, chosenRound]);

  const filters = useFilters(["task", "written", "render", "score", "age", "hidden"]);
  const { values: f } = filters;
  const cell = cells.find((c) => c.usable && c.key === current?.cell);
  const doneHere = (t: VideoTopic) => topicTaken(t, current?.cell);
  const audienceOf = (key?: string) => cells.find((c) => c.key === key)?.parts?.audience.key;
  const typeName = (key: string) => cells.find((c) => c.key === key)?.parts?.type.name ?? key;
  // the cells this topic fits: the same audience as the picked cell, and a topic source of the same kind
  const fits = coverage.filter(
    (c) => (source === MANUAL ? c.autopilot : c.sources.includes(source)) && audienceOf(c.cell) === audienceOf(current?.cell),
  );
  const todo = (topics ?? []).filter((t) => !t.dismissed && topicTask(t, current?.cell) === "unwritten").length;
  const multi = Boolean(current?.multi);
  const matching = (topics ?? []).filter(
    (t) =>
      (f.hidden === "yes" ? Boolean(t.dismissed) : !t.dismissed) &&
      (!f.age || (f.age === "stale" ? Boolean(t.stale) : !t.stale)) &&
      (!f.written || (f.written === "no" ? !doneHere(t) : doneHere(t))) &&
      (!f.render || videoState(scriptsHere(t, current?.cell)[0]) === f.render) &&
      (!f.score || (t.score ?? 0) >= Number(f.score)) &&
      matchesText(f.q, t.title, t.fields["核心角度"], t.fields["一句话"]),
  );
  const shown = matching.filter(t => !f.task || topicTask(t, current?.cell) === f.task);
  const facets: Facet[] = [
    { key: "written", label: "写稿", options: [{ value: "no", label: "尚无可用文稿任务" }, { value: "yes", label: "已有文稿任务" }] },
    { key: "render", label: "出片", options: [{ value: "done", label: "已出片" }, { value: "none", label: "未出片" }, { value: "working", label: "出片中" }, { value: "failed", label: "出片失败" }] },
    { key: "age", label: "新旧", options: [{ value: "fresh", label: "不算旧" }, { value: "stale", label: "偏旧（事件超过 5 天）" }] },
    { key: "hidden", label: "标记", options: [{ value: "yes", label: "只看标了不写的" }] },
    { key: "score", label: "评分", options: [{ value: "8", label: "8 分以上" }, { value: "7", label: "7 分以上" }] },
  ];

  return (
    <div ref={root} className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1"><h1 className="text-xl font-semibold tracking-tight">选题</h1><p className="text-xs text-text-muted mt-1">选择视频类型，再从对应来源挑选条目。整份报告改编可从矩阵研究任务进入。</p></div><button className="btn text-xs" disabled={catalogLoading} onClick={() => setTick((n) => n + 1)}><RefreshCw className="h-3.5 w-3.5" />刷新选题</button>
      </div>
      {catalogError ? <ErrorState error={catalogError} onRetry={() => setTick((n) => n + 1)} compact /> : null}
      {!catalog && catalogLoading ? <p role="status" className="text-xs text-text-muted">正在加载选题…</p> : null}
      {coverage.length ? <div className="flex items-center gap-3"><label className="text-xs text-text-muted" htmlFor="topic-cell">视频类型</label><select id="topic-cell" className="input w-auto max-w-full text-xs" value={current?.cell || ""} onChange={(e) => setSelected(e.target.value)}>{coverage.map((c) => <option key={c.cell} value={c.cell}>{cells.find((item) => item.key === c.cell)?.label || c.label}</option>)}</select></div> : null}
      <details className="card"><summary className="p-3 text-xs text-text-muted cursor-pointer">按领域与受众选择类型</summary><div className="px-3 pb-3"><MatrixGrid cells={cells} coverage={coverage} current={current?.cell} onPick={setSelected} /></div></details>
      {current?.unknown_sources?.length ? (
        <p className="text-sm text-danger">
          这个格子的配方写了没有 Job 声明的选题来源：{current.unknown_sources.join("、")}。到 <Link className="text-accent hover:underline" to="/video-recipes">文稿配方</Link> 改，或在 Job 的 YAML 里写 topics:。
        </p>
      ) : null}
      {current && !tabs.length ? (
        <p className="text-sm text-text-muted">
          这个格子还没有选题来源，要手填：去 <Link className="text-accent hover:underline" to={`/video?cell=${encodeURIComponent(cell?.id ?? current.cell)}`}>文稿页</Link> 填两个对象和对比维度。
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2" aria-label="选题来源">
        {tabs.map((name) => (
          <button key={name} aria-pressed={name === source} className={`${name === source ? "btn btn-primary" : "btn"} text-xs`} onClick={() => setChosenSource(name)}>
            {name === MANUAL ? "手动添加" : sourceLabel(name)}
          </button>
        ))}
        {canEdit ? <AddTopicForm onAdded={() => (setChosenSource(MANUAL), setTick((n) => n + 1))} /> : null}
      </div>
      {sourceInfo?.error || sourceInfo?.report_error ? (
        <p className="text-sm text-danger">选题来源「{sourceInfo.label}」：{sourceInfo.error || `最新一期 ${sourceInfo.latest ?? ""} ${sourceInfo.report_error}`}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3 text-xs text-text-muted">
        <span hidden={!source}>
          {source === MANUAL ? "手动添加的选题" : `来自「${sourceLabel(source)}」`}
          {round ? ` ${round}` : ""}，当前视频类型待写 {todo} 条。
          {multi ? "" : current?.sources.includes(source) && sources.some((s) => s.name === source && s.format === "flash") ? "想把几条合成一期，点上面的「盘点」格子。" : ""}
        </span>
        {rounds.length > 1 ? (
          <select aria-label="报告日期" className="input w-auto text-xs" value={chosenRound || rounds[0]} onChange={(e) => { setParams(prev => { const p = new URLSearchParams(prev); p.set("round", e.target.value); return p; }, { replace: true }); setPreview(null); }}>
            {rounds.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        ) : null}
        {discovery ? (
          <span className="flex items-center gap-2">
            上次发现 {discovery.report_date ?? "—"}
            {discovery.report_at ? <TimeStamp value={new Date(discovery.report_at * 1000).toISOString()} /> : null}
            {canEdit ? (
              <button
                className="btn text-xs"
                disabled={discovery.running}
                title={discovery.running ? "正在跑，跑完再点" : discovery.searches != null ? `上次用了 ${discovery.searches} 次搜索` : "要花搜索额度"}
                onClick={async () => {
                  const cost = discovery.searches != null ? `上次用了 ${discovery.searches} 次搜索` : "要花搜索额度";
                  if (!window.confirm(`现在跑「${discovery.label}」生成选题，${cost}。继续？`)) return;
                  try {
                    setDiscovery(await startDiscovery(source));
                    setNote("已开始，跑完后刷新这一页");
                  } catch (e) {
                    setNote(e instanceof Error ? e.message : "没有开始");
                  }
                }}
              >
                {discovery.running ? "正在生成…" : "生成今天的选题"}
              </button>
            ) : null}
          </span>
        ) : null}
        {sourceInfo?.job && !sourceInfo.refresh ? (
          <span>
            新选题来自 Job <Link className="text-accent hover:underline" to={`/jobs/${encodeURIComponent(sourceInfo.job)}`}>{sourceInfo.job}</Link>：它跑出新报告，这里就会更新。
          </span>
        ) : null}
        {note ? <span className="text-warning">{note}</span> : null}
      </div>
      {error ? <ErrorState error={error} onRetry={() => setTick((n) => n + 1)} compact /> : !topics && source ? <p role="status" className="text-xs text-text-muted">正在读取选题…</p> : null}
      {topics && topics.length ? <FilterBar facets={facets} filters={filters} shown={shown.length} total={topics.length} placeholder="搜索选题" /> : null}
      {topics?.length ? <div className="flex flex-wrap gap-2" aria-label="待办快捷筛选">
        <button className={`btn text-xs ${!f.task ? "btn-primary" : ""}`} aria-pressed={!f.task} onClick={() => filters.set("task", "")}>全部 {matching.length}</button>
        {(Object.entries(TASK_LABEL) as [TopicTask, string][]).map(([key, label]) => <button key={key} className={`btn text-xs ${f.task === key ? "btn-primary" : ""}`} aria-pressed={f.task === key} onClick={() => filters.set("task", f.task === key ? "" : key)}>{label} {matching.filter(t => topicTask(t, current?.cell) === key).length}</button>)}
      </div> : null}
      {topics && topics.length ? <p className="text-xs text-text-muted">制作进度与出片筛选对应当前视频类型，按最新关联文稿的当前版本判断（优先使用中的文稿）；旧版成片单独提示。</p> : null}
      {topics && topics.length === 0 ? (
        <p className="text-sm text-text-muted">
          {!source ? "" : source === MANUAL ? "还没有手动添加的选题。" : `「${sourceLabel(source)}」还没有能读出选题的报告。`}
        </p>
      ) : null}
      {multi && cell ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-text-muted">选 2–10 条，合成一期盘点；已选 {checked.length} 条。</span>
          {checked.length >= 2 && checked.length <= 10 ? (
            <Link className="btn btn-primary text-xs" to={`/video?cell=${encodeURIComponent(cell.id)}&topics=${encodeURIComponent(checked.join(","))}&topic_source=${encodeURIComponent(source)}${round ? `&topic_round=${encodeURIComponent(round)}` : ""}`}>
              合成一期盘点（{checked.length} 条）
            </Link>
          ) : null}
        </div>
      ) : null}
      {topics && topics.length > 0 && !shown.length ? <p className="text-sm text-text-muted">没有符合筛选的选题</p> : null}
      <ul className="space-y-2">
        {shown.map((t) => {
          const script = scriptsHere(t, current?.cell)[0];
          const task = topicTask(t, current?.cell);
          const back = backToTopic(t);
          const show = (url: string, label: string) => setPreview({ topicId: t.id, url, label });
          const href = script ? scriptFromTopic(script.id, back, task === "rendering" || videoState(script) === "failed" ? "video" : "draft") : "";
          return <li key={t.id} id={`topic-${t.id}`} className="card p-4 flex flex-wrap sm:flex-nowrap items-start gap-3">
            {multi && !doneHere(t) ? (
              <input type="checkbox" aria-label={`选 ${t.code}`} checked={checked.includes(t.id)} onChange={(e) => setChecked(e.target.checked ? [...checked, t.id] : checked.filter((id) => id !== t.id))} />
            ) : null}
            <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-sm">
                {t.title}{" "}
                {fits.map((c) => {
                  const done = (t.scripts ?? []).some((x) => cellOf(x) === c.cell && x.status !== "FAILED");
                  return (
                    <button key={c.cell} className={`badge border mr-1 ${done ? "border-success text-success" : "border-border text-text-muted"} ${c.cell === current?.cell ? "font-semibold" : ""}`} title={done ? "这个格子已写过" : "还没写"} onClick={() => setSelected(c.cell)}>
                      {typeName(c.cell)}{done ? " ✓" : ""}
                    </button>
                  );
                })}
              </p>
              {t.fields["核心角度"] ? <p className="text-xs text-text-muted">角度：{t.fields["核心角度"]}</p> : null}
              <p className="text-xs text-text-muted flex flex-wrap gap-x-3">
                {t.fields["发布日期"] ? <span>事件日期 {t.fields["发布日期"]}</span> : null}
                {t.stale ? <span className="badge border border-warning text-warning" title="事件已超过 5 天；自动流水线排在新一点的后面">偏旧</span> : null}
                {t.round ? <span>{sourceLabel(source)} {t.round}</span> : null}
                {t.score != null ? <span>评分 {t.score}</span> : null}
                {(t.scripts ?? []).filter(s => cellOf(s) !== current?.cell).map((s) => (
                  <span key={s.id}>
                    其他类型：<Link className="text-accent hover:underline" to={scriptFromTopic(s.id, back)}>{typeName(cellOf(s))}文稿</Link>
                  </span>
                ))}
              </p>
              <TopicProgress key={current?.cell} title={t.title} scripts={scriptsHere(t, current?.cell)} back={back}
                preview={preview?.topicId === t.id ? preview : null} show={show} close={() => setPreview(null)}
                onError={() => setPreview(p => p?.topicId === t.id ? { ...p, error: true } : p)} />
              {operationError?.topicId === t.id ? <p role="alert" className="text-xs text-danger">{operationError.message}</p> : null}
              {canEdit && cell?.column && !script && !multi && !t.dismissed ? <details className="text-xs text-text-muted pt-2">
                <summary className="cursor-pointer">写稿设置</summary>
                <div className="pt-2 space-y-2"><p>使用当前选题和视频类型：{cell.label}。已有报告会随选题带入。</p>
                  {(cell.column.allowed_forms?.length ?? 0) > 1 ? <label className="flex items-center gap-2">画面形式<select aria-label={`${t.title}的画面形式`} className="input w-auto text-xs" value={forms[t.id] ?? ""} onChange={e => setForms(prev => ({ ...prev, [t.id]: e.target.value }))}>
                    <option value="">配方默认</option>{cell.column.allowed_forms?.map(form => <option key={form} value={form}>{FORM_LABEL[form] ?? form}</option>)}
                  </select></label> : <p>按配方默认设置写稿。</p>}
                </div>
              </details> : null}
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              {t.dismissed ? (
                <span className="badge border border-border text-text-muted">已标不写</span>
              ) : task === "done" && script?.video_url ? <button className="btn btn-primary text-xs" aria-label={`查看成片：${t.title}`} onClick={() => show(script.video_url!, `当前文稿 v${script.version} 成片`)}>查看成片</button>
                : task === "writing" || task === "rendering" ? <Link className="btn text-xs" aria-label={`查看进度：${t.title}`} to={href}>查看进度</Link>
                : task === "failed" ? <Link className="btn text-xs border-danger/40 text-danger" aria-label={`查看原因：${t.title}`} to={href}>查看原因</Link>
                : task === "skipped" ? null
                : script && (task === "draft" || !canEdit) ? <Link className="btn btn-primary text-xs" aria-label={`继续编辑：${t.title}`} to={href}>{script.status === "PLAN_READY" ? "查看方案" : "继续编辑"}</Link>
                : task === "ready" && script && canEdit ? <button className="btn btn-primary text-xs" aria-label={`生成成片：${t.title}`} disabled={busyTopic != null} onClick={() => void start(t, script)}>{busyTopic === t.id ? "提交中…" : "生成成片"}</button>
                : canEdit && cell && !multi ? <button className="btn btn-primary text-xs" aria-label={`开始写稿：${t.title}`} disabled={busyTopic != null} onClick={() => void start(t)}>{busyTopic === t.id ? "开始中…" : "开始写稿"}</button> : null}
              {doneHere(t) && t.published ? <span className="badge border border-success text-success">已发布</span> : null}
              {canEdit && !doneHere(t) ? (
                <button
                  className="text-xs text-text-muted hover:underline"
                  onClick={async () => {
                    await dismissTopic(t.id, !t.dismissed);
                    setTick((n) => n + 1);
                  }}
                >
                  {t.dismissed ? "恢复" : "不写"}
                </button>
              ) : null}
            </div>
          </li>;
        })}
      </ul>
    </div>
  );
}
