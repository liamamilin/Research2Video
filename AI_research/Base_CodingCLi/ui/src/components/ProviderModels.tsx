import { useEffect, useSyncExternalStore } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/api/client";

interface Price {
  input: number;
  input_max: number;
  output: number;
  output_max: number;
  tiers: { label: string; input: number; output: number }[];
}

interface Catalog {
  models: string[];
  free_models: string[];
  prices: Record<string, Price>;
  updated_at?: number | null;
  pricing_updated_at?: number | null;
  pricing_source?: string | null;
  pricing_error?: string | null;
  stale?: boolean;
  error?: string | null;
  loading?: boolean;
}

const EMPTY: Catalog = { models: [], free_models: [], prices: {} };
const catalogs = new Map<string, Catalog>();
const pending = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

function publish(id: string, data: Catalog) {
  catalogs.set(id, data);
  listeners.forEach((listener) => listener());
}

function loadCatalog(id: string, refresh = false): Promise<void> {
  const existing = pending.get(id);
  if (existing) return existing;
  const before = catalogs.get(id) ?? EMPTY;
  publish(id, { ...before, loading: true });
  const request = api<Catalog>(`/api/config/providers/${encodeURIComponent(id)}/models${refresh ? "?refresh=true" : ""}`)
    .then((data) => publish(id, { ...EMPTY, ...data, loading: false }))
    .catch((err: Error) => publish(id, { ...before, loading: false, stale: before.models.length > 0, error: err.message }))
    .finally(() => { pending.delete(id); });
  pending.set(id, request);
  return request;
}

export function isOffered(providerId: string, model: string): boolean {
  const catalog = catalogs.get(providerId);
  return !catalog || catalog.stale || catalog.models.length === 0 || catalog.models.includes(model);
}

function dollars(low: number, high: number): string {
  return low === high ? `$${low}` : `$${low}–${high}`;
}

function priceLabel(price: Price | undefined, local: boolean): string {
  if (local) return "本地免费";
  if (!price) return "价格未知";
  if (price.input_max === 0 && price.output_max === 0) return "免费";
  return `入 ${dollars(price.input, price.input_max)} / 出 ${dollars(price.output, price.output_max)}`;
}

function stamp(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

// Cards sharing a provider refresh together; picking a chip still writes only the original model ID.
export function ProviderModels({ providerId, label, model, fallbacks = [], onPick }: {
  providerId: string;
  label: string;
  model: string;
  fallbacks?: string[];
  onPick: (id: string) => void;
}) {
  const state = useSyncExternalStore(subscribe, () => catalogs.get(providerId) ?? EMPTY);
  useEffect(() => { void loadCatalog(providerId); }, [providerId]);

  const missing = state.stale || state.error ? [] : [model, ...fallbacks]
    .map((m) => m.trim()).filter((m) => m && state.updated_at && !state.models.includes(m));
  const local = providerId === "ollama";
  const chip = (id: string) => {
    const price = state.prices[id];
    const title = price?.tiers?.map((tier) => `${tier.label}: 输入 $${tier.input} / 输出 $${tier.output}`).join("\n");
    return (
      <button key={id} type="button" onClick={() => onPick(id)}
        title={title ? `${title}\n每百万 tokens（USD）` : undefined}
        className="badge border border-border bg-bg-card font-mono text-[11px] hover:bg-bg-hover">
        {id}<span className="ml-1 text-text-muted">({priceLabel(price, local)})</span>
      </button>
    );
  };

  return (
    <div className="space-y-2 text-[11px] text-text-muted">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn text-xs" disabled={state.loading}
          onClick={() => void loadCatalog(providerId, true)}>
          <RefreshCw className={`w-3 h-3${state.loading ? " animate-spin" : ""}`} />
          {state.loading ? "检测中…" : "检测并更新模型"}
        </button>
        <span role="status">
          {state.updated_at ? `${state.stale ? "上次成功" : "已更新"}：${stamp(state.updated_at)}` : "从服务商检测模型与免费列表"}
        </span>
      </div>
      {state.error && <div role="alert">{state.error}{state.stale ? "；保留上次清单。" : ""}</div>}
      {missing.length > 0 && <div role="alert" className="text-danger">
        {label} 没有这个模型：{missing.join("、")}。请从最新清单选择。
      </div>}
      {state.updated_at && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span>免费模型{state.pricing_error && !local ? "（参考）" : ""}：</span>
          {state.free_models.length ? state.free_models.map(chip) : <span>未检测到已确认的免费模型</span>}
        </div>
      )}
      {state.models.length > 0 && <details>
        <summary className="cursor-pointer">{label} 现在可用的模型（{state.models.length}）</summary>
        <div className="flex flex-wrap gap-1.5 pt-1.5">{state.models.map(chip)}</div>
      </details>}
      {state.models.length > 0 && <div className="space-y-1">
        <div>价格：USD / 每百万 tokens，入 = 输入，出 = 输出；区间随上下文或时段变化。
          {providerId === "opencode-go" && " Go 单价用于扣减订阅额度，月费另计。"}
          {state.free_models.length > 0 && !local && " 免费模型可能有地区、期限或额度限制。"}
        </div>
        {state.pricing_source && <div>
          <a href={state.pricing_source} target="_blank" rel="noreferrer" className="underline">官网价格来源</a>
          {state.pricing_updated_at && ` · 价格更新：${stamp(state.pricing_updated_at)}`}
        </div>}
        {state.pricing_error && <div role="status">{state.pricing_error}</div>}
        <div>检测读取模型清单，不产生推理费用；实际权限与额度可用“测试”验证。</div>
      </div>}
    </div>
  );
}
