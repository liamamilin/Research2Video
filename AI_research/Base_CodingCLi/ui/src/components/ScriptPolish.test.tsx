import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ScriptPolish } from "./ScriptPolish";

afterEach(cleanup);

describe("ScriptPolish", () => {
  it("shows each reworded paragraph with its reason and the original wording", () => {
    render(<ScriptPolish polish={{ version: 1, findings: [], rejected: [{ id: "S3P1", reason: "改写改动了数字", text: "x" }],
      changes: [{ id: "S2P4", rules: ["stock_ending"], before: "这条的要点是：按周算。", after: "以后额度按周来算。" }] }} />);
    expect(screen.getByText("自动润色了 1 段")).toBeTruthy();
    expect(screen.getByText("S2P4 · 固定句式收尾")).toBeTruthy();
    expect(screen.getByText("这条的要点是：按周算。")).toBeTruthy();
    expect(screen.getByText("以后额度按周来算。")).toBeTruthy();
    expect(screen.getByText("S3P1 的改写没有采用：改写改动了数字")).toBeTruthy();
  });

  it("stays hidden when nothing was changed or attempted", () => {
    const { container } = render(<ScriptPolish polish={{ version: 1, findings: [], changes: [], rejected: [] }} />);
    expect(container.innerHTML).toBe("");
    render(<ScriptPolish />);
  });
});
