import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { loadExperiments } from "@/api/experiments";
import { ExperimentsTab } from "./ExperimentsTab";

vi.mock("@/api/experiments", () => ({ loadExperiments: vi.fn() }));
afterEach(cleanup);

const registry = [{ path: "cover_style", label: "封面样式", readers: ["web/services/publish_cover.py"], timing: "from_writing" as const,
  reaches: "all" as const, applied_by: "封面文字缓存里 copy 是否为空", status: "planned" as const, values: ["number", "title"], note: "" }];

it("shows the master switch off, an empty list and the switch registry", async () => {
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: false, experiments: [], registry });
  render(<ExperimentsTab />);
  expect(await screen.findByText("关")).toBeTruthy();
  expect(screen.getByText("还没有实验。")).toBeTruthy();
  expect(screen.getByText("封面样式")).toBeTruthy();
  expect(screen.getByText("要新增")).toBeTruthy();
});

it("lists saved experiments with their kind and end point", async () => {
  vi.mocked(loadExperiments).mockResolvedValue({ enabled: true, registry, experiments: [{ id: "aa_first", status: "draft", created_at: 1, updated_at: 1,
    definition: { name: "第一次 A/A", kind: "aa", share_b: 50, metric: { primary: "completion_adjusted" }, stop: { per_arm: 20, max_days: 28 } } }] });
  render(<ExperimentsTab />);
  expect(await screen.findByText("第一次 A/A")).toBeTruthy();
  expect(screen.getByText("A/A 检查")).toBeTruthy();
  expect(screen.getByText("草稿")).toBeTruthy();
  expect(screen.getByText("每组 20 条 / 28 天")).toBeTruthy();
});
