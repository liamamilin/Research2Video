import { useEffect, useState } from "react";
import type { ScriptSection, ScriptVersion } from "@/api/video";

type Draft = { title: string; sections: ScriptSection[]; baseVersion: number; original: string };
const content = (title: string, sections: ScriptSection[]) => JSON.stringify({ title, sections });

/** Local recovery is scoped to the signed-in person, script and immutable base version. */
export function useScriptEditor(id: string, username: string, version: ScriptVersion | null | undefined) {
  const key = version ? `script-editor:${username}:${id}:v${version.version}` : "";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [recovery, setRecovery] = useState<Draft | null>(null);
  const [storageError, setStorageError] = useState(false);
  const dirty = Boolean(draft && content(draft.title, draft.sections) !== draft.original);

  useEffect(() => {
    setEditing(false); setDraft(null); setRecovery(null); setStorageError(false);
    if (!key) return;
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null") as Draft | null;
      if (value && value.baseVersion === version?.version && typeof value.title === "string" && typeof value.original === "string"
        && Array.isArray(value.sections) && value.sections.every(s => typeof s.title === "string" && Array.isArray(s.paragraphs)
          && s.paragraphs.every(p => typeof p.text === "string" && Array.isArray(p.citations)))) setRecovery(value);
    } catch { setStorageError(true); }
  }, [key, version?.version]);

  useEffect(() => {
    if (!editing || !draft || !key) return;
    try {
      if (dirty) { localStorage.setItem(key, JSON.stringify(draft)); setRecovery(draft); }
      else { localStorage.removeItem(key); setRecovery(null); }
      setStorageError(false);
    } catch { setStorageError(true); }
  }, [key, editing, draft, dirty]);

  function start() {
    if (!version) return;
    setDraft(recovery ?? { title: version.title, sections: structuredClone(version.sections), baseVersion: version.version,
      original: content(version.title, version.sections) });
    setEditing(true);
  }
  function discard() {
    try { if (key) localStorage.removeItem(key); } catch { setStorageError(true); }
    setRecovery(null); setDraft(null); setEditing(false);
  }
  return { editing, dirty, recovery, storageError, title: draft?.title ?? "", sections: draft?.sections ?? [],
    baseVersion: draft?.baseVersion, start, stop: () => setEditing(false), discard,
    setTitle: (title: string) => setDraft(d => d && { ...d, title }),
    setSections: (update: (sections: ScriptSection[]) => ScriptSection[]) => setDraft(d => d && { ...d, sections: update(d.sections) }) };
}
