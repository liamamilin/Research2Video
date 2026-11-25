/** "实验 X · B 组" on a script (spec/ab-testing.md §9a step ④), read from the script's own frozen context. */
export type ExperimentMark = { id: string; arm: "A" | "B"; token?: string };

export function ExperimentBadge({ marks }: { marks?: ExperimentMark[] | null }) {
  if (!Array.isArray(marks) || marks.length === 0) return null;
  return <>{marks.map(m => <span key={m.id} className="badge border border-border text-xs" title="A/B 测试分组，建稿时决定">实验 {m.id} · {m.arm} 组</span>)}</>;
}
