import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthProvider } from "./AuthProvider";
import { useAuthStore } from "@/lib/auth-store";
import { setAuthErrorHandler } from "@/api/client";
import { SESSION_EVENT_KEY } from "@/lib/session-events";

function response(status: number, data: unknown) {
  return { ok: status < 400, status, statusText: "status", text: async () => JSON.stringify(data) } as Response;
}

beforeEach(() => {
  useAuthStore.setState({ user: null, loading: true, error: "" });
  setAuthErrorHandler(null);
  localStorage.clear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setAuthErrorHandler(null); });

it("offers session recovery after a temporary refresh failure without clearing browser login", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(response(401, {}))
    .mockResolvedValueOnce(response(503, {}))
    .mockResolvedValueOnce(response(200, { username: "editor", role: "editor" }));
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider><p>工作台已恢复</p></AuthProvider>);
  expect(await screen.findByRole("button", { name: "重试恢复会话" })).toBeTruthy();
  expect(screen.queryByText("工作台已恢复")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重试恢复会话" }));
  await screen.findByText("工作台已恢复");
  expect(useAuthStore.getState().user?.username).toBe("editor");
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/auth/me", "/api/auth/refresh", "/api/auth/me"]);
});

it("clears only the local view when a refresh is truly expired", async () => {
  const fetch = vi.fn().mockResolvedValue(response(401, { error: { code: "expired" } }));
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider><p>需由路由显示登录</p></AuthProvider>);
  await waitFor(() => expect(useAuthStore.getState().loading).toBe(false));
  expect(useAuthStore.getState().user).toBeNull();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/auth/me", "/api/auth/refresh"]);
});

it("recovers the login page when another tab signs in without a page reload", async () => {
  const fetch = vi.fn().mockResolvedValue(response(401, {}));
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider><p>共享会话</p></AuthProvider>);
  await waitFor(() => expect(useAuthStore.getState().loading).toBe(false));
  fetch.mockResolvedValue(response(200, { id: 1, username: "admin", role: "admin" }));
  const value = JSON.stringify({ action: "login", id: "another-tab-login" });
  localStorage.setItem(SESSION_EVENT_KEY, value);
  fireEvent(window, new StorageEvent("storage", { key: SESSION_EVENT_KEY, newValue: value }));
  await waitFor(() => expect(useAuthStore.getState().user?.username).toBe("admin"));
  expect(fetch.mock.calls.filter(([url]) => url === "/api/auth/logout")).toHaveLength(0);
});

it("ignores an old logout event queued before a newer login", async () => {
  const fetch = vi.fn().mockResolvedValue(response(200, { id: 1, username: "admin", role: "admin" }));
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider><p>共享会话</p></AuthProvider>);
  await waitFor(() => expect(useAuthStore.getState().user?.username).toBe("admin"));
  localStorage.setItem(SESSION_EVENT_KEY, JSON.stringify({ action: "login", id: "latest" }));
  fireEvent(window, new StorageEvent("storage", { key: SESSION_EVENT_KEY,
    newValue: JSON.stringify({ action: "logout", id: "old" }) }));
  expect(useAuthStore.getState().user?.username).toBe("admin");
});

it("recovers on focus without hiding an open workspace", async () => {
  const fetch = vi.fn().mockResolvedValue(response(200, { id: 1, username: "admin", role: "admin" }));
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider><p>正在编辑的内容</p></AuthProvider>);
  await screen.findByText("正在编辑的内容");
  fetch.mockRejectedValueOnce(new TypeError("network unavailable"));
  fireEvent.focus(window);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getByText("正在编辑的内容")).toBeTruthy();
  expect(useAuthStore.getState().user?.username).toBe("admin");
});
