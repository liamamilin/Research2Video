import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RoundsPage } from "./Rounds";

const response = (body: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify(body) }) as Response;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Rounds list", () => {
  it("shows seven rounds and one control to reveal the remaining history", async () => {
    const rounds = Array.from({ length: 9 }, (_, i) => ({ date: `2026-10-${String(23-i).padStart(2,"0")}`, done: 1, total: 1, stages: [], artifacts: [], tokens_total: 0 }));
    vi.stubGlobal("fetch", vi.fn(async () => response({ rounds, running: false, groups: [] })));
    render(<MemoryRouter><RoundsPage /></MemoryRouter>);
    await screen.findByText("2026-10-23");
    expect(screen.queryByText("2026-10-15")).toBeNull();
    expect(screen.getAllByRole("button", { name: /再显示 7 轮/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /再显示 7 轮/ }));
    expect(screen.getByText("2026-10-15")).toBeTruthy();
  });

  it("shows an initial failure with retry instead of an empty history", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue(response({ rounds: [], running: false, groups: [] })));
    render(<MemoryRouter><RoundsPage /></MemoryRouter>);
    await screen.findByRole("alert");
    expect(screen.queryByText(/暂无轮次记录/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText(/暂无轮次记录/)).toBeTruthy();
  });
});
