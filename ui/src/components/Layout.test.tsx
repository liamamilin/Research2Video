import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Layout } from "./Layout";
import { useAppStore } from "@/lib/app-store";
import { useAuthStore } from "@/lib/auth-store";

function stubMatchMedia(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>home page</div>} />
          <Route path="/reports" element={<div>reports page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({
    user: {
      id: 1,
      username: "admin",
      role: "admin",
      disabled: false,
      created_at: "2026-01-01T00:00:00",
      last_login_at: null,
    },
  });
  useAppStore.setState({ sidebarOpen: false });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Layout sidebar", () => {
  it("opens the sidebar on desktop mount", () => {
    stubMatchMedia(true);
    const { container } = renderLayout();
    const aside = container.querySelector("aside");
    expect(aside?.className).toContain("translate-x-0");
    expect(aside?.className).not.toContain("-translate-x-full");
    const links = Array.from(container.querySelectorAll("nav a"));
    expect(links.slice(0, 6).map((link) => link.textContent)).toEqual(["矩阵", "产出", "视频看板", "文稿", "成片与发布", "助手"]);
    // 选题、信息源和文稿配方还在，放进「高级」
    const names = links.map((link) => link.textContent);
    expect(names.slice(names.indexOf("选题"), names.indexOf("选题") + 3)).toEqual(["选题", "信息源", "文稿配方"]);
    expect(names.indexOf("选题")).toBeGreaterThan(names.indexOf("报告"));
  });

  it("keeps the sidebar visible after navigating on desktop", () => {
    stubMatchMedia(true);
    const { container } = renderLayout();
    fireEvent.click(screen.getByText("报告"));
    const aside = container.querySelector("aside");
    expect(aside?.className).toContain("translate-x-0");
    expect(aside?.className).not.toContain("-translate-x-full");
    expect(screen.getByText("reports page")).toBeTruthy();
  });

  it("closes the drawer after navigating on mobile", () => {
    stubMatchMedia(false);
    const { container } = renderLayout();
    fireEvent.click(screen.getByText("报告"));
    const aside = container.querySelector("aside");
    expect(aside?.className).toContain("-translate-x-full");
  });
});
