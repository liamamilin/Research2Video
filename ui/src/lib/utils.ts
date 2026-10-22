import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Human-friendly relative time for backend timestamps like
 *  "2026-09-19T00:23:28+0800". Falls back to the raw string. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const normalized = iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const ahead = diffMs < 0; // 下一次运行这类还没到的时间：说「几小时后」，不是「刚刚」
  const mins = Math.round(Math.abs(diffMs) / 60000);
  const tail = ahead ? "后" : "前";
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟${tail}`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时${tail}`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天${tail}`;
  return iso.slice(0, 10);
}

/** Compact token counts: 1234 -> "1.2k", 2500000 -> "2.50M". */
export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** Reduce a priority label to its bare code: "**P0**" / "`P1`" / " p2 " -> "P0".
 *
 *  Priority drives a colour lookup that is an exact match, so anything the
 *  model wrapped in markup fell through to the default. The parser now strips
 *  it, but rows imported before that still hold it, and a top-priority action
 *  rendered in the muted grey of a medium one.
 */
export function normalizePriority(value: string | null | undefined): string {
  return (value || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

/** "10-20 10:32" — the date and time as written, for lists where "3 小时前" alone hides how old something is. */
export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (!m) return iso.slice(0, 16);
  const sameYear = Number(m[1]) === new Date().getFullYear();
  return `${sameYear ? "" : m[1] + "-"}${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
}
