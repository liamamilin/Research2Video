import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import type { Publication, VideoRender } from "@/api/video";
import type { UploadTask } from "@/api/platforms";
import { PublicationFlow } from "./PublicationFlow";

afterEach(cleanup);
const video = { id: "r-old", version: 1, files: [{ name: "video.mp4" }] } as VideoRender;
const task = { id: "task-old", version: 1, status: "SUBMITTED", error: "" } as UploadTask;
const publication = { id: "p1", version: 1, url: "https://www.bilibili.com/video/BV1xx411c7mD" } as Publication;

function show(overrides: Partial<React.ComponentProps<typeof PublicationFlow>> = {}) {
  render(<MemoryRouter><PublicationFlow scriptId="s1" version={1} render={video} publications={[]} canEdit {...overrides} /></MemoryRouter>);
}

describe("document publication entry", () => {
  it("opens the exact render in platform management and keeps a document return path", () => {
    show();
    const href = screen.getByRole("link", { name: "投稿到哔哩哔哩" }).getAttribute("href")!;
    const params = new URL(href, "http://localhost").searchParams;
    expect(Object.fromEntries(params)).toEqual({ tab: "uploads", from_script: "s1", version: "1", render: "r-old" });
  });

  it.each(["QUEUED", "UPLOADING", "SUBMITTED", "UNKNOWN", "FAILED", "CANCELLED"])("continues the existing %s task without offering another submission", status => {
    show({ upload: { ...task, status } });
    expect(screen.queryByRole("link", { name: "投稿到哔哩哔哩" })).toBeNull();
    expect(screen.getByRole("link", { name: "查看投稿进度" }).getAttribute("href")).toContain("task=task-old");
  });

  it("shows a public video and the shared record after publication", () => {
    show({ publications: [publication], upload: { ...task, status: "PUBLISHED" } });
    expect(screen.getByRole("link", { name: "查看已发布视频" }).getAttribute("href")).toBe(publication.url);
    expect(screen.queryByRole("link", { name: "投稿到哔哩哔哩" })).toBeNull();
  });

  it("keeps a previous publication from marking another version published", () => {
    show({ version: 2, publications: [publication], render: { ...video, version: 2 } });
    expect(screen.queryByRole("link", { name: "查看已发布视频" })).toBeNull();
    expect(screen.getByRole("link", { name: "投稿到哔哩哔哩" })).toBeTruthy();
  });

  it("requires an available MP4 and respects viewer permissions", () => {
    show({ render: { ...video, files: [] } });
    expect(screen.queryByRole("link", { name: "投稿到哔哩哔哩" })).toBeNull();
    expect(screen.getByText(/成片文件暂不可用/)).toBeTruthy();
    cleanup();
    show({ canEdit: false });
    expect(screen.queryByRole("link", { name: "投稿到哔哩哔哩" })).toBeNull();
  });
});
