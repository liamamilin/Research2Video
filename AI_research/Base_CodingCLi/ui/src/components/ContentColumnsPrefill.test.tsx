import { expect, it } from "vitest";
import type { ContentColumnInput } from "@/api/content-columns";
import { withPrefill } from "./ContentColumns";

const base = { name: "快讯", matrix: "ai", type: "news", audience: "", promise: "", content_mode: "episode", production_ids: [],
  render_inheritance: "content_matrix", render: { voice_id: "v1" }, presentation: { strategy: "旧方向" }, status: "active", notes: "旧" } as unknown as ContentColumnInput;

it("fills only the suggested fields and keeps the rest", () => {
  const next = withPrefill(base, { notes: "先讲数字", "presentation.strategy": "数字开场", "render.music": "calm", unknown: 1 });
  expect(next.notes).toBe("先讲数字");
  expect(next.presentation.strategy).toBe("数字开场");
  expect(next.render).toEqual({ voice_id: "v1", music: "calm" });
  expect(base.notes).toBe("旧");
});
