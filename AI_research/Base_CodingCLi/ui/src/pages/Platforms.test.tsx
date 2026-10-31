import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { loadPlatforms, loadBilibiliCategories, platformAction, type PlatformOverview } from "@/api/platforms";
import { useAuthStore } from "@/lib/auth-store";
import { PlatformsPage } from "./Platforms";

vi.mock("@/api/platforms", async (original) => ({ ...await original<typeof import("@/api/platforms")>(), loadPlatforms: vi.fn(), loadBilibiliCategories: vi.fn(), platformAction: vi.fn() }));
vi.mock("@/api/client", () => ({ api: vi.fn() }));

let data: PlatformOverview;
const BV = "BV1xx411c7mD";
const row = { bvid: BV, rpid: "102", root: "101", author: "观众甲", message: "如何配置？", ctime: 100, likes: 2, status: "NEW", draft: "", reply: "", reply_rpid: "", error: "" };
const catalog = { categories: [
  { tid: 95, name: "数码", path: "科技 / 数码", description: "数码产品相关内容" },
  { tid: 201, name: "科学科普", path: "知识 / 科学科普", description: "科学知识分享" },
], stale: false, updated_at: 100 };
const preview = { render_id: "r1", title: "第一期", description: "视频介绍", tags: ["科技"], tid: 95, warnings: ["检查参考来源"], credibility: { text: "有出处可信" } };

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  data = { account: { connected: true, name: "测试UP主", uid: "123" }, settings: { auto_upload: false, sync_comments: false, tid: 95, auto_since: 0 }, biliup_ready: true, uploads: [], candidates: [], videos: [{ bvid: BV, title: "第一期", synced_at: null }], comments: [row] };
  data.candidate_summary = { completed_renders: 0, available_versions: 0, published_versions: 0, queued_versions: 0, unavailable_versions: 0, ready_versions: 0 };
  vi.mocked(loadPlatforms).mockImplementation(async () => data);
  vi.mocked(loadBilibiliCategories).mockResolvedValue(catalog);
  vi.mocked(platformAction).mockResolvedValue({});
  useAuthStore.setState({ user: { username: "editor", role: "editor" } as never });
});

afterEach(() => { cleanup(); useAuthStore.setState({ user: null }); vi.useRealTimers(); });

function show(path = "/platforms") {
  return render(<MemoryRouter initialEntries={[path]}><PlatformsPage /></MemoryRouter>);
}

function chooseCategory(label: string, path: string) {
  const input = screen.getByRole("combobox", { name: label });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: path } });
  fireEvent.click(screen.getByRole("option", { name: new RegExp(path) }));
}

describe("Platform management", () => {
  it("shows durable Bilibili recovery notes and checks the original task without reuploading", async () => {
    data.uploads = [{ id: "uncertain", script_id: "s1", render_id: "r1", version: 1,
      status: "UNKNOWN", bvid: "", error: "连接中断", created_at: 100, payload: preview,
      recovery: { lookup_count: 2, note: "已核对最近 3 页稿件，暂未找到匹配；继续自动核对，尚未重新投稿。" } }];
    show();
    expect(await screen.findByText(/已核对最近 3 页稿件/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重新检查并排队" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "立即核对投稿结果" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads/uncertain/lookup"));
    expect(platformAction).toHaveBeenCalledTimes(1);
  });
  it("explains missing upload authorization and links to account settings", async () => {
    data.account.upload_authorized = false;
    show();
    expect(await screen.findByText(/当前账号缺少上传授权/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "账号与策略" }).getAttribute("href")).toBe("/platforms?tab=settings");
    useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
    fireEvent.click(screen.getByRole("tab", { name: "账号与策略" }));
    expect(await screen.findByText(/当前登录可管理评论，但缺少视频上传授权/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新扫码连接" })).toBeTruthy();
    expect(platformAction).not.toHaveBeenCalled();
  });
  it("continues the existing task when reopening a render that is already queued", async () => {
    const task = { id: "queued-task", script_id: "s1", version: 1, render_id: "r1", status: "QUEUED", bvid: "", error: "", created_at: 100, payload: preview };
    vi.mocked(api).mockImplementation(async path => String(path).endsWith("/uploads/queued-task") ? task : { ...preview, existing_upload: task });
    show("/platforms?render=r1");
    expect(await screen.findByText("这个版本已有投稿任务，请继续查看进度。")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "取消排队" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "提交到 B 站" })).toBeNull();
    expect(platformAction).not.toHaveBeenCalled();
  });
  it("focuses an existing document task outside the overview list and returns to its exact version", async () => {
    vi.mocked(api).mockResolvedValue({ id: "old-task", script_id: "s1", version: 1, render_id: "old-render", status: "SUBMITTED", bvid: BV, error: "", created_at: 100, payload: { ...preview, title: "旧版投稿" } });
    show("/platforms?task=old-task&from_script=s1&version=1");
    expect(await screen.findByText("旧版投稿")).toBeTruthy();
    expect(screen.getByRole("link", { name: "← 返回文稿发布页（v1）" }).getAttribute("href")).toBe("/video/s1?tab=publish&publish_version=1");
    expect(screen.getByRole("link", { name: "查看文稿发布记录 →" }).getAttribute("href")).toBe("/video/s1?tab=publish&publish_version=1&render=old-render");
    expect(platformAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "账号与策略" }));
    expect(screen.getByRole("link", { name: "← 返回文稿发布页（v1）" })).toBeTruthy();
  });
  it("shows the Bilibili account, upload readiness and three tabs", async () => {
    show();
    expect(await screen.findByText("哔哩哔哩")).toBeTruthy();
    expect(screen.getByText("测试UP主 · UID 123")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "视频投稿" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "评论收件箱 · 1" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "账号与策略" })).toBeTruthy();
    expect(screen.getByText("投稿组件可用")).toBeTruthy();
  });

  it("routes an unconnected account to connection settings", async () => {
    data.account = { connected: false };
    show();
    fireEvent.click(await screen.findByRole("button", { name: "前往账号连接" }));
    expect(screen.getByText(/账号连接和自动化策略由管理员配置/)).toBeTruthy();
  });

  it("defaults to current approved videos and keeps archived/history search available", async () => {
    data.candidates = [
      { render_id: "current", script_id: "s1", title: "当前标题", version: 3, current_version: 3, approved_version: 3, script_status: "APPROVED", archived: 0, completed_at: "2026-10-24" },
      { render_id: "archived", script_id: "s2", title: "归档标题", version: 1, current_version: 2, approved_version: 1, script_status: "DRAFT", archived: 1, completed_at: "2026-10-23" },
      { render_id: "history", script_id: "s1", title: "历史标题", version: 2, current_version: 3, approved_version: 3, script_status: "APPROVED", archived: 0, completed_at: "2026-10-22" },
    ];
    data.candidate_summary = { completed_renders: 8, available_versions: 5, published_versions: 2, queued_versions: 0, unavailable_versions: 0, ready_versions: 3 };
    show();
    expect(await screen.findByRole("option", { name: "当前标题 · v3" })).toBeTruthy();
    expect((screen.getByLabelText("成片范围") as HTMLSelectElement).value).toBe("current");
    expect(screen.queryByRole("option", { name: /归档标题/ })).toBeNull();
    expect(screen.queryByRole("option", { name: /历史标题/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("成片范围"), { target: { value: "all" } });
    expect(screen.getByRole("option", { name: "归档标题 · v1 · 已归档 · 历史版本，当前 v2" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "历史标题 · v2 · 历史版本，当前 v3" })).toBeTruthy();
    expect(screen.getByText(/已完成 8 次出片，文件可用 5 个版本；已记录发布 2 个/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("成片范围"), { target: { value: "archived" } });
    expect(screen.queryByRole("option", { name: "当前标题 · v3" })).toBeNull();
    expect(screen.getByRole("option", { name: /归档标题/ })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("成片范围"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("搜索投稿成片"), { target: { value: "历史标题" } });
    expect(screen.getByRole("option", { name: /历史标题/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /归档标题/ })).toBeNull();
  });

  it("explains when the running backend still exposes the old incomplete list", async () => {
    delete data.candidate_summary;
    show();
    expect(await screen.findByText(/当前后端尚未加载完整成片列表/)).toBeTruthy();
  });

  it("saves a draft without sending it and sends only on the explicit button", async () => {
    show("/platforms?tab=comments");
    fireEvent.change(await screen.findByLabelText("回复 观众甲"), { target: { value: "打开设置页面。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith(`/comments/${BV}/102`, { draft: "打开设置页面。", handled: false }, "PUT"));
    expect(vi.mocked(platformAction).mock.calls.some(([path]) => path.endsWith("/reply"))).toBe(false);
    await waitFor(() => expect((screen.getByRole("button", { name: "发送回复" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "发送回复" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith(`/comments/${BV}/102/reply`, { text: "打开设置页面。" }));
  });

  it("keeps viewer access read-only and prevents resending an uncertain reply", async () => {
    useAuthStore.setState({ user: { username: "viewer", role: "viewer" } as never });
    show("/platforms?tab=comments");
    await screen.findByText("如何配置？");
    expect(screen.queryByRole("button", { name: "发送回复" })).toBeNull();
    expect(screen.queryByRole("button", { name: "同步最新评论" })).toBeNull();
    cleanup();
    data.comments = [{ ...row, status: "UNKNOWN", reply: "已尝试回复", error: "发送结果未确认" }];
    useAuthStore.setState({ user: { username: "editor", role: "editor" } as never });
    show("/platforms?tab=comments");
    fireEvent.change(await screen.findByLabelText("评论状态"), { target: { value: "" } });
    await screen.findByText("发送结果未确认");
    expect(screen.queryByRole("button", { name: "发送回复" })).toBeNull();
  });

  it("loads a render's publish pack and submits the reviewed metadata", async () => {
    data.candidates = [{ render_id: "r1", script_id: "s1", version: 1, title: "第一期", completed_at: "2026-10-24" }];
    vi.mocked(api).mockResolvedValue({ render_id: "r1", title: "第一期", description: "视频介绍", tags: ["科技"], tid: 95, warnings: ["检查参考来源"], credibility: { text: "有出处可信" } });
    show("/platforms?tab=uploads&render=r1");
    await screen.findByLabelText("标题");
    expect(screen.queryByText("检查参考来源")).toBeNull();
    await waitFor(() => expect((screen.getByLabelText("投稿分区") as HTMLInputElement).value).toBe("科技 / 数码"));
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(platformAction).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "编辑后的标题" } });
    fireEvent.click(screen.getByRole("button", { name: "提交到 B 站" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads", { render_id: "r1", title: "编辑后的标题", description: "视频介绍", tid: 95, tags: ["科技"], first_comment: { enabled: false, text: "", pin: false } }));
  });

  it("submits the ID belonging to the category selected by name", async () => {
    vi.mocked(api).mockResolvedValue(preview);
    show("/platforms?render=r1");
    await screen.findByLabelText("投稿分区");
    await waitFor(() => expect((screen.getByLabelText("投稿分区") as HTMLInputElement).disabled).toBe(false));
    chooseCategory("投稿分区", "知识 / 科学科普");
    fireEvent.click(screen.getByRole("button", { name: "提交到 B 站" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads", expect.objectContaining({ tid: 201 })));
  });

  it("lets the user adopt a content recommendation before submitting its official ID", async () => {
    vi.mocked(api).mockResolvedValue({ ...preview, title: "海床下七百米的坑：远古撞击怎样被还原", description: "北海陨石坑的地质证据。", tags: ["AI", "科普"] });
    show("/platforms?render=r1");
    const button = await screen.findByRole("button", { name: "采用推荐：知识 / 科学科普" });
    expect(screen.getByLabelText("投稿分区")).toHaveProperty("value", "科技 / 数码");
    expect(platformAction).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(screen.getByLabelText("投稿分区")).toHaveProperty("value", "知识 / 科学科普");
    fireEvent.click(screen.getByRole("button", { name: "提交到 B 站" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads", expect.objectContaining({ tid: 201 })));
  });

  it("requires a new choice when the saved category is no longer available", async () => {
    vi.mocked(api).mockResolvedValue({ ...preview, tid: 999 });
    show("/platforms?render=r1");
    await screen.findByText("原分区已不在当前可用列表中，请重新选择。");
    expect((screen.getByRole("button", { name: "提交到 B 站" }) as HTMLButtonElement).disabled).toBe(true);
    expect(platformAction).not.toHaveBeenCalled();
    chooseCategory("投稿分区", "知识 / 科学科普");
    expect((screen.getByRole("button", { name: "提交到 B 站" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("can retry category loading without losing the edited upload fields", async () => {
    vi.mocked(api).mockResolvedValue({ ...preview, tid: 0 });
    vi.mocked(loadBilibiliCategories).mockRejectedValueOnce(new Error("连接失败"));
    show("/platforms?render=r1");
    await screen.findByText("分区读取失败：连接失败");
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "保留编辑内容" } });
    expect((screen.getByRole("button", { name: "提交到 B 站" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "重新获取分区" }));
    await waitFor(() => expect((screen.getByLabelText("投稿分区") as HTMLInputElement).disabled).toBe(false));
    expect((screen.getByLabelText("标题") as HTMLInputElement).value).toBe("保留编辑内容");
  });

  it("saves the default category selected by name and identifies cached lists", async () => {
    useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
    vi.mocked(loadBilibiliCategories).mockResolvedValue({ ...catalog, stale: true });
    show("/platforms?tab=settings");
    await waitFor(() => expect((screen.getByLabelText("默认投稿分区") as HTMLInputElement).value).toBe("科技 / 数码"));
    expect(screen.getByText("分区更新失败，暂显示上次获取的分区。")).toBeTruthy();
    chooseCategory("默认投稿分区", "知识 / 科学科普");
    fireEvent.click(screen.getByRole("button", { name: "保存策略" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/settings", { auto_upload: false, sync_comments: false, tid: 201, first_comment: { enabled: false, text: "", pin: false } }, "PUT"));
  });

  it("shows backend failures without losing the edited reply", async () => {
    vi.mocked(platformAction).mockRejectedValue(new Error("登录已失效，请重新扫码。"));
    show("/platforms?tab=comments");
    fireEvent.change(await screen.findByLabelText("回复 观众甲"), { target: { value: "保存这段回复" } });
    fireEvent.click(screen.getByRole("button", { name: "发送回复" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "登录已失效，请重新扫码。");
    expect((screen.getByLabelText("回复 观众甲") as HTMLTextAreaElement).value).toBe("保存这段回复");
  });

  it("keeps polling an unchanged waiting QR state", async () => {
    useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
    vi.mocked(platformAction).mockImplementation(async path => path === "/login" ? { image: "data:image/png;base64,test" } : { status: "waiting" });
    show("/platforms?tab=settings");
    const button = await screen.findByRole("button", { name: "重新扫码连接" });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(button); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(vi.mocked(platformAction).mock.calls.filter(([path]) => path === "/login/poll")).toHaveLength(2);
  });

  it("includes an edited first comment and its optional pin in the upload only on submit", async () => {
    vi.mocked(api).mockResolvedValue(preview);
    show("/platforms?render=r1");
    fireEvent.click(await screen.findByRole("checkbox", { name: "视频公开后添加首条评论" }));
    expect((screen.getByRole("button", { name: "提交到 B 站" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("首条评论内容"), { target: { value: "本期补充资料\n欢迎交流。" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "置顶这条评论" }));
    expect(platformAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "提交到 B 站" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads", expect.objectContaining({ first_comment: { enabled: true, text: "本期补充资料\n欢迎交流。", pin: true } })));
  });

  it("loads the common comment but allows disabling it for one upload", async () => {
    vi.mocked(api).mockResolvedValue({ ...preview, first_comment: { enabled: true, text: "常用说明", pin: true } });
    show("/platforms?render=r1");
    expect(await screen.findByLabelText("首条评论内容")).toHaveProperty("value", "常用说明");
    fireEvent.click(screen.getByRole("checkbox", { name: "视频公开后添加首条评论" }));
    expect(screen.queryByLabelText("首条评论内容")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "提交到 B 站" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads", expect.objectContaining({ first_comment: expect.objectContaining({ enabled: false }) })));
  });

  it("saves a common comment without uploading or sending it", async () => {
    useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
    vi.mocked(api).mockResolvedValue(preview);
    show("/platforms?render=r1");
    fireEvent.click(await screen.findByRole("checkbox", { name: "视频公开后添加首条评论" }));
    fireEvent.change(screen.getByLabelText("首条评论内容"), { target: { value: "固定说明" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "置顶这条评论" }));
    fireEvent.click(screen.getByRole("button", { name: "保存为常用评论" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/first-comment-template", { enabled: true, text: "固定说明", pin: true }, "PUT"));
    expect(await screen.findByText("已保存，后续投稿默认使用；当前还未发送。")).toBeTruthy();
    expect(vi.mocked(platformAction).mock.calls).toHaveLength(1);
  });

  it("shows successful posting separately from failed pinning and retries the pin only", async () => {
    data.uploads = [{ id: "u1", script_id: "s1", version: 1, render_id: "r1", status: "PUBLISHED", bvid: BV, error: "", created_at: 100,
      payload: { ...preview, first_comment: { enabled: true, text: "固定说明", pin: true } },
      first_comment_status: "SENT", first_comment_rpid: "987654321123456789", first_comment_pin_status: "FAILED", first_comment_pin_error: "B 站接口返回 12029" }];
    show();
    expect(await screen.findByText("首条评论 · 已发送")).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看首条评论" }).getAttribute("href")).toBe(`https://www.bilibili.com/video/${BV}/#reply987654321123456789`);
    expect(screen.queryByRole("button", { name: "重试首条评论" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "仅重试置顶" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/uploads/u1/first-comment/pin/retry"));
  });

  it("never offers to resend an uncertain first comment", async () => {
    data.uploads = [{ id: "u1", script_id: "s1", version: 1, render_id: "r1", status: "PUBLISHED", bvid: BV, error: "", created_at: 100,
      payload: { ...preview, first_comment: { enabled: true, text: "固定说明", pin: false } }, first_comment_status: "UNKNOWN" }];
    show();
    expect(await screen.findByText("首条评论 · 发送结果待核对")).toBeTruthy();
    expect(screen.getByRole("link", { name: "到视频下核对评论" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试首条评论" })).toBeNull();
    expect(screen.queryByRole("button", { name: "仅重试置顶" })).toBeNull();
  });

  it("saves the default first comment with the automation policy", async () => {
    useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
    show("/platforms?tab=settings");
    fireEvent.click(await screen.findByRole("checkbox", { name: "新投稿默认添加首条评论" }));
    fireEvent.change(screen.getByLabelText("常用首条评论内容"), { target: { value: "固定说明" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "默认置顶首条评论" }));
    fireEvent.click(screen.getByRole("button", { name: "保存策略" }));
    await waitFor(() => expect(platformAction).toHaveBeenCalledWith("/settings", expect.objectContaining({ first_comment: { enabled: true, text: "固定说明", pin: true } }), "PUT"));
  });
});
