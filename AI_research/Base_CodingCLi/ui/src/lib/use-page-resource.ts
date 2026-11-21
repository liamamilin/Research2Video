import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./toast";
import { readPageSnapshot, savePageSnapshot, snapshotRevision } from "./page-snapshots";

/** Keep the last good response on refresh, cancel stale scopes and pause hidden polling.
 * The caller must memoize load with useCallback (or pass a module-level API function).
 */
export function usePageResource<T>(load: (signal: AbortSignal) => Promise<T>, interval = 0, keepPrevious = false, snapshotKey: unknown = load) {
  const [result, setResult] = useState<{ load: typeof load; snapshotKey: unknown; data: T } | null>(() => {
    const data = readPageSnapshot<T>(snapshotKey);
    return data === null ? null : { load, snapshotKey, data };
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const flight = useRef<AbortController | null>(null);
  const latestReload = useRef<(() => Promise<void>) | null>(null);
  const reload = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true);
    const revision = snapshotRevision();
    try {
      const data = await load(controller.signal);
      if (!controller.signal.aborted && revision === snapshotRevision()) {
        savePageSnapshot(snapshotKey, data, revision); setResult({ load, snapshotKey, data }); setError("");
      }
    } catch (e) { if (!controller.signal.aborted && revision === snapshotRevision()) setError(errorMessage(e, "读取失败")); }
    finally {
      if (flight.current === controller) {
        flight.current = null;
        if (!controller.signal.aborted && revision !== snapshotRevision()) void latestReload.current?.();
        else setLoading(false);
      }
    }
  }, [load, snapshotKey]);
  latestReload.current = reload;
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
  const sameScope = snapshotKey === load || result?.snapshotKey === snapshotKey;
  return { data: result && sameScope && (keepPrevious || result.load === load) ? result.data : null, error, loading, reload };
}
