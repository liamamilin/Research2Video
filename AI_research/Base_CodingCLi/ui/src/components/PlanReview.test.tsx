import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VideoScriptDetail } from "@/api/video";
import { PlanReview } from "./PlanReview";

const PLAN = {
  question: "为什么前缀要稳定",
  audience: { knows: [], lacks: [], cares: "", takeaway: "" },
  core_message: "前缀稳定，缓存才会命中",
  hook: { kind: "问题", idea: "账单为什么突然变高", facts: [] },
  glossary: [],
  beats: [
    { title: "开场", role: "钩子", point: "账单变高", facts: [], terms: [], budget: 100 },
    { title: "机制", role: "机制", point: "前缀匹配", facts: [], terms: [], budget: 200 },
  ],
  left_out: [],
};

const DETAIL = {
  id: "s1", status: "PLAN_READY", stage: null,
  pending_plan: { id: "p1", plan: PLAN, problems: [], style: {}, note: "", created_by: "editor", created_at: "" },
} as unknown as VideoScriptDetail;

let posted: { url: string; body: unknown }[] = [];

beforeEach(() => {
  posted = [];
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") posted.push({ url: String(url), body: init.body ? JSON.parse(String(init.body)) : null });
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(DETAIL) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PlanReview", () => {
  it("confirms an unchanged plan without sending any edit", async () => {
    render(<PlanReview detail={DETAIL} canEdit onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("确认并写稿"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].url).toContain("/scripts/s1/plan/confirm");
    expect(posted[0].body).toEqual({});
  });

  it("sends only the words that changed", async () => {
    render(<PlanReview detail={DETAIL} canEdit onChanged={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("主旨"), { target: { value: "先稳住前缀" } });
    fireEvent.change(screen.getByLabelText("第 2 节要点"), { target: { value: "只比较开头" } });
    fireEvent.click(screen.getByText("确认改动并写稿"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body).toEqual({ core_message: "先稳住前缀", beats: [{}, { point: "只比较开头" }] });
  });

  it("redoes only with a note, and can discard", async () => {
    render(<PlanReview detail={DETAIL} canEdit onChanged={vi.fn()} />);
    const redo = screen.getByText("带意见重做") as HTMLButtonElement;
    expect(redo.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("对方案的意见"), { target: { value: "开场换成场景" } });
    fireEvent.click(redo);
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({ url: expect.stringContaining("/plan/redo"), body: { note: "开场换成场景" } });
    fireEvent.click(screen.getByText("放弃"));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1].url).toContain("/plan/discard");
  });

  it("shows nothing to a script that is not waiting", () => {
    const { container } = render(<PlanReview detail={{ ...DETAIL, status: "DRAFT" }} canEdit onChanged={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });
});
