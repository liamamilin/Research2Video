import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Layout } from "@/components/Layout";
import { HomeBoardPage } from "@/pages/HomeBoard";
import { MatricesPage } from "@/pages/Matrices";
import { OutputsPage } from "@/pages/Outputs";
import { VideoScriptsPage } from "@/pages/VideoScripts";
import { PublishQueuePage } from "@/pages/PublishQueue";
import { ToastProvider } from "@/lib/toast";
import { useAuthStore } from "@/lib/auth-store";
import { invalidatePageSnapshots } from "@/lib/page-snapshots";
import { pageLoader } from "@/lib/page-modules";
import workload from "../../tests/fixtures/console_workload.json";

const count = workload.scripts;
const stamp = workload.timestamp;
const scripts = Array.from({ length: count }, (_, i) => ({
  id: `s${i}`, title: `固定视频 ${i}`, status: "APPROVED", current_version: 1, approved_version: 1,
  column_key: "flash", column_label: "固定快讯", created_at: stamp, updated_at: stamp,
  matrix: `m${i % workload.matrices}`, matrix_name: `固定矩阵 ${i % workload.matrices}`,
  job: `fixture/job_${i % workload.jobs}`, topic: { title: `固定视频 ${i}` }, content_context: {},
  render: { id: `r${i}`, status: "COMPLETED", version: 1, files: [], created_at: stamp, completed_at: stamp },
  credibility: { tier: "generated", text: "按素材生成", reasons: [], cited_share: null },
  publications: [], distributions: [],
}));
const matrices = Array.from({ length: workload.matrices }, (_, i) => ({
  key: `m${i}`, name: `固定矩阵 ${i}`, keywords: [], errors: [], paused: [], jobs: [],
  members: Array.from({ length: workload.jobs / workload.matrices }, (_, j) => ({
    job: `fixture/job_${i + j * workload.matrices}`, name: `固定任务 ${i}-${j}`, exists: true, schedule: null,
  })),
}));
const board = {
  stages: [], failed: 0, attention: [], matrices,
  items: scripts.map(s => ({ ...s, script_id: s.id, stage: "draft", version: 1, group: "todo", action: "approve",
    action_label: "查看并定稿", note: "固定说明", job_name: "固定任务", has_video: true, has_previous_video: false })),
};
const response = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(body) }) as Response;
const calls: string[] = [];
let delayReads = false;
let finishBoard: ((response: Response) => void) | undefined;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  calls.length = 0; delayReads = false; finishBoard = undefined;
  useAuthStore.setState({ user: { username: "fixture", role: "viewer" } as never });
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(String(url), "http://fixture").pathname;
    calls.push(path);
    if (delayReads && !path.startsWith("/api/chains")) return new Promise<Response>(() => {});
    if (path === "/api/chains") return response({ items: [], archived: [] });
    if (path === "/api/video/board") {
      if (finishBoard === undefined) return response(board);
      return new Promise<Response>(resolve => { finishBoard = resolve; });
    }
    if (path === "/api/video/alerts") return response([]);
    if (path === "/api/matrices") return response({ matrices, ungrouped: [] });
    if (path === "/api/outputs") return response({ matrices, scripts: scripts.map(s => ({ ...s, version: 1, stage: "rendered" })), jobs: [] });
    if (path === "/api/video/scripts") return response(scripts);
    if (path === "/api/video/shipments") return response(scripts.map(s => ({ ...s, script_id: s.id, version: 1, warnings: [] })));
    throw new Error(`Unexpected initial page request: ${path}`);
  }));
});
afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  useAuthStore.setState({ user: null });
});

function open() {
  return render(<ToastProvider><MemoryRouter><Routes><Route element={<Layout />}>
    <Route index element={<HomeBoardPage />} />
    <Route path="matrices" element={<MatricesPage />} />
    <Route path="outputs" element={<OutputsPage />} />
    <Route path="video" element={<VideoScriptsPage />} />
    <Route path="publish" element={<PublishQueuePage />} />
  </Route></Routes></MemoryRouter></ToastProvider>);
}
function navigate(name: string) {
  fireEvent.click(within(screen.getByRole("navigation")).getByRole("link", { name }));
}

it("keeps 600-record first visits bounded and restores each recent list during a delayed revalidation", async () => {
  useAuthStore.setState({ user: { username: "fixture", role: "admin" } as never });
  open();
  await screen.findByRole("button", { name: `全部视频 ${count}` });
  const covered = ["/chains", "/matrices", "/outputs", "/", "/video", "/publish", "/platforms", "/ask",
    "/engine", "/jobs", "/rounds", "/reports", "/scheduler", "/topics", "/sources", "/video-recipes", "/analytics", "/settings"];
  const destinations = within(screen.getByRole("navigation")).getAllByRole("link")
    .map(link => new URL((link as HTMLAnchorElement).href).pathname);
  expect(destinations.sort()).toEqual(covered.sort());
  for (const path of destinations) expect(pageLoader(path)).toBeTypeOf("function");
  const metrics: { tab: string; first_ms: number; return_ms: number }[] = [];
  for (const [tab, heading, api] of [
    ["矩阵", "矩阵", "/api/matrices"], ["产出", "产出", "/api/outputs"],
    ["文稿", "视频文稿", "/api/video/scripts"], ["成片与发布", "成片与发布", "/api/video/shipments"],
  ]) {
    const start = performance.now();
    navigate(tab);
    await screen.findByRole("heading", { name: heading });
    if (tab === "矩阵") await screen.findByRole("link", { name: "固定矩阵 0" });
    else await screen.findByText("固定视频 0", { exact: true, selector: "a" });
    const first = performance.now() - start;
    expect(calls.filter(path => path === api)).toHaveLength(1);
    expect(document.querySelectorAll("main tbody tr,main li").length).toBeLessThanOrEqual(50);
    navigate("视频看板");
    await screen.findByRole("button", { name: `全部视频 ${count}` });
    delayReads = true;
    const back = performance.now();
    navigate(tab);
    // Synchronous assertions: useful content must exist before the delayed GET.
    expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
    if (tab === "矩阵") expect(screen.getByRole("link", { name: "固定矩阵 0" })).toBeTruthy();
    else expect(screen.getByText("固定视频 0", { exact: true, selector: "a" })).toBeTruthy();
    expect(calls.filter(path => path === api)).toHaveLength(2);
    metrics.push({ tab, first_ms: first, return_ms: performance.now() - back });
    delayReads = false;
    navigate("视频看板");
    await screen.findByRole("button", { name: `全部视频 ${count}` });
  }
  // Diagnostic jsdom times, not browser LCP or a machine-independent time gate.
  console.info("NAV_PERF", JSON.stringify(metrics));
});

it("ignores the visible board's pre-save response and immediately loads the saved data", async () => {
  open();
  await screen.findByRole("button", { name: `全部视频 ${count}` });
  finishBoard = () => {};
  fireEvent.click(screen.getByRole("button", { name: "刷新看板" }));
  await waitFor(() => expect(calls.filter(path => path === "/api/video/board")).toHaveLength(2));
  const oldRequest = finishBoard;
  invalidatePageSnapshots();
  finishBoard = undefined;
  await act(async () => oldRequest?.(response({ ...board, items: [] })));
  await waitFor(() => expect(calls.filter(path => path === "/api/video/board")).toHaveLength(3));
  expect(screen.getByRole("button", { name: `全部视频 ${count}` })).toBeTruthy();
});
