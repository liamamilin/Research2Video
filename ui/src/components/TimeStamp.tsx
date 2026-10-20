import { cn, shortTime, timeAgo } from "@/lib/utils";

/**
 * A time that says how old it is: the date and clock time plus "3 小时前", so a list never hides whether something
 * was made just now or last week. Older than ``staleDays`` (default 3) it is shown in the warning colour.
 */
export function TimeStamp({ value, staleDays = 3, label, className }: { value: string | null | undefined; staleDays?: number; label?: string; className?: string }) {
  if (!value) return null;
  const then = new Date(value.replace(/([+-]\d{2})(\d{2})$/, "$1:$2")).getTime();
  const stale = !Number.isNaN(then) && Date.now() - then > staleDays * 86400000;
  return (
    <time dateTime={value} title={value} className={cn("text-xs whitespace-nowrap", stale ? "text-warning" : "text-text-muted", className)}>
      {label ? `${label} ` : ""}
      {shortTime(value)} · {timeAgo(value)}
    </time>
  );
}
