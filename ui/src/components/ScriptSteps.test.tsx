import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VideoScriptDetail } from "@/api/video";
import { ScriptSteps } from "./ScriptSteps";

const DETAIL = {
  id: "s1", status: "DRAFT", stage: null, report_path: "output/video/r.md", column: { writer: "single" },
  steps: [
    { step: "research", label: "调研", state: "DONE", note: "" },
    { step: "write", label: "写稿", state: "DONE", note: "v1" },
    { step: "audit", label: "审计", state: "INCOMPLETE", note: "模型审计没有跑完，只有规则检查" },
    { step: "approve", label: "定稿", state: "WAITING", note: "等你定稿" },
    { step: "render", label: "出片", state: "PENDING", note: "" },
  ],
  events: [{ step: "research", event: "start", note: "", by: "admin", at: "2026-10-20T10:00:00+0800" }],
} as unknown as VideoScriptDetail;

let posted: string[] = [];

beforeEach(() => {
  posted = [];
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") posted.push(String(url));
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(DETAIL) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ScriptSteps", () => {
  it("shows each step's state and reruns one after saying what it costs", async () => {
    const changed = vi.fn();
    render(<ScriptSteps detail={DETAIL} canEdit onChanged={changed} />);
    expect(screen.getByText("没跑完")).toBeTruthy();
    expect(screen.getByText("等你定稿")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "从断点继续" })).toBeNull(); // the research did not fail
    fireEvent.click(screen.getByRole("button", { name: "重跑审计" }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("可能自动修改文稿"));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(posted).toEqual(["/api/video/scripts/s1/steps/audit/rerun"]);
    fireEvent.click(screen.getByRole("button", { name: "记录（1）" }));
    expect(screen.getByText(/调研开始 · admin/)).toBeTruthy();
  });

  it("offers no reruns to a viewer or while the script is working, and no audit rerun for an adapted script", () => {
    const { rerender } = render(<ScriptSteps detail={DETAIL} canEdit={false} onChanged={() => undefined} />);
    expect(screen.queryByRole("button", { name: "重写" })).toBeNull();
    rerender(<ScriptSteps detail={{ ...DETAIL, status: "WRITING" } as VideoScriptDetail} canEdit onChanged={() => undefined} />);
    expect(screen.queryByRole("button", { name: "重写" })).toBeNull();
    rerender(<ScriptSteps detail={{ ...DETAIL, column: { writer: "adapt" } } as unknown as VideoScriptDetail} canEdit onChanged={() => undefined} />);
    expect(screen.getByRole("button", { name: "重写" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重跑审计" })).toBeNull();
    rerender(<ScriptSteps detail={{ ...DETAIL, status: "APPROVED" } as VideoScriptDetail} canEdit onChanged={() => undefined} />);
    expect(screen.queryByRole("button", { name: "重跑审计" })).toBeNull(); // it would lose its approval
    expect(screen.getByRole("button", { name: "重写" })).toBeTruthy();
  });
});
