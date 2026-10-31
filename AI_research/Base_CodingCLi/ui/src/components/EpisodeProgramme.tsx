import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import type { ScriptSection, VideoRender } from "@/api/video";

export function EpisodeOutline({ sections }: { sections: ScriptSection[] }) {
  const items = sections.map((s, i) => ({ s, i })).filter(({ s }) => !s.episode_role || s.episode_role === "item");
  return <section className="card p-4 space-y-2 text-sm" aria-label="本期节目内容">
    <div className="font-medium">整期节目 · {items.length} 个内容</div>
    <p className="text-xs text-text-muted">同一篇总稿、同一条视频，按内容分段讲述；时长由内容决定。</p>
    <ol className="flex flex-wrap gap-2">{items.map(({ s, i }, n) => <li key={i}><button className="btn text-xs" onClick={() => document.getElementById(`script-section-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{String(n + 1).padStart(2, "0")} · {s.title}</button></li>)}</ol>
  </section>;
}
interface Chapter { id: string; title: string; start_ms: number; end_ms: number }
interface Navigation { items: { title: string; chapter_indexes: number[] }[] }
const stamp = (ms: number) => { const n = Math.floor(ms / 1000); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`; };

export function EpisodePlayer({ render, episode }: { render: VideoRender; episode: boolean }) {
  const player = useRef<HTMLVideoElement>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [time, setTime] = useState(0);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const movie = render.files.find(f => f.name === "video.mp4");
  const chapterUrl = render.files.find(f => f.name === "chapters.json")?.url;
  const navigationUrl = render.files.find(f => f.name === "episode-navigation.json")?.url;
  useEffect(() => {
    setChapters([]); setError(""); setTime(0);
    if (!episode) return;
    if (!chapterUrl || !navigationUrl) return;
    const controller = new AbortController();
    Promise.all([api<{ chapters: Chapter[] }>(chapterUrl, { signal: controller.signal }), api<Navigation>(navigationUrl, { signal: controller.signal })])
      .then(([data, direction]) => { if (!controller.signal.aborted) setChapters(direction.items.flatMap(item => {
        const parts = data.chapters.filter(c => item.chapter_indexes.some(i => c.id === `c${i + 1}`));
        return parts.length ? [{ id: parts[0].id, title: item.title, start_ms: parts[0].start_ms, end_ms: parts[parts.length - 1].end_ms }] : [];
      })); }).catch(() => { if (!controller.signal.aborted) setError("内容时间点暂未载入，视频仍可正常播放。"); });
    return () => controller.abort();
  }, [render.id, chapterUrl, navigationUrl, episode, retry]);
  return <><video ref={player} key={render.id} aria-label={`文稿 v${render.version} 成片`} controls preload="metadata" src={movie?.url} onTimeUpdate={() => setTime(player.current?.currentTime || 0)} className="w-full rounded bg-black aspect-video" />
    {episode && chapters.length ? <nav aria-label="本期视频内容导航" className="grid gap-2 sm:grid-cols-2 text-xs">{chapters.map((c, i) => <button key={c.id} aria-label={`${String(i + 1).padStart(2, "0")} · ${c.title} ${stamp(c.start_ms)}`} aria-current={time * 1000 >= c.start_ms && time * 1000 < c.end_ms ? "step" : undefined} className="btn text-left justify-start aria-[current=step]:border-accent aria-[current=step]:text-accent" onClick={() => { if (player.current) { player.current.currentTime = c.start_ms / 1000; setTime(c.start_ms / 1000); } }}>{String(i + 1).padStart(2, "0")} · {c.title}<span className="text-text-muted ml-auto">{stamp(c.start_ms)}</span></button>)}</nav> : null}
    {error ? <p className="text-xs text-warning">{error} <button className="text-accent" onClick={() => setRetry(n => n + 1)}>重新读取目录</button></p> : null}
  </>;
}
