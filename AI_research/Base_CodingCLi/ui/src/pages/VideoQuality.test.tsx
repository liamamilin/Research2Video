import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraftQualityPanel } from "./VideoQuality";

const DATA = {
  columns: { flash: { scripts: 1, edited_by_hand: 1, median_kept_share: 0.46, paragraphs_untouched: 0 } },
  scripts: [
    {
      script_id: "s1", column_key: "flash", title: "Gemini 4 Argon 上线", draft_version: 1, approved_version: 3,
      unchanged: 0, edited: 5, dropped: 2, added: 2, kept_share: 0.46, new_share: 0.22,
      signals: { length_status: "OK", serious: null, must: null, problems: 1, audited: false },
      examples: [{ before: "初稿里的这句话", after: "改完的这句话", similarity: 0.55 }],
    },
  ],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(DATA) }) as Response));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Draft quality", () => {
  it("shows the share of the draft kept per column and the edits of one script on request", async () => {
    render(<MemoryRouter><DraftQualityPanel /></MemoryRouter>);
    await screen.findByText("初稿质量");
    expect(screen.getByText("46%", { selector: "td" })).toBeTruthy();
    expect(screen.queryByText("初稿：初稿里的这句话")).toBeNull();
    fireEvent.click(screen.getByText("展开"));
    expect(screen.getByText("初稿：初稿里的这句话")).toBeTruthy();
    expect(screen.getByText("定稿：改完的这句话")).toBeTruthy();
  });
});
