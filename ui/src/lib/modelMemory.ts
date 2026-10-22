// The model last used with each provider, per settings card (ai, review_ai, extraction, video), kept in this browser:
// switching provider puts back what the card had with that provider before, or nothing at all, because another
// vendor's model id is a 404 and a guessed one is worse than an empty field.
export interface RememberedModel {
  model: string;
  fallbacks: string[];
}

const key = (scope: string) => `llm-model-memory:${scope}`;

function readAll(scope: string): Record<string, RememberedModel> {
  try {
    return JSON.parse(localStorage.getItem(key(scope)) || "{}") || {};
  } catch {
    return {};
  }
}

export function rememberModel(scope: string, providerId: string, value: RememberedModel): void {
  if (!providerId || !value.model) return;
  try {
    localStorage.setItem(key(scope), JSON.stringify({ ...readAll(scope), [providerId]: value }));
  } catch {
    /* private window or blocked storage: switching still works, it just forgets */
  }
}

export function recallModel(scope: string, providerId: string): RememberedModel | null {
  const found = readAll(scope)[providerId];
  return found && typeof found.model === "string" ? { model: found.model, fallbacks: found.fallbacks || [] } : null;
}
