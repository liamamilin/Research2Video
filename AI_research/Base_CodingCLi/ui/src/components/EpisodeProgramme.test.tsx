import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ScriptSection, VideoRender } from "@/api/video";
import { EpisodeOutline, EpisodePlayer } from "./EpisodeProgramme";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const movie = { id: "r1", version: 1, files: [{ name: "video.mp4", url: "/movie.mp4" }, { name: "chapters.json", url: "/chapters.json" }, { name: "episode-navigation.json", url: "/navigation.json" }] } as VideoRender;
it("lists content items separately from opening, closing and paragraphs", () => {
  const sections = [{ title: "导读", episode_role: "opening", paragraphs: [] }, { title: "模型变化", episode_role: "item", paragraphs: [{ text: "段落甲" }, { text: "段落乙" }] }, { title: "回顾", episode_role: "closing", paragraphs: [] }] as ScriptSection[];
  render(<EpisodeOutline sections={sections} />);
  expect(screen.getByText("整期节目 · 1 个内容")).toBeTruthy();
  expect(screen.getByRole("button", { name: "01 · 模型变化" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /导读|回顾/ })).toBeNull();
});
it("seeks using the final packaged chapter timing and highlights the playing item", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, status: 200, text: async () => JSON.stringify(url.includes("navigation") ? { items: [{ title: "甲", chapter_indexes: [1] }, { title: "乙", chapter_indexes: [2] }] } : { chapters: [{ id: "brand_intro", title: "品牌", start_ms: 0, end_ms: 2000 }, { id: "c2", title: "甲", start_ms: 7000, end_ms: 17000 }, { id: "c3", title: "乙", start_ms: 17000, end_ms: 27000 }] }) })));
  render(<EpisodePlayer render={movie} episode />);
  const first = await screen.findByRole("button", { name: "01 · 甲 0:07" });
  const second = screen.getByRole("button", { name: "02 · 乙 0:17" });
  const player = screen.getByLabelText("文稿 v1 成片") as HTMLVideoElement;
  fireEvent.click(first); expect(player.currentTime).toBe(7); expect(first.getAttribute("aria-current")).toBe("step");
  player.currentTime = 19; fireEvent.timeUpdate(player); expect(second.getAttribute("aria-current")).toBe("step");
  expect(screen.queryByText("品牌")).toBeNull();
});
it("leaves historical individual videos playable without loading programme files", () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  render(<EpisodePlayer render={movie} episode={false} />);
  expect(screen.getByLabelText("文稿 v1 成片")).toBeTruthy(); expect(fetch).not.toHaveBeenCalled();
});
