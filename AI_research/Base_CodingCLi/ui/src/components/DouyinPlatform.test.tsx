import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { douyinAction, loadDouyin, type DouyinOverview, type DouyinJob } from "@/api/douyin";
import { useAuthStore } from "@/lib/auth-store";
import { PlatformsPage } from "@/pages/Platforms";

vi.mock("@/api/douyin", async original => ({ ...await original<typeof import("@/api/douyin")>(), loadDouyin: vi.fn(), douyinAction: vi.fn() }));
vi.mock("@/api/client", () => ({ api: vi.fn() }));
let data: DouyinOverview;
const job: DouyinJob = { id: "job1", kind: "UPLOAD", status: "UNKNOWN", phase: "等待核对", error: "", work_id: "", script_id: "s1", version: 2, render_id: "r1", created_at: 1, updated_at: 1, payload: { title: "待核对视频" }, url: "", can_retry: false };
const preview = { account_uid: "123", render_id: "r1", script_id: "s1", version: 2, title: "原标题", description: "简介", tags: ["科技"], warnings: [] };

beforeEach(() => {
  vi.clearAllMocks();
  data = { account: { connected: false }, runtime_ready: true, login_id: null, candidates: [], uploads: [], checks: [] };
  vi.mocked(loadDouyin).mockImplementation(async () => data);
  vi.mocked(douyinAction).mockResolvedValue({ ...job, id: "login1", kind: "LOGIN" });
  vi.mocked(api).mockResolvedValue({ ...job, id: "login1", kind: "LOGIN", phase: "等待扫码", connected: false, image: "data:image/png;base64,dGVzdA==" });
  useAuthStore.setState({ user: { username: "admin", role: "admin" } as never });
});
afterEach(() => { cleanup(); useAuthStore.setState({ user: null }); });
const show = () => render(<MemoryRouter initialEntries={["/platforms?platform=douyin"]}><PlatformsPage /></MemoryRouter>);
const connect = () => { data.account = { connected: true, uid: "123", name: "测试创作者" }; };

describe("Douyin platform", () => {
  it("opens the Douyin connection page without starting login or uploading", async () => {
    show();
    expect(await screen.findByText("尚未连接账号")).toBeTruthy();
    expect((screen.getByLabelText("选择平台") as HTMLSelectElement).value).toBe("douyin");
    expect(douyinAction).not.toHaveBeenCalled();
  });
  it("starts login explicitly and displays its QR code without posting", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "扫码连接抖音" }));
    expect(await screen.findByAltText("使用抖音 App 扫码连接")).toBeTruthy();
    expect(douyinAction).toHaveBeenCalledWith("/login");
    expect(vi.mocked(douyinAction).mock.calls.some(([path]) => path === "/uploads")).toBe(false);
  });
  it("restricts connection to administrators", async () => {
    useAuthStore.setState({ user: { username: "editor", role: "editor" } as never });
    show();
    expect(await screen.findByText("账号连接由管理员配置。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "扫码连接抖音" })).toBeNull();
  });
  it("keeps uncertain outcomes in reconciliation and never offers automatic retry", async () => {
    connect(); data.uploads = [job]; show();
    expect(await screen.findByText("结果待核对")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重新检查并投稿" })).toBeNull();
    expect(screen.getByRole("link", { name: "查看文稿发布记录 →" }).getAttribute("href")).toContain("publish_version=2");
    fireEvent.change(screen.getByLabelText("核对抖音作品"), { target: { value: "7559876543210123456" } });
    fireEvent.click(screen.getByRole("button", { name: "立即核对一次" }));
    await waitFor(() => expect(douyinAction).toHaveBeenCalledWith("/uploads/job1/reconcile", { video: "7559876543210123456" }));
  });
  it("searches candidates and submits edited metadata for the selected version only on confirmation", async () => {
    connect();
    data.candidates = [
      { render_id: "r1", script_id: "s1", title: "当前成片", version: 2, current_version: 2, approved_version: 2, script_status: "APPROVED", archived: 0, completed_at: "2026-10-25" },
      { render_id: "old", script_id: "s1", title: "历史成片", version: 1, current_version: 2, approved_version: 2, script_status: "APPROVED", archived: 0, completed_at: "2026-10-24" },
    ];
    vi.mocked(api).mockResolvedValue(preview);
    show();
    expect(await screen.findByRole("option", { name: "当前成片 · v2" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "历史成片 · v1" })).toBeNull();
    fireEvent.change(screen.getByLabelText("抖音成片范围"), { target: { value: "all" } });
    expect(screen.getByRole("option", { name: "历史成片 · v1" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索抖音成片"), { target: { value: "当前" } });
    expect(screen.queryByRole("option", { name: "历史成片 · v1" })).toBeNull();
    fireEvent.change(screen.getByLabelText("抖音投稿成片"), { target: { value: "r1" } });
    expect(await screen.findByText("抖音发布包 · 文稿 v2")).toBeTruthy();
    expect(douyinAction).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("抖音标题"), { target: { value: "字".repeat(31) } });
    expect((screen.getByRole("button", { name: "确认发布到抖音" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("抖音标题"), { target: { value: "抖音版标题" } });
    fireEvent.click(screen.getByRole("button", { name: "确认发布到抖音" }));
    await waitFor(() => expect(douyinAction).toHaveBeenCalledWith("/uploads", { render_id: "r1", account_uid: "123", title: "抖音版标题", description: "简介", tags: ["科技"], first_comment: { enabled: false, text: "", pin: false } }));
  });
});


it("opens an exact render from the document return link without posting", async () => {
  connect();
  vi.mocked(api).mockResolvedValue({ ...preview, draft: { payload: { title: "已保存的抖音标题" } } });
  render(<MemoryRouter initialEntries={["/platforms?platform=douyin&render=r1&from_script=s1&version=2"]}><PlatformsPage /></MemoryRouter>);
  expect(await screen.findByLabelText("抖音标题")).toHaveProperty("value", "已保存的抖音标题");
  expect(screen.getByRole("link", { name: "← 返回文稿发布页（v2）" }).getAttribute("href")).toBe("/video/s1?tab=publish&publish_version=2");
  expect(douyinAction).not.toHaveBeenCalled();
});


it("captures an optional author comment and disables unsupported pinning", async () => {
  connect();
  vi.mocked(api).mockResolvedValue({ ...preview, first_comment: { enabled: true, text: "模板\n\nhttps://example.test", pin: false } });
  render(<MemoryRouter initialEntries={["/platforms?platform=douyin&render=r1"]}><PlatformsPage /></MemoryRouter>);
  const form = within(await screen.findByRole("form", { name: "抖音发布包" }));
  expect(form.getByLabelText("作者评论内容")).toHaveProperty("value", "模板\n\nhttps://example.test");
  expect(form.getByLabelText("自动置顶（抖音暂不支持）")).toHaveProperty("disabled", true);
  fireEvent.change(form.getByLabelText("作者评论内容"), { target: { value: "仅这条作品的评论" } });
  fireEvent.click(form.getByRole("button", { name: "确认发布到抖音" }));
  await waitFor(() => expect(douyinAction).toHaveBeenCalledWith("/uploads", expect.objectContaining({ first_comment: { enabled: true, text: "仅这条作品的评论", pin: false } })));
});

it("adds one comment to an existing submitted work and explains automatic verification", async () => {
  connect(); data.uploads = [{ ...job, status: "SUBMITTED", work_id: "7559876543210123456", verification: { automatic: true, count: 3, status: "RECORDED", reason: "尚未确认公开" } }];
  show();
  expect(await screen.findByText(/自动核对已启用/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "添加作者评论" }));
  const form = within(screen.getByRole("form", { name: "为已有抖音作品添加作者评论" }));
  expect(form.getByRole("button", { name: "确认安排这条作者评论" })).toHaveProperty("disabled", true);
  fireEvent.change(form.getByLabelText("作者评论内容"), { target: { value: "只发送一次\n\n链接" } });
  fireEvent.click(form.getByRole("button", { name: "确认安排这条作者评论" }));
  await waitFor(() => expect(douyinAction).toHaveBeenCalledWith("/uploads/job1/first-comment", { enabled: true, text: "只发送一次\n\n链接", pin: false }));
});

it("never offers retry for an uncertain comment and only offers read-only verification", async () => {
  connect(); data.uploads = [{ ...job, status: "PUBLISHED", work_id: "7559876543210123456", first_comment: { id: "comment1", status: "UNKNOWN", phase: "待核对", error: "请求超时", text: "评论", comment_id: "", read_back: false, can_retry: false, created_at: 1, updated_at: 1 } }];
  show();
  expect(await screen.findByText("作者评论 · 结果待核对，不会自动重发")).toBeTruthy();
  expect(screen.getByText("已公开")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "重新检查并发送评论" })).toBeNull();
  expect(screen.queryByRole("button", { name: "添加作者评论" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "只读核对评论" }));
  await waitFor(() => expect(douyinAction).toHaveBeenCalledWith("/uploads/job1/first-comment/check"));
  expect(douyinAction).toHaveBeenCalledTimes(1);
});

it("saves an account template explicitly without enqueueing old works", async () => {
  connect(); show();
  fireEvent.click(await screen.findByText("常用作者评论设置"));
  fireEvent.click(screen.getByLabelText("新投稿默认添加作者评论"));
  fireEvent.change(screen.getByLabelText("常用作者评论内容"), { target: { value: "默认评论" } });
  fireEvent.click(screen.getByRole("button", { name: "保存抖音常用评论" }));
  await waitFor(() => expect(douyinAction).toHaveBeenCalledWith("/first-comment-template", { enabled: true, text: "默认评论", pin: false }, "PUT"));
  expect(douyinAction).toHaveBeenCalledTimes(1);
});
