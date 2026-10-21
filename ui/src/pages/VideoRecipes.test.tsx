import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { VideoRecipesPage } from "./VideoRecipes";

const FLASH = {
  key: "flash", label: "快讯", status: "enabled", reason: "", error: "", usable: true, cell: "ai-developer-quick-take-long",
  name: "快讯", pipeline: "adapt", research_mode: "job", target_minutes: 3, topic_kind: "single", topic_sources: ["flash"],
  uses: { research: { kind: "research", name: "quick_take" }, output: { kind: "output", name: "motion" } },
  audiences: [{ key: "developer", recipe: "flash" }, { key: "non_tech", recipe: "ai-non_tech-quick_take-short" }],
  scripts: 4, mtime: 1, sha256: "x", content: "label: 快讯\nresearch:\n  template: quick_take\n", column: null,
};
const PARTS = [
  { kind: "research", kind_label: "调研模板", name: "quick_take", label: "快讯调研（开发者）", path: "jobs/_templates/video_research/quick_take.yaml", used_by: ["flash"] },
  { kind: "output", kind_label: "出片部件", name: "motion", label: "motion", path: "config/video_parts/output/motion.yaml", used_by: ["flash", "point_short"] },
];
const PART = { ...PARTS[0], content: "name: 快讯调研（开发者）\nprompt: |\n  核实这条变化\n", mtime: 2 };

let calls: { url: string; method: string; body: string }[] = [];

function page(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/video-recipes/:key" element={<VideoRecipesPage />} />
        <Route path="/video-recipes/parts/:kind/:name" element={<VideoRecipesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  calls = [];
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push({ url: u, method: init?.method ?? "GET", body: String(init?.body ?? "") });
    const body = u.endsWith("/recipes/parts") ? PARTS
      : u.includes("/recipes/parts/") ? { ...PART, content: init?.method === "PUT" ? JSON.parse(String(init.body)).content : PART.content }
      : u.endsWith("/recipes") ? [FLASH] : FLASH;
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null });
  vi.unstubAllGlobals();
});

describe("Video recipes page", () => {
  it("lists the audiences a recipe offers, each linked to its profile, and the column each one is", async () => {
    page("/video-recipes/flash");
    const link = await screen.findByRole("link", { name: "non_tech" });
    expect(link.getAttribute("href")).toBe("/video-recipes/parts/audience/non_tech");
    expect(screen.getByRole("link", { name: "developer" }).getAttribute("href")).toBe("/video-recipes/parts/audience/developer");
    expect(screen.getByText(/（栏目 ai-non_tech-quick_take-short）/)).toBeTruthy();
  });

  it("shows which shared parts a recipe uses, linked to them, and lists every part with how many recipes use it", async () => {
    page("/video-recipes/flash");
    const link = await screen.findByRole("link", { name: "quick_take" });
    expect(link.getAttribute("href")).toBe("/video-recipes/parts/research/quick_take");
    expect(screen.getByRole("link", { name: "motion" }).getAttribute("href")).toBe("/video-recipes/parts/output/motion");
    expect(screen.getByText(/出片部件 · motion · 2 个配方在用/)).toBeTruthy();
  });

  it("opens a part, names the recipes it feeds, and saves an edit", async () => {
    page("/video-recipes/parts/research/quick_take");
    const editor = (await screen.findByLabelText("部件 YAML")) as HTMLTextAreaElement;
    expect(screen.getByRole("link", { name: "flash" }).getAttribute("href")).toBe("/video-recipes/flash");
    fireEvent.change(editor, { target: { value: PART.content.replace("核实", "先核实") } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("/api/video/recipes/parts/research/quick_take");
    expect(JSON.parse(put.body)).toMatchObject({ expected_mtime: 2 });
  });

  it("is read-only for a viewer", async () => {
    useAuthStore.setState({ user: { username: "v", role: "viewer" } as never });
    page("/video-recipes/parts/research/quick_take");
    expect(((await screen.findByLabelText("部件 YAML")) as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
  });
});
