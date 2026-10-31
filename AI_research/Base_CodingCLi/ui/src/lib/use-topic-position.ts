import { useLayoutEffect, useRef, type RefObject } from "react";
import { useLocation } from "react-router-dom";

const PREFIX = "topic-list-position:";
function saved(key: string): number | null {
  try { const raw = sessionStorage.getItem(PREFIX + key); return raw == null ? null : Number(raw); } catch { return null; }
}

/** Restore after asynchronous cards are present, inside Layout's scrolling main. */
export function useTopicPosition(root: RefObject<HTMLDivElement>, ready: boolean) {
  const location = useLocation();
  const scope = location.pathname + location.search;
  const pending = useRef(true);
  useLayoutEffect(() => {
    if (!ready || !root.current) return;
    const main = root.current.closest("main");
    if (!main) return;
    const restore = pending.current;
    let frame = 0;
    if (restore) {
      const top = saved(scope);
      frame = requestAnimationFrame(() => {
        if (top != null && Number.isFinite(top)) main.scrollTop = top;
        else if (location.hash) {
          try { document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({ block: "center" }); } catch { /* Ignore malformed optional anchors. */ }
        }
        pending.current = false;
      });
    }
    const remember = () => {
      if (pending.current) return;
      try { sessionStorage.setItem(PREFIX + scope, String(main.scrollTop)); } catch { /* Private browsing can disable storage. */ }
    };
    main.addEventListener("scroll", remember, { passive: true });
    return () => { remember(); main.removeEventListener("scroll", remember); cancelAnimationFrame(frame); };
  }, [root, ready, scope, location.hash]);
}
