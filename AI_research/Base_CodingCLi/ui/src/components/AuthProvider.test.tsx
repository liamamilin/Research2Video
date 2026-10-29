import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthProvider } from "./AuthProvider";
import { useAuthStore } from "@/lib/auth-store";
import { setAuthErrorHandler } from "@/api/client";

function response(status: number, data: unknown) {
  return { ok: status < 400, status, statusText: "status", text: async () => JSON.stringify(data) } as Response;
}

beforeEach(() => {
  useAuthStore.setState({ user: null, loading: true, error: "" });
  setAuthErrorHandler(null);
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
