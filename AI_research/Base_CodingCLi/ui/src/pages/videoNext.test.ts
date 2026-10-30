import { describe, expect, it } from "vitest";

import type { VideoScriptDetail } from "@/api/video";
import { nextStep, currentPublications } from "./videoNext";

function detail(over: Partial<VideoScriptDetail>): VideoScriptDetail {
  return { status: "DRAFT", renders: [], publications: [], version_shown: null, current_version: 1, versions: [{ version: 1 }], ...over } as unknown as VideoScriptDetail;
}

describe("nextStep", () => {
  it("waits while researching or writing and reports a failure", () => {
    expect(nextStep(detail({ status: "RESEARCHING" })).tone).toBe("wait");
    expect(nextStep(detail({ status: "FAILED" })).tone).toBe("bad");
  });

  it("guides explicit duplicate and exhausted skips without reporting a technical failure", () => {
    for (const kind of ["duplicate", "exhausted"] as const) {
      expect(nextStep(detail({ status: "FAILED", selection_skip: { kind, reason: "跳过", originals: [] } }))).toMatchObject({ tone: "done", tab: "draft" });
    }
  });

  it("asks for approval of a draft and mentions must-fix findings", () => {
    const checks = { summary: { passed: false, serious: 2 } };
    const step = nextStep(detail({ version_shown: { checks } as never }));
    expect(step.tab).toBe("draft");
    expect(step.text).toContain("2 处严重问题");
  });

  it("walks an approved script from render to publication", () => {
    const approved = { status: "APPROVED" } as const;
    expect(nextStep(detail(approved))).toMatchObject({ tab: "video", tone: "todo" });
    expect(nextStep(detail({ ...approved, renders: [{ version: 1, status: "RUNNING" }] as never }))).toMatchObject({ tab: "video", tone: "wait" });
    expect(nextStep(detail({ ...approved, renders: [{ version: 1, status: "FAILED" }] as never })).tone).toBe("bad");
    expect(nextStep(detail({ ...approved, renders: [{ version: 1, status: "COMPLETED" }] as never }))).toMatchObject({ tab: "publish", tone: "todo" });
    expect(nextStep(detail({ ...approved, renders: [{ version: 1, status: "COMPLETED" }] as never, publications: [{ version: 1 }] as never }))).toMatchObject({ tab: "publish", tone: "done" });
  });
  it("ignores earlier-version videos and publications, and keeps a completed video after a failed retry", () => {
    const newer = detail({ status: "APPROVED", current_version: 2, versions: [{ version: 2 }, { version: 1 }] as never,
      renders: [{ version: 1, status: "COMPLETED" }] as never, publications: [{ version: 1 }, { version: null }] as never });
    expect(nextStep(newer)).toMatchObject({ tab: "video", text: "已定稿，下一步生成成片。" });
    newer.renders = [{ version: 2, status: "FAILED" }, { version: 2, status: "COMPLETED" }] as never;
    expect(nextStep(newer)).toMatchObject({ tab: "publish", tone: "todo" });
  });

  it("waits for an audit and treats style-only findings as advice", () => {
    expect(nextStep(detail({ stage: "事实审计" })).tone).toBe("wait");
    const checks = { summary: { passed: false, serious: 0, must: 3 } };
    expect(nextStep(detail({ version_shown: { checks } as never })).text).not.toContain("必须改");
  });
});


it("does not assign an unknown legacy publication version to the current draft", () => {
  const d = detail({ status: "APPROVED", publications: [{ version: null, platform: "bilibili" }] as never });
  expect(currentPublications(d)).toEqual([]);
  expect(nextStep(d)).toMatchObject({ tab: "video", tone: "todo" });
});


it("keeps polling guidance for the Douyin worker RUNNING status", () => {
  const d = detail({ status: "APPROVED", distributions: [{ version: 1, platform: "douyin", status: "UPLOADING", task: { status: "RUNNING" } }] as never });
  expect(nextStep(d)).toMatchObject({ tab: "publish", tone: "wait" });
});
