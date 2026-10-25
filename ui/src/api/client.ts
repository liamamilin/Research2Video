// API client with CSRF, auto-refresh, and 401 handling

export class ApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : null;
}

type RequestOpts = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  responseType?: "stream";
  timeoutMs?: number;
  query?: Record<string, string | number | undefined>;
};

let onAuthError: (() => void) | null = null;
// Set once a session is declared dead. Without this, an expired session 401s on
// /api/auth/refresh, which 401s, which calls logout(), whose own request 401s
// again -- a loop that hammers the API and ends up logging the user out over a
// transient failure.
let _sessionExpired = false;

export function setAuthErrorHandler(handler: (() => void) | null) {
  onAuthError = handler;
  _sessionExpired = false;
}

// --- Token refresh ---

type RefreshResult = "ok" | "expired" | "unavailable";
let _refreshing: Promise<RefreshResult> | null = null;

async function request(url: string, init: RequestInit, timeoutMs: number, stream = false) {
  // Keep the caller's signal attached to a live stream after its headers arrive.
  if (stream && timeoutMs <= 0) {
    const resp = await fetch(url, init);
    return { resp, text: resp.ok ? "" : await resp.text() };
  }
  const controller = new AbortController();
  const cancel = () => controller.abort();
  const signal = init.signal;
  let timedOut = false;
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", cancel, { once: true });
  const timer = timeoutMs > 0 ? setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs) : undefined;
  try {
    const resp = await fetch(url, { ...init, signal: controller.signal });
    const text = resp.ok && stream ? "" : await resp.text();
    return { resp, text };
  } catch (err) {
    if (timedOut) throw new ApiError(0, "request_timeout", "服务响应超时，请重试");
    throw err;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

async function tryRefresh(): Promise<RefreshResult> {
  if (_refreshing) return _refreshing;
  _refreshing = (async () => {
    try {
      const csrf = getCookie("ai_research_csrf");
      const { resp } = await request("/api/auth/refresh", {
        method: "POST",
        credentials: "include",
        headers: csrf ? { "X-CSRF-Token": csrf } : undefined,
      }, 10_000);
      return resp.ok ? "ok" : [401, 403].includes(resp.status) ? "expired" : "unavailable";
    } catch {
      return "unavailable";
    } finally {
      _refreshing = null;
    }
  })();
  return _refreshing;
}

// --- Main api function ---

export async function api<T = unknown>(
  path: string,
  opts: RequestOpts = {},
  _retried = false,
): Promise<T> {
  const method = opts.method || "GET";
  const headers: Record<string, string> = {
    Accept: opts.responseType === "stream" ? "text/event-stream" : "application/json",
  };

  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  // CSRF for unsafe methods
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = getCookie("ai_research_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let url = path;
  if (opts.query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined) params.set(k, String(v));
    }
    const qs = params.toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }

  let resp: Response;
  let text: string;
  try {
    ({ resp, text } = await request(url, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
      credentials: "include",
    }, opts.timeoutMs ?? (method === "GET" && opts.responseType !== "stream" ? 30_000 : 0), opts.responseType === "stream"));
  } catch (err) {
    if (err instanceof TypeError || (err instanceof Error && err.name === "AbortError")) {
      throw new ApiError(0, "network_error", "网络请求失败: " + (err as Error).message);
    }
    throw err;
  }

  // 204 No Content
  if (resp.status === 204) return undefined as T;

  // Return the live body before reading it. Authentication failures below still
  // use the same refresh / CSRF handling and never replay an opened stream.
  if (resp.ok && opts.responseType === "stream") return resp as T;

  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: { code: "bad_response", message: text.slice(0, 200) } };
    }
  }

  if (!resp.ok) {
    // Auto-refresh on 401 (only once per request)
    if (resp.status === 401 && !_retried) {
      const ok = await tryRefresh();
      if (ok === "ok") return api<T>(path, opts, true);
      if (ok === "unavailable") {
        throw new ApiError(503, "auth_unavailable", "会话刷新暂时失败，请重试");
      }
    }

    // If refresh failed or already retried, fire the logout callback -- once.
    if (resp.status === 401 && onAuthError && !_sessionExpired) {
      _sessionExpired = true;
      onAuthError();
    }

    const errData = data && typeof data === "object" && "error" in data
      ? (data as Record<string, unknown>).error as Record<string, unknown>
      : { code: "unknown", message: resp.statusText };
    throw new ApiError(
      resp.status,
      String(errData.code || "unknown"),
      String(errData.message || resp.statusText),
      (errData.details as Record<string, unknown>) || {},
    );
  }

  return data as T;
}
