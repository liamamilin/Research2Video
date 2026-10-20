import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FilterBar, matchesText, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { listVideoCells, listVideoTopics, type VideoCell, type VideoTopic } from "@/api/video";

type Source = "flash" | "opportunities";

const SOURCE_LABEL: Record<Source, string> = { flash: "快讯条目", opportunities: "内容机会" };

/** The cell a topic of this source is written in: quick takes for flash items, the first usable single-topic cell otherwise. */
function cellFor(cells: VideoCell[], source: Source): VideoCell | undefined {
  const usable = cells.filter((c) => c.usable);
  if (source === "flash") return usable.find((c) => c.parts?.type.key === "quick_take");
  return usable.find((c) => (c.column?.topic_kind ?? "single") === "single" && c.parts?.type.key !== "quick_take");
}

export function TopicsPage() {
  const [source, setSource] = useState<Source>("flash");
  const [round, setRound] = useState<string | null>(null);
  const [topics, setTopics] = useState<VideoTopic[] | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    listVideoCells().then(setCells).catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    setTopics(null);
    listVideoTopics(source)
      .then((t) => alive && (setRound(t.round), setTopics(t.topics), setError("")))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    return () => {
      alive = false;
    };
  }, [source]);

  const filters = useFilters(["written", "score"]);
  const { values: f } = filters;
  const cell = cellFor(cells, source);
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
        <div role="tablist" className="flex gap-1">
          {(Object.keys(SOURCE_LABEL) as Source[]).map((key) => (
            <button key={key} role="tab" aria-selected={key === source} className={key === source ? "btn btn-primary text-xs" : "btn text-xs"} onClick={() => setSource(key)}>
              {SOURCE_LABEL[key]}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs text-text-muted">
        {source === "flash" ? "来自快讯发现报告" : "来自情报轮次推荐的视频选题（P7）"}
        {round ? ` ${round}` : ""}，还有 {todo} 条没写。想把几条快讯合成一期盘点，去文稿页选「盘点」。
      </p>
      {error ? <p className="text-danger text-sm">{error}</p> : null}
      {topics && topics.length ? <FilterBar facets={facets} filters={filters} shown={shown.length} total={topics.length} placeholder="搜索选题" /> : null}
      {topics && topics.length === 0 ? (
        <p className="text-sm text-text-muted">
          {source === "flash" ? "还没有快讯发现报告。" : "还没有内容机会报告。跑一轮情报流水线后，这里会列出推荐的选题。"}
        </p>
      ) : null}
      <ul className="space-y-2">
        {shown.map((t) => (
          <li key={t.id} className="card flex items-start gap-3">
            <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-sm">{t.title}</p>
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
