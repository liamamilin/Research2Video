import { useEffect, useRef, useState } from "react";
import { useAuthStore } from "./auth-store";
import type { FirstComment } from "@/api/platforms";
export type PublishFields = { title: string; description: string; tags: string; firstComment: FirstComment; tid?: number };
const fingerprint = (value: PublishFields) => JSON.stringify(value);
function read(key: string): PublishFields | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (value && typeof value.title === "string" && typeof value.description === "string" && typeof value.tags === "string" && typeof value.firstComment?.enabled === "boolean" && typeof value.firstComment?.text === "string" && typeof value.firstComment?.pin === "boolean" && (value.tid == null || typeof value.tid === "number")) return value;
  } catch { /* Keep the server draft usable when browser storage is unavailable. */ }
  return null;
}
/** Recovery belongs to the user, platform, account and immutable render. */
export function usePublishDraft(platform: string, renderId: string, accountUid: string, initial: PublishFields, serverSaved: boolean) {
  const username = useAuthStore(s => s.user?.username) ?? "anonymous";
  const key = `publish-draft:${username}:${platform}:${accountUid}:${renderId}`;
  const fresh = () => { const recovered = read(key); return { key, value: recovered ?? initial, baseline: initial, saved: serverSaved, recovered: Boolean(recovered) }; };
  const [scope, setScope] = useState(fresh);
  // Reset synchronously so an ownership change cannot write the previous owner's fields into the new key.
  let current = scope;
  if (scope.key !== key) { current = fresh(); setScope(current); }
  const { value, baseline, saved, recovered } = current;
  const [storageError, setStorageError] = useState(false);
  const latest = useRef(value); latest.current = value;
  const dirty = fingerprint(value) !== fingerprint(baseline);
  useEffect(() => {
    const flush = () => {
      try {
        if (fingerprint(latest.current) === fingerprint(baseline)) localStorage.removeItem(key);
        else localStorage.setItem(key, fingerprint(latest.current));
        setStorageError(false);
      } catch { setStorageError(true); }
    };
    flush(); window.addEventListener("ui-before-reload", flush);
    return () => window.removeEventListener("ui-before-reload", flush);
  }, [key, value, baseline]);
  const field = <K extends keyof PublishFields>(name: K, next: PublishFields[K]) => setScope(previous => previous.key === key ? { ...previous, value: { ...previous.value, [name]: next } } : previous);
  const markSaved = (snapshot: PublishFields = value) => setScope(previous => previous.key === key ? { ...previous, baseline: snapshot, saved: true, recovered: false } : previous);
  const applySaved = (snapshot: PublishFields) => setScope(previous => previous.key === key ? { ...previous, value: snapshot, baseline: snapshot, saved: true, recovered: false } : previous);
  return { value, field, markSaved, applySaved, dirty, recovered, storageError,
    status: dirty ? "未保存 · 本机暂存" : saved ? "已保存本平台草稿" : "默认文案，可保存或直接提交",
    discard: () => setScope(previous => previous.key === key ? { ...previous, value: previous.baseline, recovered: false } : previous) };
}
