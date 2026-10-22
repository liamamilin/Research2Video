import { useEffect, useState } from "react";
import { api } from "@/api/client";

// The models a provider offers right now, beside the model id field: the id is checked against the provider's own
// /models list (GET /api/config/providers/{id}/models), so "Open Design with a model only opencode has" shows here
// instead of as a 404 on the first request. The preset list is only a hint; this one is what the provider answered.
export function ProviderModels({ providerId, label, model, fallbacks = [], onPick }: {
  providerId: string;
  label: string;
  model: string;
  fallbacks?: string[];
  onPick: (id: string) => void;
}) {
  const [state, setState] = useState<{ id: string; models: string[]; error: string | null } | null>(null);

  useEffect(() => {
    let live = true;
    setState(null);
    api<any>(`/api/config/providers/${encodeURIComponent(providerId)}/models`)
      .then((d) => live && setState({ id: providerId, models: d.models || [], error: d.error || null }))
      .catch((err) => live && setState({ id: providerId, models: [], error: err.message }));
    return () => { live = false; };
  }, [providerId]);

  if (!state || state.id !== providerId) return null;
  if (state.models.length === 0) {
    return state.error ? <div className="text-[11px] text-text-muted">没取到 {label} 的模型列表：{state.error}</div> : null;
  }
  const missing = [model, ...fallbacks].map((m) => m.trim()).filter((m) => m && !state.models.includes(m));
  return (
    <div className="space-y-1">
      {missing.length > 0 && (
        <div role="alert" className="text-[11px] text-danger">
          {label} 没有这个模型：{missing.join("、")}。请求会返回 404 model_not_found，请换成下面列表里的。
        </div>
      )}
      <details className="text-[11px] text-text-muted">
        <summary className="cursor-pointer">{label} 现在可用的模型（{state.models.length}）</summary>
        <div className="flex flex-wrap gap-1.5 pt-1.5">
          {state.models.map((id) => (
            <button key={id} type="button" onClick={() => onPick(id)}
              className="badge border border-border bg-bg-card font-mono text-[11px] hover:bg-bg-hover">
              {id}
            </button>
          ))}
        </div>
      </details>
    </div>
  );
}
