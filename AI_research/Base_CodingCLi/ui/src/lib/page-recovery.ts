const KEY = "ui-chunk-recovery";
export function isChunkError(error: Error) {
  return /Failed to fetch dynamically imported module|Loading chunk .* failed|Importing a module script failed|Unable to preload CSS/i.test(error.message);
}
/** One refresh per destination per minute; retain URL and locally saved form inputs. */
export function recoverChunk(error: Error, storage?: Storage, href = location.href, reload = () => location.reload(), now = Date.now()) {
  if (!isChunkError(error)) return false;
  try {
    const target = storage ?? window.sessionStorage;
    const previous = JSON.parse(target.getItem(KEY) || "null");
    if (previous?.href === href && now - previous.at < 60000) return false;
    target.setItem(KEY, JSON.stringify({ href, at: now }));
    window.dispatchEvent(new Event("ui-before-reload"));
    reload();
    return true;
  } catch { return false; }
}
