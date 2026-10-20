import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FilterBar, matchesText, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import {
  dismissTopic, getDiscovery, listCellCoverage, listVideoCells, listVideoTopics, startDiscovery,
  type CellCoverage, type DiscoveryStatus, type VideoCell, type VideoTopic,
} from "@/api/video";
import { useAuthStore } from "@/lib/auth-store";

type Source = "flash" | "opportunities";

const SOURCE_LABEL: Record<Source, string> = { flash: "快讯条目", opportunities: "内容机会" };

/** The content matrix as a grid: a row per type, a column per audience; a click picks the cell. */
function MatrixGrid({ cells, coverage, current, onPick }: { cells: VideoCell[]; coverage: CellCoverage[]; current?: string; onPick: (key: string) => void }) {
  const usable = cells.filter((c) => c.usable && c.parts);
  const types = [...new Map(usable.map((c) => [c.parts!.type.key, c.parts!.type.name])).entries()];
  const audiences = [...new Map(usable.map((c) => [c.parts!.audience.key, c.parts!.audience.name])).entries()];
  if (!types.length) return null;
  return (
    <div className="overflow-x-auto" aria-label="内容矩阵">
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
                      aria-label={`${tname} ${cell.parts!.audience.name}`}
                      aria-pressed={on}
                      className={`${on ? "btn btn-primary" : "btn"} text-xs w-full`}
                      onClick={() => onPick(row.cell)}
                    >
                      {row.sources.length ? `${row.open} 条${row.multi ? "可选" : "待写"}` : "手选"}
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
  const [selected, setSelected] = useState<string | null>(null);
  const [round, setRound] = useState<string | null>(null);
  const [topics, setTopics] = useState<VideoTopic[] | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [coverage, setCoverage] = useState<CellCoverage[]>([]);
  const [error, setError] = useState("");
  const [rounds, setRounds] = useState<string[]>([]);
  const [chosenRound, setChosenRound] = useState("");
  const [discovery, setDiscovery] = useState<DiscoveryStatus | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [tick, setTick] = useState(0);
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";

  useEffect(() => {
    listVideoCells().then(setCells).catch(() => undefined);
  }, []);
  useEffect(() => {
    listCellCoverage().then(setCoverage).catch(() => undefined);
  }, [tick]);
  useEffect(() => {
    getDiscovery().then(setDiscovery).catch(() => undefined);
    const timer = window.setInterval(() => getDiscovery().then(setDiscovery).catch(() => undefined), 15000);
    return () => window.clearInterval(timer);
  }, [tick]);

  const current =
    coverage.find((c) => c.cell === selected) ?? coverage.find((c) => c.cell === "flash") ?? coverage.find((c) => c.sources.length) ?? coverage[0];
  const source = (current?.sources[0] ?? "flash") as Source;
  const noSource = Boolean(current && !current.sources.length);

  useEffect(() => {
    let alive = true;
    setTopics(null);
    if (noSource) {
      setRound(null);
      setTopics([]);
      return;
    }
    listVideoTopics(source, chosenRound || undefined)
      .then((t) => alive && (setRound(t.round), setRounds(t.rounds ?? []), setTopics(t.topics), setError("")))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    return () => {
      alive = false;
    };
  }, [source, noSource, chosenRound, tick]);
  useEffect(() => {
    setChosenRound("");
    setChecked([]);
  }, [source]);

  const filters = useFilters(["written", "score", "age", "hidden"]);
  const { values: f } = filters;
  const cell = cells.find((c) => c.usable && c.key === current?.cell);
  const inCell = (t: VideoTopic) => (t.scripts ?? []).some((x) => x.column_key === current?.cell);
  const doneHere = (t: VideoTopic) => inCell(t) || (t.published && !(t.scripts ?? []).length);
  const audienceOf = (key?: string) => cells.find((c) => c.key === key)?.parts?.audience.key;
  const typeName = (key: string) => cells.find((c) => c.key === key)?.parts?.type.name ?? key;
  // the cells this topic fits: the same audience as the picked cell, and a topic source of the same kind
  const fits = coverage.filter((c) => c.sources.includes(source) && audienceOf(c.cell) === audienceOf(current?.cell));
  const todo = (topics ?? []).filter((t) => !doneHere(t) && !t.dismissed).length;
  const multi = Boolean(current?.multi);
  const shown = (topics ?? []).filter(
    (t) =>
      (f.hidden === "yes" ? Boolean(t.dismissed) : !t.dismissed) &&
      (!f.age || (f.age === "stale" ? Boolean(t.stale) : !t.stale)) &&
      (!f.written || (f.written === "no" ? !doneHere(t) : doneHere(t))) &&
      (!f.score || (t.score ?? 0) >= Number(f.score)) &&
      matchesText(f.q, t.title, t.fields["核心角度"], t.fields["一句话"]),
  );
  const facets: Facet[] = [
    { key: "written", label: "写稿", options: [{ value: "no", label: "这个格子还没写" }, { value: "yes", label: "这个格子写过" }] },
    { key: "age", label: "新旧", options: [{ value: "fresh", label: "不算旧" }, { value: "stale", label: "偏旧（事件超过 5 天）" }] },
    { key: "hidden", label: "标记", options: [{ value: "yes", label: "只看标了不写的" }] },
    { key: "score", label: "评分", options: [{ value: "8", label: "8 分以上" }, { value: "7", label: "7 分以上" }] },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">选题</h1>
      </div>
      <MatrixGrid cells={cells} coverage={coverage} current={current?.cell} onPick={setSelected} />
      {current && !current.sources.length ? (
        <p className="text-sm text-text-muted">
          这个格子还没有选题来源，只能手选：去 <Link className="text-accent hover:underline" to={`/video?cell=${encodeURIComponent(cell?.id ?? current.cell)}`}>文稿页</Link> 选条目或填标题。
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3 text-xs text-text-muted">
        <span>
          {source === "flash" ? "来自快讯发现报告" : "来自情报轮次推荐的视频选题（P7）"}
          {round ? ` ${round}` : ""}，这个格子还有 {todo} 条没写。
          {source === "flash" && !multi ? "想把几条快讯合成一期，点上面的「盘点」格子。" : ""}
        </span>
        {rounds.length > 1 ? (
          <select aria-label="报告日期" className="input w-auto text-xs" value={chosenRound || rounds[0]} onChange={(e) => setChosenRound(e.target.value === rounds[0] ? "" : e.target.value)}>
            {rounds.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        ) : null}
        {source === "flash" && discovery ? (
          <span className="flex items-center gap-2">
            上次发现 {discovery.report_date ?? "—"}
            {discovery.report_at ? <TimeStamp value={new Date(discovery.report_at * 1000).toISOString()} /> : null}
            {canEdit ? (
              <button
                className="btn text-xs"
                disabled={discovery.running}
                title={discovery.running ? "正在跑，跑完再点" : `约 ${discovery.searches} 次搜索`}
                onClick={async () => {
                  if (!window.confirm(`生成今天的快讯选题，约 ${discovery.searches} 次搜索，通常 20 分钟左右。继续？`)) return;
                  try {
                    setDiscovery(await startDiscovery());
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
        {source === "opportunities" ? (
          <span>
            新选题来自情报轮次第 07 步：到 <Link className="text-accent hover:underline" to="/rounds">情报轮次</Link> 跑一轮就会更新。
          </span>
        ) : null}
        {note ? <span className="text-warning">{note}</span> : null}
      </div>
      {error ? <p className="text-danger text-sm">{error}</p> : null}
      {topics && topics.length ? <FilterBar facets={facets} filters={filters} shown={shown.length} total={topics.length} placeholder="搜索选题" /> : null}
      {topics && topics.length === 0 ? (
        <p className="text-sm text-text-muted">
          {!current?.sources.length ? "" : source === "flash" ? "还没有快讯发现报告。" : "还没有内容机会报告。跑一轮情报流水线后，这里会列出推荐的选题。"}
        </p>
      ) : null}
      {multi && cell ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-text-muted">选 2–10 条，合成一期盘点；已选 {checked.length} 条。</span>
          {checked.length >= 2 && checked.length <= 10 ? (
            <Link className="btn btn-primary text-xs" to={`/video?cell=${encodeURIComponent(cell.id)}&topics=${encodeURIComponent(checked.join(","))}`}>
              合成一期盘点（{checked.length} 条）
            </Link>
          ) : null}
        </div>
      ) : null}
      <ul className="space-y-2">
        {shown.map((t) => (
          <li key={t.id} className="card flex items-start gap-3">
            {multi && !doneHere(t) ? (
              <input type="checkbox" aria-label={`选 ${t.code}`} checked={checked.includes(t.id)} onChange={(e) => setChecked(e.target.checked ? [...checked, t.id] : checked.filter((id) => id !== t.id))} />
            ) : null}
            <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-sm">
                {t.title}{" "}
                {fits.map((c) => {
                  const done = (t.scripts ?? []).some((x) => x.column_key === c.cell);
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
                <span>{source === "flash" ? "发现报告" : "情报轮次"} {t.round}</span>
                {t.score != null ? <span>评分 {t.score}</span> : null}
                {(t.scripts ?? []).map((s) => (
                  <span key={s.id}>
                    <Link className="text-accent hover:underline" to={`/video/${s.id}`}>已有文稿</Link> <TimeStamp value={s.created_at} />
                  </span>
                ))}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              {doneHere(t) ? (
                t.published ? (
                  <span className="badge border border-success text-success">已发布</span>
                ) : (
                  <span className="badge border border-border text-text-muted">这个格子已写过</span>
                )
              ) : t.dismissed ? (
                <span className="badge border border-border text-text-muted">已标不写</span>
              ) : cell && !multi ? (
                <Link className="btn btn-primary text-xs" to={`/video?cell=${encodeURIComponent(cell.id)}&topic=${encodeURIComponent(t.id)}`}>
                  用这个写稿
                </Link>
              ) : null}
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
          </li>
        ))}
      </ul>
    </div>
  );
}
