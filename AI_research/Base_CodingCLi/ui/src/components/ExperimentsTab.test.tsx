import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { createExperiment, loadExperiments, moveExperiment, setExperimentsEnabled, type ExperimentRow } from "@/api/experiments";
import { ExperimentBadge } from "./ExperimentBadge";
import { ExperimentsTab } from "./ExperimentsTab";

vi.mock("@/api/experiments", () => ({ loadExperiments: vi.fn(), loadExperiment: vi.fn(), createExperiment: vi.fn(), moveExperiment: vi.fn(), setExperimentsEnabled: vi.fn(), loadPreview: vi.fn(), requestPreview: vi.fn(), confirmPreview: vi.fn(), loadResults: vi.fn(), loadSuggestion: vi.fn() }));
afterEach(cleanup);
beforeEach(() => { vi.mocked(createExperiment).mockResolvedValue({} as ExperimentRow); vi.mocked(moveExperiment).mockResolvedValue({} as ExperimentRow); vi.mocked(setExperimentsEnabled).mockResolvedValue({ enabled: true }); });

const registry = [{ path: "cover_style", label: "封面样式", readers: ["web/services/publish_cover.py"], timing: "from_writing" as const,
  reaches: "all" as const, applied_by: "封面文字缓存里 copy 是否为空", status: "planned" as const, values: ["number", "title"], note: "" }];
const draft: ExperimentRow = { id: "aa_first", status: "draft", created_at: 1, updated_at: 1,
  definition: { id: "aa_first", name: "第一次 A/A", kind: "aa", share_b: 50, arms: { A: { name: "现状", overrides: {} }, B: { name: "现状（对照）", overrides: {} } },
    metric: { primary: "completion_adjusted", better: "higher", min_effect: 8 }, stop: { per_arm: 20, max_days: 28 } } };

it("shows the master switch off, an empty list and the switch registry", async () => {
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: false, experiments: [], registry });
  render(<ExperimentsTab />);
  expect(await screen.findByText("关")).toBeTruthy();
  expect(screen.getByText("还没有实验。")).toBeTruthy();
  expect(screen.getByText("封面样式")).toBeTruthy();
  expect(screen.getByText("要新增")).toBeTruthy();
});

it("lists saved experiments and starts a draft", async () => {
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: true, registry, experiments: [draft] });
  render(<ExperimentsTab />);
  expect(await screen.findByText("第一次 A/A")).toBeTruthy();
  expect(screen.getByText("A/A 检查")).toBeTruthy();
  expect(screen.getByText("时长校正后的完播率")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "开始" }));
  await waitFor(() => expect(moveExperiment).toHaveBeenCalledWith("aa_first", "start"));
});

it("asks before turning the master switch on", async () => {
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: false, experiments: [], registry });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<ExperimentsTab />);
  fireEvent.click(await screen.findByRole("button", { name: "打开总开关" }));
  expect(setExperimentsEnabled).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "打开总开关" }));
  await waitFor(() => expect(setExperimentsEnabled).toHaveBeenCalledWith(true));
  confirm.mockRestore();
});

it("saves a new A/A check with both arms unchanged", async () => {
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: false, experiments: [], registry });
  render(<ExperimentsTab />);
  fireEvent.click(await screen.findByRole("button", { name: "新建实验" }));
  fireEvent.change(screen.getByLabelText(/标识/), { target: { value: "aa_first" } });
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "第一次 A/A" } });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(createExperiment).toHaveBeenCalled());
  const saved = vi.mocked(createExperiment).mock.calls[0][0];
  expect(saved.kind).toBe("aa");
  expect(saved.arms.B.overrides).toEqual({});
  expect(saved.stop.per_arm).toBe(20);
});

it("labels a script with its experiment and arm, and nothing otherwise", () => {
  const { container } = render(<ExperimentBadge marks={[]} />);
  expect(container.textContent).toBe("");
  render(<ExperimentBadge marks={[{ id: "aa_first", arm: "B" }]} />);
  expect(screen.getByText("实验 aa_first · B 组")).toBeTruthy();
});

it("shows A and B side by side once a preview is done and confirms it", async () => {
  const { loadExperiment, loadPreview, confirmPreview } = await import("@/api/experiments");
  const visual: ExperimentRow = { ...draft, id: "card", definition: { ...draft.definition, id: "card", name: "要点卡", kind: "compare",
    arms: { A: { name: "现状", overrides: {} }, B: { name: "要点卡", overrides: { "presentation.opening_card": true } } } } };
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: false, registry, experiments: [visual] });
  vi.mocked(loadExperiment).mockResolvedValue({ ...visual, events: [], start_problems: ["画面类、封面类开关要先预览 B 并确认"], arms: { A: 0, B: 0 } });
  vi.mocked(loadPreview).mockResolvedValue({ kind: "render", confirmed: false, sources: [{ render_id: "r1", script_id: "s1", title: "一条视频", completed_at: "" }],
    preview: { id: "p1", status: "done", error: "", source_render_id: "r1", files: ["video.mp4"] }, a_video: "/a.mp4", b_video: "/b.mp4" });
  vi.mocked(confirmPreview).mockResolvedValue({});
  const { container } = render(<ExperimentsTab />);
  fireEvent.click(await screen.findByRole("button", { name: "要点卡" }));
  expect(await screen.findByText("状态：已出好")).toBeTruthy();
  expect(container.querySelectorAll("video")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "确认 B 没问题" }));
  await waitFor(() => expect(confirmPreview).toHaveBeenCalledWith("card"));
});
