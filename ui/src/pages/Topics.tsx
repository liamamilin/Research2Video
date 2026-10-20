import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FilterBar, matchesText, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { listCellCoverage, listVideoCells, listVideoTopics, type CellCoverage, type VideoCell, type VideoTopic } from "@/api/video";

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
                      {row.sources.length ? `${row.open} 条待写` : "手选"}
                      {row.status !== "enabled" ? "（实验）" : ""}
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

  useEffect(() => {
    listVideoCells().then(setCells).catch(() => undefined);
    listCellCoverage().then(setCoverage).catch(() => undefined);
  }, []);

  const current = coverage.find((c) => c.cell === selected) ?? coverage.find((c) => c.sources.length) ?? coverage[0];
  const source = (current?.sources[0] ?? "flash") as Source;

  useEffect(() => {
    let alive = true;
    setTopics(null);
    if (current && !current.sources.length) {
      setRound(null);
      setTopics([]);
      return;
    }
    listVideoTopics(source)
      .then((t) => alive && (setRound(t.round), setTopics(t.topics), setError("")))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    return () => {
      alive = false;
    };
  }, [source, current?.cell, current?.sources.length]);

  const filters = useFilters(["written", "score"]);
  const { values: f } = filters;
  const cell = cells.find((c) => c.usable && c.key === current?.cell);
  const todo = (topics ?? []).filter((t) => !t.scripted && !t.published).length;
  const shown = (topics ?? []).filter(
    (t) =>
      (!f.written || (f.written === "no" ? !t.scripted && !t.published : t.scripted || t.published)) &&
      (!f.score || (t.score ?? 0) >= Number(f.score)) &&
      matchesText(f.q, t.title, t.fields["核心角度"], t.fields["一句话"]),
  );
  const facets: Facet[] = [
    { key: "written", label: "写稿", options: [{ value: "no", label: "还没写" }, { value: "yes", label: "已写过" }] },
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
      <p className="text-xs text-text-muted">
        {source === "flash" ? "来自快讯发现报告" : "来自情报轮次推荐的视频选题（P7）"}
        {round ? ` ${round}` : ""}，还有 {todo} 条没写。想把几条快讯合成一期盘点，去文稿页选「盘点」。
      </p>
      {error ? <p className="text-danger text-sm">{error}</p> : null}
      {topics && topics.length ? <FilterBar facets={facets} filters={filters} shown={shown.length} total={topics.length} placeholder="搜索选题" /> : null}
      {topics && topics.length === 0 ? (
        <p className="text-sm text-text-muted">
          {!current?.sources.length ? "" : source === "flash" ? "还没有快讯发现报告。" : "还没有内容机会报告。跑一轮情报流水线后，这里会列出推荐的选题。"}
        </p>
      ) : null}
      <ul className="space-y-2">
        {shown.map((t) => (
          <li key={t.id} className="card flex items-start gap-3">
            <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-sm">
                {t.title} {current ? <span className="badge border border-border text-text-muted">{current.label}</span> : null}
              </p>
              {t.fields["核心角度"] ? <p className="text-xs text-text-muted">角度：{t.fields["核心角度"]}</p> : null}
              <p className="text-xs text-text-muted flex flex-wrap gap-x-3">
                {t.fields["发布日期"] ? <span>事件日期 {t.fields["发布日期"]}</span> : null}
                <span>{source === "flash" ? "发现报告" : "情报轮次"} {t.round}</span>
                {t.score != null ? <span>评分 {t.score}</span> : null}
                {(t.scripts ?? []).map((s) => (
                  <span key={s.id}>
                    <Link className="text-accent hover:underline" to={`/video/${s.id}`}>已有文稿</Link> <TimeStamp value={s.created_at} />
                  </span>
                ))}
              </p>
            </div>
            {t.published ? (
              <span className="badge border border-success text-success">已发布</span>
            ) : t.scripted ? (
              <span className="badge border border-border text-text-muted">已写过</span>
            ) : cell ? (
              <Link className="btn btn-primary text-xs shrink-0" to={`/video?cell=${encodeURIComponent(cell.id)}&topic=${encodeURIComponent(t.id)}`}>
                用这个写稿
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
