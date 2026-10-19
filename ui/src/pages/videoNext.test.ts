import { describe, expect, it } from "vitest";

import type { VideoScriptDetail } from "@/api/video";
import { nextStep } from "./videoNext";

function detail(over: Partial<VideoScriptDetail>): VideoScriptDetail {
  return { status: "DRAFT", renders: [], publications: [], version_shown: null, ...over } as unknown as VideoScriptDetail;
}

describe("nextStep", () => {
  it("waits while researching or writing and reports a failure", () => {
    expect(nextStep(detail({ status: "RESEARCHING" })).tone).toBe("wait");
    expect(nextStep(detail({ status: "FAILED" })).tone).toBe("bad");
  });

  it("asks for approval of a draft and mentions must-fix findings", () => {
    const checks = { summary: { passed: false, serious: 2 } };
    const step = nextStep(detail({ version_shown: { checks } as never }));
    expect(step.tab).toBe("draft");
    expect(step.text).toContain("2 处必须改");
  });

  it("walks an approved script from render to publication", () => {
    const approved = { status: "APPROVED" } as const;
    expect(nextStep(detail(approved))).toMatchObject({ tab: "video", tone: "todo" });
    expect(nextStep(detail({ ...approved, renders: [{ status: "RUNNING" }] as never }))).toMatchObject({ tab: "video", tone: "wait" });
    expect(nextStep(detail({ ...approved, renders: [{ status: "FAILED" }] as never })).tone).toBe("bad");
    expect(nextStep(detail({ ...approved, renders: [{ status: "COMPLETED" }] as never }))).toMatchObject({ tab: "publish", tone: "todo" });
    expect(nextStep(detail({ ...approved, renders: [{ status: "COMPLETED" }] as never, publications: [{}] as never }))).toMatchObject({ tab: "publish", tone: "done" });
  });
});
