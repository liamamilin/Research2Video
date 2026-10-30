import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAuthStore } from "./auth-store";
import { SESSION_EVENT_KEY } from "./session-events";
import { setAuthErrorHandler } from "@/api/client";
import type { User } from "@/api/types";

const USER: User = { id: 1, username: "admin", role: "admin", created_at: null, last_login_at: null };
function response(status: number, data: unknown): Response {
  return { ok: status < 400, status, text: async () => JSON.stringify(data) } as Response;
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.getState().setUser(null);
  setAuthErrorHandler(null);
});
afterEach(() => { vi.unstubAllGlobals(); });

it("ignores an older account check that completes after a new login", async () => {
  vi.stubGlobal("crypto", undefined); // The supported HTTP LAN URL has no randomUUID.
  let finish!: (response: Response) => void;
  const oldRequest = new Promise<Response>((resolve) => { finish = resolve; });
  vi.stubGlobal("fetch", vi.fn()
    .mockReturnValueOnce(oldRequest)
    .mockResolvedValueOnce(response(200, { user: USER })));
  const check = useAuthStore.getState().checkAuth();
  await useAuthStore.getState().login("admin", "not-stored", true);
  finish(response(200, { ...USER, id: 2, username: "previous-account" }));
  await check;
  expect(useAuthStore.getState().user?.username).toBe("admin");
  const marker = localStorage.getItem(SESSION_EVENT_KEY) || "";
  expect(JSON.parse(marker).action).toBe("login");
  expect(marker).not.toContain("not-stored");
  expect(marker).not.toContain("admin");
});

it("does not restore an older account check after an explicit logout", async () => {
  useAuthStore.setState({ user: USER });
  let finish!: (response: Response) => void;
  const oldRequest = new Promise<Response>((resolve) => { finish = resolve; });
  vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(oldRequest).mockResolvedValueOnce(response(200, {})));
  const check = useAuthStore.getState().checkAuth(true);
  await useAuthStore.getState().logout();
  finish(response(200, USER));
  await check;
  expect(useAuthStore.getState().user).toBeNull();
  expect(JSON.parse(localStorage.getItem(SESSION_EVENT_KEY) || "{}").action).toBe("logout");
});

it("does not announce an older logout after a later successful login", async () => {
  let finish!: (response: Response) => void;
  const oldRequest = new Promise<Response>((resolve) => { finish = resolve; });
  vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(oldRequest)
    .mockResolvedValueOnce(response(200, { user: USER })));
  const logout = useAuthStore.getState().logout();
  await useAuthStore.getState().login("admin", "not-stored", true);
  finish(response(200, {}));
  await logout;
  expect(JSON.parse(localStorage.getItem(SESSION_EVENT_KEY) || "{}").action).toBe("login");
  expect(useAuthStore.getState().user?.username).toBe("admin");
});
