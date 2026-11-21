import { sessionRevision } from "./session-events";

// Memory only. Mounts still revalidate; mutations invalidate pending reads too.
let generation = 0;
const snapshots = new Map<unknown, { revision: string; at: number; data: unknown }>();
export const snapshotRevision = () => `${sessionRevision()}:${generation}`;
export function invalidatePageSnapshots() {
  generation++;
  snapshots.clear();
}
export function readPageSnapshot<T>(key: unknown): T | null {
  const entry = snapshots.get(key);
  return entry && entry.revision === snapshotRevision() && Date.now() - entry.at < 60_000
    ? entry.data as T : null;
}
export function savePageSnapshot(key: unknown, data: unknown, revision: string) {
  if (revision !== snapshotRevision()) return;
  snapshots.delete(key);
  snapshots.set(key, { revision, at: Date.now(), data });
  if (snapshots.size > 40) snapshots.delete(snapshots.keys().next().value);
}
