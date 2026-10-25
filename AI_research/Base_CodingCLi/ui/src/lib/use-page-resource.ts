import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./toast";

/** Keep the last good response on refresh, cancel stale scopes and pause hidden polling.
 * The caller must memoize load with useCallback (or pass a module-level API function).
 */
export function usePageResource<T>(load: (signal: AbortSignal) => Promise<T>, interval = 0, keepPrevious = false) {
  const [result, setResult] = useState<{ load: typeof load; data: T } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const flight = useRef<AbortController | null>(null);
  const reload = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true);
    try {
      const data = await load(controller.signal);
      if (!controller.signal.aborted) { setResult({ load, data }); setError(""); }
    } catch (e) { if (!controller.signal.aborted) setError(errorMessage(e, "读取失败")); }
    finally { if (flight.current === controller) { flight.current = null; setLoading(false); } }
  }, [load]);
  useEffect(() => {
    setError(""); void reload();
    return () => { flight.current?.abort(); flight.current = null; };
  }, [reload]);
  useEffect(() => {
    if (!interval) return;
    const refresh = () => { if (!document.hidden) void reload(); };
    const timer = window.setInterval(refresh, interval);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [interval, reload]);
  // Opt in only inside a stable ownership scope (for example a component keyed by UID).
  // This keeps search controls mounted while a different page/filter loads.
  return { data: result && (keepPrevious || result.load === load) ? result.data : null, error, loading, reload };
}
