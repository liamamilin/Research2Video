// API client with CSRF, auto-refresh, and 401 handling
import { sessionRevision } from "@/lib/session-events";

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
  skipAuthRefresh?: boolean;
  query?: Record<string, string | number | undefined>;
};

let onAuthError: (() => void) | null = null;
// Set once a session is declared dead. Without this, an expired session 401s on
// /api/auth/refresh, which 401s, which calls logout(), whose own request 401s
// again -- a loop that hammers the API and ends up logging the user out over a
// transient failure.
let _expiredRevision: string | null = null;

export function setAuthErrorHandler(handler: (() => void) | null) {
  onAuthError = handler;
  _expiredRevision = null;
}

// --- Token refresh ---

type RefreshResult = "ok" | "expired" | "unavailable";
let _refreshing: { revision: string; csrf: string | null; promise: Promise<RefreshResult> } | null = null;

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
  const revision = sessionRevision();
  const csrf = getCookie("ai_research_csrf");
  if (_refreshing?.revision === revision && _refreshing.csrf === csrf) return _refreshing.promise;
  const promise = (async (): Promise<RefreshResult> => {
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const csrf = getCookie("ai_research_csrf");
        const { resp, text } = await request("/api/auth/refresh", {
          method: "POST",
          credentials: "include",
          headers: csrf ? { "X-CSRF-Token": csrf } : undefined,
        }, 10_000);
        // The starting gateway may return an HTML page with HTTP 200. That
        // does not prove login recovered and must not end in a forced logout.
        if (resp.ok) {
          const data: unknown = JSON.parse(text);
          return data && typeof data === "object" && "user" in data ? "ok" : "unavailable";
        }
        if (resp.status === 401) return "expired";
        // Another tab can rotate CSRF after we build these headers. Read the
        // current cookie once more; a rejected CSRF check does not revoke login.
        if (resp.status !== 403) return "unavailable";
      }
      return "unavailable";
    } catch {
      return "unavailable";
    }
  })();
  _refreshing = { revision, csrf, promise };
  try {
    return await promise;
  } finally {
    if (_refreshing?.promise === promise) _refreshing = null;
  }
}

// --- Main api function ---

export async function api<T = unknown>(
  path: string,
  opts: RequestOpts = {},
  _retried = false,
): Promise<T> {
  const revision = sessionRevision();
  const sessionCsrf = getCookie("ai_research_csrf");
  const sessionChanged = () => revision !== sessionRevision() || sessionCsrf !== getCookie("ai_research_csrf");
  const method = opts.method || "GET";
  const headers: Record<string, string> = {
    Accept: opts.responseType === "stream" ? "text/event-stream" : "application/json",
  };

  if (opts.body !== undefined) {
    if (!(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
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
      body: opts.body instanceof FormData ? opts.body : opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
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
      if (resp.ok) throw new ApiError(resp.status, "bad_response", "服务返回的数据格式异常，请刷新重试");
      data = { error: { code: "bad_response", message: "服务返回的数据格式异常，请重试" } };
    }
  }

  if (!resp.ok) {
    const rawError = data && typeof data === "object" && "error" in data ? data.error : null;
    const errData = rawError && typeof rawError === "object"
      ? rawError as Record<string, unknown>
      : { code: "unknown", message: resp.statusText };
    const changedResponse = () => new ApiError(503, "session_changed", "登录状态已更新，请重试");
    // A late failure belongs to the session that sent it. It cannot log out
    // the newer session established by this tab or another one in the meantime.
    if (!opts.skipAuthRefresh && sessionChanged()
        && (resp.status === 401 || (resp.status === 403 && errData.code === "csrf_failed"))) {
      if (!_retried) return api<T>(path, opts, true);
      throw changedResponse();
    }
    // Auto-refresh on 401 (only once per request)
    if (resp.status === 401 && !_retried && !opts.skipAuthRefresh) {
      const ok = await tryRefresh();
      if (sessionChanged()) return api<T>(path, opts, true);
      if (ok === "ok") return api<T>(path, opts, true);
      if (ok === "unavailable") {
        throw new ApiError(503, "auth_unavailable", "会话刷新暂时失败，请重试");
      }
    }

    // If refresh failed or already retried, fire the logout callback -- once.
    if (resp.status === 401 && !opts.skipAuthRefresh && onAuthError && _expiredRevision !== revision) {
      _expiredRevision = revision;
      onAuthError();
    }

    throw new ApiError(
      resp.status,
      String(errData.code || "unknown"),
      String(errData.message || resp.statusText),
      (errData.details as Record<string, unknown>) || {},
    );
  }

  if (path === "/api/auth/login" || path === "/api/auth/me") _expiredRevision = null;
  return data as T;
}
