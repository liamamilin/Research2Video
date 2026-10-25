import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Distribution, Publication, VideoRender } from "@/api/video";
import { api } from "@/api/client";
import { PublicationFlow } from "./PublicationFlow";

vi.mock("@/api/client", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const video = { id: "r-old", version: 1, files: [{ name: "video.mp4" }] } as VideoRender;
const publication = { id: "p1", version: 1, platform: "bilibili", url: "https://www.bilibili.com/video/BV1xx411c7mD", account_uid: "123", recorded_by: "platform-manager" } as Publication;
function target(platform: string, overrides: Partial<Distribution> = {}): Distribution {
  return { platform, script_id: "s1", version: 1, account_uid: platform === "bilibili" ? "123" : "456", account_name: platform === "bilibili" ? "B站账号" : "抖音账号", current_account: true, connected: true,
    status: "UNPUBLISHED", can_prepare: true, blocked_reason: "", task: null, draft: null, publications: [], ...overrides };
}
function show(overrides: Partial<React.ComponentProps<typeof PublicationFlow>> = {}) {
  return render(<MemoryRouter><PublicationFlow scriptId="s1" version={1} render={video} publications={[]} distributions={[target("bilibili"), target("douyin")]} canEdit {...overrides} /></MemoryRouter>);
}

describe("multi-platform document publishing", () => {
  it("keeps Douyin preparation after Bilibili publication and opens its editor inline", async () => {
    vi.mocked(api).mockResolvedValue({ render_id: "r-old", script_id: "s1", version: 1, account_uid: "456", title: "抖音标题", description: "简介", tags: ["科技"], warnings: [] });
    show({ publications: [publication], distributions: [target("bilibili", { status: "PUBLISHED", can_prepare: false, publications: [publication] }), target("douyin")] });
    expect(screen.getByText(/已发布至 1 个平台/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看作品" }).getAttribute("href")).toBe(publication.url);
    expect(screen.queryByRole("button", { name: "准备B 站发布" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "准备抖音发布" }));
    expect(await screen.findByRole("form", { name: "抖音发布包" })).toBeTruthy();
    expect(api).toHaveBeenCalledWith("/api/platforms/douyin/preview/r-old", expect.anything());
    fireEvent.change(screen.getByLabelText("抖音标题"), { target: { value: "独立修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存抖音发布包" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/video/distributions/douyin/draft", expect.objectContaining({ method: "PUT", body: { render_id: "r-old", account_uid: "456", payload: { title: "独立修改", description: "简介", tags: ["科技"], first_comment: { enabled: false, text: "", pin: false } } } })));
    expect(screen.getByRole("link", { name: "查看作品" }).getAttribute("href")).toBe(publication.url);
  });
  it.each(["QUEUED", "UPLOADING", "SUBMITTED", "UNKNOWN", "FAILED"])("keeps %s scoped to its target without a duplicate action", status => {
    show({ distributions: [target("bilibili"), target("douyin", { status, can_prepare: false, task: { id: "task", status, error: "原因", work_id: "7700436961890209034", render_id: "r-old", updated_at: 1 } })] });
    expect(screen.getByRole("button", { name: "准备B 站发布" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "准备抖音发布" })).toBeNull();
    const href = screen.getByRole("link", { name: "查看投稿进度" }).getAttribute("href")!;
    expect(Object.fromEntries(new URL(href, "http://localhost").searchParams)).toEqual({ tab: "uploads", platform: "douyin", from_script: "s1", version: "1", task: "task" });
  });
  it("never mistakes a Douyin record for Bilibili publication", () => {
    const dy = { ...publication, platform: "douyin", url: "https://www.douyin.com/video/7700436961890209034" };
    show({ publications: [dy], distributions: [target("bilibili"), target("douyin", { status: "PUBLISHED", publications: [dy], can_prepare: false })] });
    expect(within(screen.getByRole("row", { name: "B 站发布目标" })).getByText("未发布")).toBeTruthy();
    expect(screen.getByRole("button", { name: "准备B 站发布" })).toBeTruthy();
  });
  it("keeps old versions and disconnected accounts separate", () => {
    show({ version: 2, render: { ...video, version: 2 }, distributions: [target("bilibili", { status: "PUBLISHED", publications: [publication], can_prepare: false }), target("bilibili", { version: 2 }), target("douyin", { version: 2, connected: false, current_account: false, account_uid: "", account_name: "", can_prepare: false })] });
    expect(screen.queryByRole("link", { name: "查看作品" })).toBeNull();
    expect(screen.getByRole("button", { name: "准备B 站发布" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "连接账号" }).getAttribute("href")).toContain("platform=douyin");
  });
  it("requires an available MP4 and editor permissions", () => {
    show({ render: { ...video, files: [] } });
    expect(screen.queryByRole("button", { name: "准备抖音发布" })).toBeNull();
    expect(screen.getByText(/成片文件暂不可用/)).toBeTruthy(); cleanup();
    show({ canEdit: false });
    expect(screen.queryByRole("button", { name: "准备抖音发布" })).toBeNull();
  });
});
