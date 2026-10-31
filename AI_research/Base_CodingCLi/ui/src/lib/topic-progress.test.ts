import { describe, expect, it } from "vitest";
import type { TopicScript, VideoTopic } from "@/api/video";
import { cellOf, scriptsHere, topicReturn, topicTaken, topicTask } from "./topic-progress";

const script = (extra: Partial<TopicScript> = {}): TopicScript => ({ id: "s", column_key: "flash-nr", status: "WRITING", created_at: "2026-10-30", version: 1, render_state: "none", ...extra });
const topic = (scripts: TopicScript[]): VideoTopic => ({ id: "F1", round: "2026-10-30", code: "F1", title: "一个选题", fields: {}, score: null, scripted: true, scripts });

describe("topic workflow", () => {
  it("shares the same cell matching for variants, other audiences and failed attempts", () => {
    const t = topic([script(), script({ id: "other", column_key: "point_short", status: "FAILED" })]);
    expect(topicTaken(t, "flash")).toBe(true);
    expect(topicTaken(t, "point_short")).toBe(false);
    expect(topicTaken(t, "ai-non_tech-quick_take-short")).toBe(false);
    expect(cellOf(script({ column_key: "flash-m2p5-nr" }))).toBe("flash");
    expect(cellOf(script({ column_key: "custom-nr", cell_key: "custom-nr" }))).toBe("custom-nr");
  });
  it("uses the current version and prioritizes a live script over archived failures", () => {
    const t = topic([script({ id: "old", status: "FAILED", archived: 1, created_at: "2026-11-01" }), script({ status: "APPROVED", previous_video: { version: 0, url: "/old.mp4" } })]);
    expect(scriptsHere(t, "flash")[0].id).toBe("s");
    expect(topicTask(t, "flash")).toBe("ready");
    expect(topicTask(topic([script({ status: "DRAFT" })]), "flash")).toBe("draft");
    expect(topicTask(topic([script({ status: "FAILED" })]), "flash")).toBe("failed");
    expect(topicTask(topic([script({ render_state: "failed" })]), "flash")).toBe("failed");
    expect(topicTask(topic([script({ render_state: "done", render: { id: "retry", status: "RUNNING", version: 1 } })]), "flash")).toBe("done");
  });
  it("never allows an external or unrelated return destination", () => {
    expect(topicReturn("/topics?cell=flash&round=2026-10-29#topic-F1")).toBeTruthy();
    for (const value of ["https://evil.test/topics", "//evil.test/topics", "/topics/other", "/topics-else", "/video", "/topics?x=\\foo", "/topics\n"]) expect(topicReturn(value)).toBeNull();
  });
  it("classifies explicit skips separately from real failures without hiding live work", () => {
    const skipped = script({ status: "FAILED", archived: 1, version: 0,
      selection_skip: { kind: "duplicate", reason: "已经讲过", originals: [] } });
    expect(topicTask(topic([skipped]), "flash")).toBe("skipped");
    expect(topicTaken(topic([skipped]), "flash")).toBe(true);
    expect(topicTask(topic([skipped, script({ status: "APPROVED" })]), "flash")).toBe("ready");
    expect(topicTask(topic([script({ status: "FAILED" })]), "flash")).toBe("failed");
  });
});
