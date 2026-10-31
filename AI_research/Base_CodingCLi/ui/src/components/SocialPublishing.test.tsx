import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import type { SocialDefinition, SocialJob, SocialOverview, SocialPreview } from "@/api/social-publishing";
import type { Distribution, Publication, VideoRender } from "@/api/video";
import { PlatformsPage } from "@/pages/Platforms";
import { PublicationFlow } from "./PublicationFlow";
import { SocialPlatform } from "./SocialPublishing";

const auth = vi.hoisted(() => ({ role: "admin" }));
vi.mock("@/api/client", () => ({ api: vi.fn() }));
vi.mock("@/lib/auth-store", () => ({
  useAuthStore: (select: (state: { user: { username: string; role: string } }) => unknown) =>
    select({ user: { username: "test-user", role: auth.role } }),
}));

const PLATFORMS = [
  ["kuaishou", "快手"], ["xiaohongshu", "小红书"], ["weixin_channels", "微信视频号"],
] as const;
const ACTIVE_PLATFORMS = PLATFORMS.filter(([id]) => id !== "weixin_channels");
type Platform = typeof PLATFORMS[number][0];
const ACCOUNT = "verified-owner";
const RENDER = "render-exact-v3";
const SCRIPT = "script-exact";
const QR = "data:image/png;base64,dGVzdA==";
let overviews: Record<Platform, SocialOverview>;
let previews: Record<Platform, SocialPreview>;
let drafts: Record<string, SocialPreview["draft"]>;
let focused: Record<string, SocialJob>;
let pendingTask: Promise<SocialJob> | undefined;

function platformMap<T>(make: (platform: Platform, name: string) => T): Record<Platform, T> {
  return { kuaishou: make(...PLATFORMS[0]), xiaohongshu: make(...PLATFORMS[1]),
    weixin_channels: make(...PLATFORMS[2]) };
}

function definition(platform: Platform): SocialDefinition {
  const name = PLATFORMS.find(([id]) => id === platform)![1];
  return {
    id: platform, name, creator_url: `https://creator.${platform}.example/`,
    title_min: platform === "weixin_channels" ? 6 : 1,
    title_limit: platform === "kuaishou" ? 80 : platform === "xiaohongshu" ? 20 : 16,
    description_limit: platform === "kuaishou" ? 500 : 1000,
    tag_limit: platform === "kuaishou" ? 3 : 10,
    tag_length_limit: platform === "kuaishou" ? 30 : 100,
    combined_limit: platform === "kuaishou" ? 500 : platform === "weixin_channels" ? 1000 : null,
    public_check_gap: "审核和公开状态尚未核实，请在创作中心确认。",
    ownership_check: platform === "kuaishou", acceptance: "awaiting_account",
    capabilities: { connect: true, upload: true, public_check: false, first_comment: false,
      pin_comment: false, analytics: false },
  };
}

function job(platform: Platform, overrides: Partial<SocialJob> = {}): SocialJob {
  return {
    id: `${platform}-task`, kind: "UPLOAD", platform, status: "UNKNOWN", phase: "等待人工核对",
    error: "", work_id: "", url: "", script_id: SCRIPT, version: 3, render_id: RENDER,
    created_at: 1, updated_at: 2, can_retry: false, payload: { title: `${platform}待核对作品` },
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前页面地址">{location.pathname}{location.search}</output>;
}

function show(platform: Platform = "xiaohongshu", query = "") {
  return render(<MemoryRouter initialEntries={[`/platforms?platform=${platform}${query}`]}>
    {/* Retain standalone editor contracts for the deferred adapter. */}
    {platform === "weixin_channels" ? <SocialPlatform platform={platform} /> : <PlatformsPage />}<LocationProbe />
  </MemoryRouter>);
}

it("warns that an expired login paused checks and reconnection is needed", async () => {
  connect("kuaishou");
  overviews.kuaishou.definition.capabilities.public_check = true;
  overviews.kuaishou.account = { ...overviews.kuaishou.account, session: { state: "expired", failures: 5, since: 1 } };
  show("kuaishou");
  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText("快手登录已失效")).toBeTruthy();
  expect(within(alert).getByText(/连续 5 次账号核实失败/)).toBeTruthy();
  expect(screen.getByText("登录核实失败 · 自动核对已暂停")).toBeTruthy();
  expect(screen.queryByText("账号已核验 · 自动核对已开启")).toBeNull();
  expect(writes()).toEqual([]);
});

it("preserves the original upload and shows paused verification instead of automatic checking", async () => {
  connect("kuaishou");
  overviews.kuaishou.definition.capabilities.public_check = true;
  overviews.kuaishou.account.session = { state: "expired", failures: 2, since: 1 };
  overviews.kuaishou.uploads = [job("kuaishou", { status: "SUBMITTED", work_id: "original-work",
    verification: { automatic: false, paused: true, pause_reason: "平台登录核实失败，自动核对已暂停；原投稿保留。" },
    progress: { label: "已投稿 · 自动核对暂停", state: "waiting", active: false, needs_attention: true } })];
  show("kuaishou");
  expect(await screen.findByText("已投稿 · 自动核对暂停")).toBeTruthy();
  expect(screen.getByText("平台登录核实失败，自动核对已暂停；原投稿保留。")).toBeTruthy();
  expect(screen.getByText("作品 ID：original-work")).toBeTruthy();
  expect(screen.queryByText(/自动核对已开启，确认公开/)).toBeNull();
  expect(screen.queryByText(/平台已接受投稿，正在自动关联/)).toBeNull();
  expect(writes()).toEqual([]);
});

it("does not present an unknown login as verified", async () => {
  connect("kuaishou");
  overviews.kuaishou.account.session = { state: "unknown", failures: 0, since: null };
  show("kuaishou");
  expect(await screen.findByText("账号已连接 · 登录状态待核实")).toBeTruthy();
  expect(writes()).toEqual([]);
});

it("offers connection recovery while keeping checking tied to the original upload", async () => {
  overviews.xiaohongshu.definition.capabilities.public_check = true;
  overviews.xiaohongshu.uploads = [job("xiaohongshu", { status: "SUBMITTED", work_id: "6ade22190000000033018ae8",
    progress: {label:"回查暂不可用",state:"waiting",active:false,needs_attention:true,
      recovery:{kind:"connection",label:"检查平台连接",href:"/platforms?platform=xiaohongshu&task=xiaohongshu-task",
        message:"请恢复原账号登录后继续核对。",resumable:true}} })];
  show();
  expect(await screen.findByText("请恢复原账号登录后继续核对。")).toBeTruthy();
  expect(screen.getByRole("link", {name:"检查平台连接"}).getAttribute("href")).toBe("#platform-connection");
  expect(writes()).toEqual([]);
  fireEvent.click(screen.getByRole("button", {name:"核对发布结果"}));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/xiaohongshu/uploads/xiaohongshu-task/reconcile", {method:"POST",body:{video:""}}));
  expect(writes()).toHaveLength(1);
});

function connect(platform: Platform) {
  overviews[platform].account = { connected: true, uid: ACCOUNT, name: `${definition(platform).name}测试账号` };
}

function writes() {
  return vi.mocked(api).mock.calls.filter(([, options]) => options?.method && options.method !== "GET");
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  auth.role = "admin";
  focused = {};
  drafts = {};
  pendingTask = undefined;
  overviews = platformMap<SocialOverview>(platform => ({
    definition: definition(platform), account: { connected: false }, runtime_ready: true,
    login_id: null, uploads: [], checks: [], candidates: [],
  }));
  previews = platformMap<SocialPreview>((platform, name) => ({
    definition: definition(platform), account_uid: ACCOUNT, render_id: RENDER, script_id: SCRIPT,
    version: 3, title: `${name}版视频标题`, description: `${name}独立简介`, tags: ["科技"], warnings: [],
  }));
  vi.mocked(api).mockImplementation(async (path, options) => {
    const draft = path.match(/^\/api\/video\/distributions\/([^/]+)\/draft$/);
    if (draft && options?.method === "PUT") {
      const body = options.body as { render_id: string; payload: NonNullable<SocialPreview["draft"]>["payload"] };
      drafts[`${draft[1]}:${body.render_id}`] = { payload: body.payload, render_id: body.render_id };
      return { saved: true };
    }
    const matched = path.match(/^\/api\/platforms\/social\/(kuaishou|xiaohongshu|weixin_channels)(.*)$/);
    if (!matched) throw new Error(`Unexpected test API: ${path}`);
    const platform = matched[1] as Platform;
    const tail = matched[2];
    if (!tail) return overviews[platform];
    if (tail === "/first-comment-template" && options?.method === "PUT") return options.body;
    if (tail.startsWith("/preview/")) {
      const renderId = decodeURIComponent(tail.slice("/preview/".length));
      return { ...previews[platform], render_id: renderId, draft: drafts[`${platform}:${renderId}`] ?? null };
    }
    if (tail === "/login" && options?.method === "POST") {
      return job(platform, { id: `${platform}-login`, kind: "LOGIN", status: "RUNNING" });
    }
    if (tail === "/comments/login" && options?.method === "POST") {
      return job(platform,{id:`${platform}-comment-login`,kind:"LOGIN",status:"RUNNING",login_purpose:"comments"});
    }
    if (tail.startsWith("/login/") && options?.method !== "POST") {
      return { ...job(platform, { id: tail.slice(7), kind: "LOGIN", status: "RUNNING", phase: "等待扫码确认" }),
        ...(tail.endsWith("comment-login") ? {login_purpose:"comments"} : {}),connected: false, image: QR };
    }
    if (tail.startsWith("/uploads/") && options?.method !== "POST") {
      return pendingTask ?? focused[`${platform}:${tail.slice(9)}`] ?? job(platform);
    }
    if (tail.endsWith("/confirm-absent") && options?.method === "POST") {
      const existing = overviews[platform].uploads[0];
      const updated = { ...existing, status: "FAILED", can_retry: true, can_confirm_absent: false, updated_at: 3 };
      overviews[platform].uploads = [updated];
      return updated;
    }
    if (options?.method === "POST" || options?.method === "DELETE") return job(platform, { status: "QUEUED" });
    throw new Error(`Unexpected test API: ${path}`);
  });
});

describe("Kuaishou first comments", () => {
  function enable() {
    connect("kuaishou");
    overviews.kuaishou.definition.capabilities.first_comment=true;
    previews.kuaishou.definition.capabilities.first_comment=true;
  }

  it("loads an account template and snapshots edited opt-in only on explicit submission", async () => {
    enable();
    previews.kuaishou.first_comment={enabled:true,text:"常用说明",pin:false};
    show("kuaishou",`&render=${RENDER}`);
    const form=within(await screen.findByRole("form",{name:"快手发布包"}));
    expect(form.getByLabelText("首条评论内容")).toHaveProperty("value","常用说明");
    expect(form.getByLabelText("自动置顶（快手待接入）")).toHaveProperty("disabled",true);
    fireEvent.change(form.getByLabelText("首条评论内容"),{target:{value:"这条视频的补充资料"}});
    expect(writes()).toEqual([]);
    fireEvent.click(form.getByRole("button",{name:"提交到快手"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/kuaishou/uploads",expect.objectContaining({body:expect.objectContaining({first_comment:{enabled:true,text:"这条视频的补充资料",pin:false}})})));
    expect(writes()).toHaveLength(1);
  });

  it("saves a per-video comment in a draft without posting a video or comment", async () => {
    enable();show("kuaishou",`&render=${RENDER}`);
    const form=within(await screen.findByRole("form",{name:"快手发布包"}));
    fireEvent.click(form.getByLabelText("视频公开后添加首条评论"));
    expect(form.getByRole("button",{name:"提交到快手"})).toHaveProperty("disabled",true);
    fireEvent.change(form.getByLabelText("首条评论内容"),{target:{value:"补充资料"}});
    fireEvent.click(form.getByRole("button",{name:"保存草稿"}));
    await waitFor(() => expect(drafts[`kuaishou:${RENDER}`]?.payload.first_comment).toEqual({enabled:true,text:"补充资料",pin:false}));
    expect(writes().every(([,options]) => options?.method==="PUT")).toBe(true);
  });

  it("exposes only read-only checking for uncertain comments and preserves video publication", async () => {
    enable();overviews.kuaishou.uploads=[job("kuaishou",{status:"PUBLISHED",work_id:"3xexactwork",first_comment:{id:"child",status:"UNKNOWN",phase:"结果待核对",error:"请求超时",text:"补充资料",comment_id:"",read_back:false,can_retry:false}})];
    show("kuaishou");
    const section=within(await screen.findByLabelText("快手首评进度"));
    expect(section.queryByRole("button",{name:"重新检查并发送首评"})).toBeNull();
    expect(screen.getByText("已发布")).toBeTruthy();
    expect(writes()).toEqual([]);
    fireEvent.click(section.getByRole("button",{name:"只读核对评论"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/kuaishou/uploads/kuaishou-task/first-comment/check",{method:"POST",body:undefined}));
    expect(writes()).toHaveLength(1);
  });

  it("shows explicit add for an existing video without silently sending a template", async () => {
    enable();overviews.kuaishou.uploads=[job("kuaishou",{status:"PUBLISHED",work_id:"3xexactwork"})];
    show("kuaishou");
    fireEvent.click(await screen.findByRole("button",{name:"添加首条评论"}));
    const form=within(screen.getByRole("form",{name:"为已有快手作品添加首评"}));
    fireEvent.change(form.getByLabelText("首条评论内容"),{target:{value:"仅发一次"}});
    expect(writes()).toEqual([]);
    fireEvent.click(form.getByRole("button",{name:"确认安排首评"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/kuaishou/uploads/kuaishou-task/first-comment",{method:"POST",body:{enabled:true,text:"仅发一次",pin:false}}));
  });
});

afterEach(() => cleanup());

describe("Xiaohongshu author comments", () => {
  function enable() {
    connect("xiaohongshu");
    overviews.xiaohongshu.definition.capabilities.first_comment=true;
    previews.xiaohongshu.definition.capabilities.first_comment=true;
  }
  it("uses Xiaohongshu labels, opt-in text and an isolated account template", async () => {
    enable();previews.xiaohongshu.first_comment={enabled:true,text:"小红书说明",pin:false};
    show("xiaohongshu",`&render=${RENDER}`);
    const form=within(await screen.findByRole("form",{name:"小红书发布包"}));
    expect(form.getByLabelText("首条评论内容")).toHaveProperty("value","小红书说明");
    expect(form.getByLabelText("自动置顶（本期不做）")).toHaveProperty("disabled",true);
    expect(form.queryByText(/快手/)).toBeNull();
    fireEvent.change(form.getByLabelText("首条评论内容"),{target:{value:"仅用于这条小红书"}});
    expect(writes()).toEqual([]);
    fireEvent.click(form.getByRole("button",{name:"保存为小红书常用评论"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/xiaohongshu/first-comment-template",{method:"PUT",body:{enabled:true,text:"仅用于这条小红书",pin:false}}));
    expect(writes().some(([path]) => path.endsWith("/uploads"))).toBe(false);
  });
  it("persists a per-video draft snapshot and submits it only after confirmation", async () => {
    enable();show("xiaohongshu",`&render=${RENDER}`);
    const form=within(await screen.findByRole("form",{name:"小红书发布包"}));
    fireEvent.click(form.getByLabelText("视频公开后添加首条评论"));
    expect(form.getByRole("button",{name:"提交到小红书"})).toHaveProperty("disabled",true);
    fireEvent.change(form.getByLabelText("首条评论内容"),{target:{value:"资料补充"}});
    fireEvent.click(form.getByRole("button",{name:"保存草稿"}));
    await waitFor(() => expect(drafts[`xiaohongshu:${RENDER}`]?.payload.first_comment).toEqual({enabled:true,text:"资料补充",pin:false}));
    expect(writes().some(([path]) => path.endsWith("/uploads"))).toBe(false);
    fireEvent.click(form.getByRole("button",{name:"提交到小红书"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/xiaohongshu/uploads",expect.objectContaining({body:expect.objectContaining({first_comment:{enabled:true,text:"资料补充",pin:false}})})));
  });
  it("connects the comment login explicitly and renews the same login purpose", async () => {
    enable();show("xiaohongshu");
    fireEvent.click(await screen.findByRole("button",{name:"连接评论登录"}));
    await screen.findByAltText("使用平台 App 扫码连接账号");
    expect(writes()).toEqual([["/api/platforms/social/xiaohongshu/comments/login",{method:"POST",body:undefined}]]);
    fireEvent.click(screen.getByRole("button",{name:"重新获取二维码"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/xiaohongshu/comments/login/renew",{method:"POST",body:undefined}));
    expect(writes().some(([path]) => path.endsWith("/uploads"))).toBe(false);
  });
  it("does not expose comment account mutation to editors", async () => {
    enable();auth.role="editor";show("xiaohongshu");
    await screen.findByText(/账号已核验/);
    expect(screen.queryByRole("button",{name:"连接评论登录"})).toBeNull();
    expect(writes()).toEqual([]);
  });
  it("shows uncertain comments with read-only checking and no resend", async () => {
    enable();overviews.xiaohongshu.uploads=[job("xiaohongshu",{status:"PUBLISHED",work_id:"note-id",first_comment:{id:"child",status:"UNKNOWN",phase:"自动回查，不重发",error:"",text:"资料补充",comment_id:"",read_back:false,can_retry:false}})];
    show("xiaohongshu");
    const section=within(await screen.findByLabelText("小红书首评进度"));
    expect(section.getByText("自动回查，不重发")).toBeTruthy();
    expect(section.queryByRole("button",{name:"重新检查并发送首评"})).toBeNull();
    fireEvent.click(section.getByRole("button",{name:"只读核对评论"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/xiaohongshu/uploads/xiaohongshu-task/first-comment/check",{method:"POST",body:undefined}));
  });
});

describe("new platform account and read-only navigation", () => {
  it.each(ACTIVE_PLATFORMS)("opens %s without automatic login, upload, or comments", async (platform, name) => {
    show(platform);
    expect(await screen.findByText("尚未连接账号")).toBeTruthy();
    expect(screen.getByLabelText("选择平台")).toHaveProperty("value", platform);
    expect(screen.getByRole("button", { name: `扫码连接${name}` })).toBeTruthy();
    expect(writes()).toEqual([]);
    expect(vi.mocked(api).mock.calls.every(([path]) => path === `/api/platforms/social/${platform}`)).toBe(true);
  });

  it.each(ACTIVE_PLATFORMS)("starts %s QR login only after the administrator clicks", async (platform, name) => {
    show(platform);
    fireEvent.click(await screen.findByRole("button", { name: `扫码连接${name}` }));
    expect(await screen.findByAltText("使用平台 App 扫码连接账号")).toHaveProperty("src", QR);
    expect(writes()).toEqual([[`/api/platforms/social/${platform}/login`, { method: "POST", body: undefined }]]);
    expect(screen.getByText("等待扫码确认")).toBeTruthy();
    expect(vi.mocked(api).mock.calls.some(([path]) => path.endsWith("/uploads"))).toBe(false);
  });

  it.each(["editor", "viewer"])("does not expose account mutation to %s", async role => {
    auth.role = role;
    connect("xiaohongshu");
    overviews.xiaohongshu.login_id = "existing-login";
    show();
    expect(await screen.findByText("账号连接由管理员配置。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /扫码连接|验证平台登录|断开平台账号|重新获取二维码/ })).toBeNull();
    expect(screen.queryByAltText("使用平台 App 扫码连接账号")).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("lets viewers inspect existing results but offers no publishing editor", async () => {
    auth.role = "viewer";
    connect("xiaohongshu");
    overviews.xiaohongshu.uploads = [job("xiaohongshu")];
    show("xiaohongshu", `&render=${RENDER}`);
    expect(await screen.findByText("结果待核对")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "小红书发布包" })).toBeNull();
    expect(screen.queryByLabelText("小红书投稿成片")).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("disables connection if the isolated runtime is unavailable", async () => {
    overviews.xiaohongshu.runtime_ready = false;
    show();
    expect(await screen.findByRole("button", { name: "扫码连接小红书" })).toHaveProperty("disabled", true);
    expect(screen.getByText("本机上传组件尚未准备好。")).toBeTruthy();
    expect(writes()).toEqual([]);
  });

  it("clears old task routing and ignores a late task response after switching platform", async () => {
    connect("kuaishou"); connect("xiaohongshu");
    let resolveTask!: (value: SocialJob) => void;
    pendingTask = new Promise(resolve => { resolveTask = resolve; });
    show("kuaishou", "&task=kuaishou-task&tab=uploads");
    expect(await screen.findByRole("heading", { name: "当前投稿任务" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("选择平台"), { target: { value: "xiaohongshu" } });
    expect(await screen.findByRole("heading", { name: "小红书账号连接" })).toBeTruthy();
    expect(screen.getByLabelText("当前页面地址").textContent).not.toContain("task=");
    expect(screen.getByLabelText("当前页面地址").textContent).not.toContain("tab=");
    await act(async () => resolveTask(job("kuaishou", { payload: { title: "不得串到小红书的快手任务" } })));
    expect(screen.queryByText("不得串到小红书的快手任务")).toBeNull();
    expect(vi.mocked(api).mock.calls.some(([path]) => path === "/api/platforms/social/xiaohongshu/uploads/kuaishou-task")).toBe(false);
    expect(writes()).toEqual([]);
  });

  it("does not reuse another platform's QR login ID or screenshot", async () => {
    show("kuaishou");
    fireEvent.click(await screen.findByRole("button", { name: "扫码连接快手" }));
    expect(await screen.findByAltText("使用平台 App 扫码连接账号")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("选择平台"), { target: { value: "xiaohongshu" } });
    await screen.findByRole("heading", { name: "小红书账号连接" });
    expect(screen.queryByAltText("使用平台 App 扫码连接账号")).toBeNull();
    expect(vi.mocked(api).mock.calls.some(([path]) => path.includes("/xiaohongshu/login/kuaishou-login"))).toBe(false);
    expect(writes()).toHaveLength(1);
  });
});

describe("platform-specific finished videos and publish packages", () => {
  it("defaults to finalized current versions and searches historical and archived videos explicitly", async () => {
    connect("xiaohongshu");
    overviews.xiaohongshu.candidates = [
      { render_id: RENDER, script_id: SCRIPT, title: "当前视频", version: 3, current_version: 3, approved_version: 3, script_status: "APPROVED", archived: 0, completed_at: "2026-10-25" },
      { render_id: "old-render", script_id: SCRIPT, title: "历史视频", version: 1, current_version: 3, approved_version: 3, script_status: "APPROVED", archived: 0, completed_at: "2026-10-24" },
      { render_id: "archived-render", script_id: "archived", title: "归档视频", version: 2, current_version: 2, approved_version: 2, script_status: "APPROVED", archived: 1, completed_at: "2026-10-24" },
      { render_id: "draft-render", script_id: "draft", title: "尚未定稿", version: 2, current_version: 2, approved_version: 1, script_status: "DRAFT", archived: 0, completed_at: "2026-10-24" },
    ];
    show();
    expect(await screen.findByRole("option", { name: "当前视频 · v3" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "历史视频 · v1" })).toBeNull();
    expect(screen.queryByRole("option", { name: "归档视频 · v2 · 已归档" })).toBeNull();
    expect(screen.queryByRole("option", { name: "尚未定稿 · v2" })).toBeNull();
    fireEvent.change(screen.getByLabelText("小红书成片范围"), { target: { value: "all" } });
    expect(screen.getByRole("option", { name: "历史视频 · v1" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "归档视频 · v2 · 已归档" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("小红书搜索成片"), { target: { value: "历史" } });
    expect(screen.getByRole("option", { name: "历史视频 · v1" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "当前视频 · v3" })).toBeNull();
    expect(writes()).toEqual([]);
  });

  it.each(ACTIVE_PLATFORMS)("opens exact %s render/version from its source document without publishing", async (platform, name) => {
    connect(platform);
    show(platform, `&render=${RENDER}&from_script=${SCRIPT}&version=3`);
    expect(await screen.findByRole("form", { name: `${name}发布包` })).toBeTruthy();
    expect(api).toHaveBeenCalledWith(`/api/platforms/social/${platform}/preview/${RENDER}`, expect.anything());
    expect(screen.getByRole("link", { name: "← 返回文稿发布页（v3）" }).getAttribute("href"))
      .toBe(`/video/${SCRIPT}?tab=publish&publish_version=3&render=${RENDER}`);
    expect(writes()).toEqual([]);
  });

  it("saves each platform draft independently and restores its own edits on return", async () => {
    connect("xiaohongshu"); connect("kuaishou");
    show("xiaohongshu", `&render=${RENDER}`);
    fireEvent.change(await screen.findByLabelText("小红书标题"), { target: { value: "小红书独立编辑" } });
    fireEvent.change(screen.getByLabelText("小红书简介"), { target: { value: "这段只用于小红书" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByText("已保存本平台草稿");
    expect(api).toHaveBeenCalledWith("/api/video/distributions/xiaohongshu/draft", {
      method: "PUT", body: { render_id: RENDER, account_uid: ACCOUNT,
        payload: { title: "小红书独立编辑", description: "这段只用于小红书", tags: ["科技"] } },
    });
    fireEvent.change(screen.getByLabelText("选择平台"), { target: { value: "kuaishou" } });
    expect(await screen.findByLabelText("快手标题")).toHaveProperty("value", "快手版视频标题");
    fireEvent.change(screen.getByLabelText("快手标题"), { target: { value: "快手单独标题" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/video/distributions/kuaishou/draft", expect.objectContaining({ method: "PUT" })));
    fireEvent.change(screen.getByLabelText("选择平台"), { target: { value: "xiaohongshu" } });
    expect(await screen.findByLabelText("小红书标题")).toHaveProperty("value", "小红书独立编辑");
    expect(screen.getByLabelText("小红书简介")).toHaveProperty("value", "这段只用于小红书");
    expect(writes().every(([, options]) => options?.method === "PUT")).toBe(true);
  });

  it.each(ACTIVE_PLATFORMS)("enqueues %s only on explicit confirmation with edited metadata", async (platform, name) => {
    auth.role = "editor";
    connect(platform);
    show(platform, `&render=${RENDER}`);
    fireEvent.change(await screen.findByLabelText(`${name}标题`), { target: { value: "这是明确确认的标题" } });
    fireEvent.change(screen.getByLabelText(`${name}简介`), { target: { value: "修改后的简介" } });
    fireEvent.change(screen.getByLabelText(`${name}话题`), { target: { value: "#AI，开发者" } });
    expect(writes()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: `提交到${name}` }));
    await waitFor(() => expect(api).toHaveBeenCalledWith(`/api/platforms/social/${platform}/uploads`, {
      method: "POST", body: { render_id: RENDER, account_uid: ACCOUNT, title: "这是明确确认的标题",
        description: "修改后的简介", tags: ["AI", "开发者"] },
    }));
    expect(writes()).toHaveLength(1);
  });

  it("respects an explicitly disabled first-comment capability without sending comment fields", async () => {
    connect("xiaohongshu");
    show("xiaohongshu", `&render=${RENDER}`);
    const form = within(await screen.findByRole("form", { name: "小红书发布包" }));
    expect(form.getByText("本平台的首评、置顶和视频数据仍待分别接入。")).toBeTruthy();
    expect(form.queryByRole("checkbox")).toBeNull();
    expect(form.queryByLabelText(/评论内容|自动置顶/)).toBeNull();
    fireEvent.click(form.getByRole("button", { name: "提交到小红书" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0][1]?.body).not.toHaveProperty("first_comment");
    expect(writes()[0][1]?.body).not.toHaveProperty("pin");
  });
});

describe("character limits and safe queue actions", () => {
  it("explains oversized saved Kuaishou topics and only trims them on an explicit click", async () => {
    connect("kuaishou");
    const topics = ["报告速览", "开发者", "AI", "model", "arena", "名次差", "价格差"];
    drafts[`kuaishou:${RENDER}`] = { render_id: RENDER, payload: { title: "用户保存的标题", tags: topics } };
    show("kuaishou", `&render=${RENDER}`);
    const input = await screen.findByLabelText("快手话题");
    expect(input).toHaveProperty("value", topics.join("，"));
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("快手话题（7/3 个，逗号分隔）")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("话题超限：当前 7 个，最多 3 个，请删除 4 个。");
    expect(screen.getByRole("button", { name: "保存草稿" })).toHaveProperty("disabled", false);
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", true);
    expect(writes()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "保留前 3 个话题" }));
    expect(input).toHaveProperty("value", topics.slice(0, 3).join("，"));
    expect(screen.getByLabelText("快手标题")).toHaveProperty("value", "用户保存的标题");
    expect(input.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "保存草稿" })).toHaveProperty("disabled", false);
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", false);
    expect(screen.getByText(/审核和公开状态尚未核实/)).toBeTruthy();
    expect(writes()).toEqual([]);
    expect(drafts[`kuaishou:${RENDER}`]?.payload.tags).toEqual(topics);
  });

  it("identifies Kuaishou topic length errors without submitting", async () => {
    connect("kuaishou"); show("kuaishou", `&render=${RENDER}`);
    const input = await screen.findByLabelText("快手话题");
    fireEvent.change(input, { target: { value: "🙂".repeat(31) } });
    expect(screen.getByRole("alert").textContent).toContain("每个话题最多 30 字");
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", true);
    fireEvent.change(input, { target: { value: "🙂".repeat(30) } });
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", false);
    expect(writes()).toEqual([]);
  });

  it("flags malformed saved topics until the user corrects them", async () => {
    connect("kuaishou");
    drafts[`kuaishou:${RENDER}`] = { render_id: RENDER, payload: { tags: ["科技\n知识"] } };
    show("kuaishou", `&render=${RENDER}`);
    await screen.findByLabelText("快手话题");
    expect(screen.getByRole("alert").textContent).toContain("话题不能包含换行");
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("快手话题"), { target: { value: "科技，知识" } });
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", false);
    expect(writes()).toEqual([]);
  });

  it("counts emoji as code points for the Xiaohongshu maximum instead of UTF-16 units", async () => {
    connect("xiaohongshu"); show("xiaohongshu", `&render=${RENDER}`);
    const title = await screen.findByLabelText("小红书标题");
    fireEvent.change(title, { target: { value: "🙂".repeat(20) } });
    expect(screen.getByText("小红书标题（20/20，至少 1 字）")).toBeTruthy();
    expect(screen.getByRole("button", { name: "提交到小红书" })).toHaveProperty("disabled", false);
    fireEvent.change(title, { target: { value: "🙂".repeat(21) } });
    expect(screen.getByRole("button", { name: "提交到小红书" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("alert").textContent).toContain("标题需为 1–20 字，当前 21 字。");
    fireEvent.change(title, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "保存草稿" })).toHaveProperty("disabled", false);
    expect(writes()).toEqual([]);
  });

  it("enforces Channels' six-character minimum and sixteen-character maximum including emoji", async () => {
    connect("weixin_channels"); show("weixin_channels", `&render=${RENDER}`);
    const title = await screen.findByLabelText("微信视频号标题");
    for (const [count, disabled] of [[5, true], [6, false], [16, false], [17, true]] as const) {
      fireEvent.change(title, { target: { value: "🙂".repeat(count) } });
      expect(screen.getByRole("button", { name: "提交到微信视频号" })).toHaveProperty("disabled", disabled);
    }
    expect(writes()).toEqual([]);
  });

  it("enforces Kuaishou's combined caption limit with separators and emoji", async () => {
    connect("kuaishou"); show("kuaishou", `&render=${RENDER}`);
    fireEvent.change(await screen.findByLabelText("快手标题"), { target: { value: "🙂" } });
    fireEvent.change(screen.getByLabelText("快手话题"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("快手简介"), { target: { value: "🙂".repeat(497) } });
    expect(screen.getByText("标题、简介和话题合计 500/500 字")).toBeTruthy();
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", false);
    fireEvent.change(screen.getByLabelText("快手简介"), { target: { value: "🙂".repeat(498) } });
    expect(screen.getByText("标题、简介和话题合计 501/500 字")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("标题、简介和话题合计超限：当前 501 字，最多 500 字。");
    expect(screen.getByRole("button", { name: "提交到快手" })).toHaveProperty("disabled", true);
  });

  it("includes Channels hashtags and newlines in its combined 1000-character limit", async () => {
    connect("weixin_channels"); show("weixin_channels", `&render=${RENDER}`);
    fireEvent.change(await screen.findByLabelText("微信视频号标题"), { target: { value: "🙂".repeat(6) } });
    fireEvent.change(screen.getByLabelText("微信视频号话题"), { target: { value: "AI" } });
    fireEvent.change(screen.getByLabelText("微信视频号简介"), { target: { value: "文".repeat(988) } });
    expect(screen.getByText("标题、简介和话题合计 1000/1000 字")).toBeTruthy();
    expect(screen.getByRole("button", { name: "提交到微信视频号" })).toHaveProperty("disabled", false);
    fireEvent.change(screen.getByLabelText("微信视频号简介"), { target: { value: "文".repeat(989) } });
    expect(screen.getByRole("button", { name: "提交到微信视频号" })).toHaveProperty("disabled", true);
    expect(writes()).toEqual([]);
  });

  it.each([false, true])("never shows retry for UNKNOWN even if can_retry=%s", async canRetry => {
    connect("xiaohongshu");
    overviews.xiaohongshu.uploads = [job("xiaohongshu", { can_retry: canRetry })];
    show();
    await screen.findByText("结果待核对");
    expect(screen.queryByRole("button", { name: "重新检查并投稿" })).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("only offers retry for a failed task confirmed not submitted", async () => {
    connect("xiaohongshu");
    overviews.xiaohongshu.uploads = [job("xiaohongshu", { status: "FAILED", can_retry: true })];
    show();
    fireEvent.click(await screen.findByRole("button", { name: "重新检查并投稿" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/xiaohongshu/uploads/xiaohongshu-task/retry", { method: "POST", body: undefined }));
    expect(writes()).toHaveLength(1);
  });

  it.each(ACTIVE_PLATFORMS)("requires a separate explicit human absence decision before restoring %s retry", async (platform) => {
    connect(platform);
    overviews[platform].uploads = [job(platform, {can_confirm_absent:true})];
    show(platform);
    const button=await screen.findByRole('button',{name:'确认未发布，恢复重试'});
    expect(button).toHaveProperty('disabled',true);
    fireEvent.click(button);
    expect(writes()).toEqual([]);
    fireEvent.click(screen.getByRole('checkbox',{name:/我已核对该账号的作品管理/}));
    fireEvent.click(button);
    await screen.findByRole('button',{name:'重新检查并投稿'});
    expect(writes()).toHaveLength(1);
    expect(api).toHaveBeenCalledWith(`/api/platforms/social/${platform}/uploads/${platform}-task/confirm-absent`,{
      method:'POST',body:{confirmed_absent:true,account_uid:ACCOUNT,expected_updated_at:2}});
    expect(writes().some(([path]) => path.endsWith('/retry'))).toBe(false);
  });

  it("does not let viewers confirm absence", async () => {
    auth.role='viewer'; connect('kuaishou');
    overviews.kuaishou.uploads=[job('kuaishou',{can_confirm_absent:true})];
    show('kuaishou');
    await screen.findByText('结果待核对');
    expect(screen.queryByRole('button',{name:'确认未发布，恢复重试'})).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("keeps accepted Kuaishou submissions without an ID out of the absence and retry flows", async () => {
    connect('kuaishou');
    overviews.kuaishou.uploads=[job('kuaishou',{status:'SUBMITTED',can_confirm_absent:false,can_retry:false})];
    show('kuaishou');
    await screen.findByText('已投稿 · 待补作品链接');
    expect(screen.getByText(/平台已接受投稿，回执未提供公开作品 ID/)).toBeTruthy();
    expect(screen.queryByRole('button',{name:'确认未发布，恢复重试'})).toBeNull();
    expect(screen.queryByRole('button',{name:'重新检查并投稿'})).toBeNull();
    expect(writes()).toEqual([]);
  });

  it.each(ACTIVE_PLATFORMS)("automatically refreshes %s publication without another upload", async platform => {
    connect(platform);
    overviews[platform].definition.capabilities.public_check=true;
    overviews[platform].uploads=[job(platform,{status:"SUBMITTED",verification:{automatic:true,state:"reviewing",checked_at:1792918129,next_check_at:1792918159,reason:"平台审核中"}})];
    const intervals=vi.spyOn(window,"setInterval");
    try {
      show(platform);
      await screen.findByText("平台审核中");
      expect(screen.getByText("账号已核验 · 自动核对已开启")).toBeTruthy();
      expect(screen.getByText(/最近核对：/)).toBeTruthy();
      expect(screen.getByText(/回查只读取结果，不会重新投稿/)).toBeTruthy();
      const refresh=intervals.mock.calls.find(([,milliseconds]) => milliseconds===4000)?.[0];
      expect(typeof refresh).toBe("function");
      overviews[platform].uploads=[job(platform,{status:"PUBLISHED",phase:"已公开并回写文稿",url:"https://platform.example/exact-work",verification:{automatic:false}})];
      await act(async () => { if (typeof refresh==="function") refresh(); });
      await screen.findByText("已发布");
      expect(screen.getByText(/已公开并回写文稿/)).toBeTruthy();
      expect(screen.getByRole("link",{name:"查看平台作品"})).toHaveProperty("href","https://platform.example/exact-work");
      expect(writes()).toEqual([]);
    } finally { intervals.mockRestore(); }
  });

  it("lets an accepted post request read-only discovery without requiring a work ID", async () => {
    connect("kuaishou");overviews.kuaishou.definition.capabilities.public_check=true;
    overviews.kuaishou.uploads=[job("kuaishou",{status:"SUBMITTED",verification:{automatic:true}})];
    show("kuaishou");
    await screen.findByText("已投稿 · 自动核对中");
    fireEvent.click(screen.getByRole("button",{name:"核对发布结果"}));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/kuaishou/uploads/kuaishou-task/reconcile",{method:"POST",body:{video:""}}));
    expect(writes().filter(([path]) => path.endsWith("/uploads") || path.endsWith("/retry"))).toEqual([]);
  });

  it("does not describe an unknown outcome as an accepted submission while checking", async () => {
    connect("kuaishou");overviews.kuaishou.definition.capabilities.public_check=true;
    overviews.kuaishou.uploads=[job("kuaishou",{status:"UNKNOWN",verification:{automatic:true,state:"missing"}})];
    show("kuaishou");
    await screen.findByText("结果待核对 · 自动回查中");
    expect(screen.queryByText("已投稿 · 自动核对中")).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("keeps historical work-query failures out of account login status", async () => {
    connect("kuaishou");overviews.kuaishou.definition.capabilities.public_check=true;
    overviews.kuaishou.checks=[job("kuaishou",{kind:"RECONCILE",status:"RECORDED",error:"历史作品查询失败"}),job("kuaishou",{id:"verified",kind:"VERIFY",status:"RECORDED",phase:"登录有效"})];
    show("kuaishou");
    await screen.findByText("账号已核验 · 自动核对已开启");
    expect(screen.getByText("登录有效")).toBeTruthy();
    expect(screen.queryByText(/历史作品查询失败/)).toBeNull();
    expect(writes()).toEqual([]);
  });

  it.each(["xiaohongshu", "weixin_channels"] as const)("does not pretend to automatically confirm %s public status", async platform => {
    connect(platform);
    overviews[platform].uploads = [job(platform, { status: "SUBMITTED", work_id: "provider-work-id" })];
    show(platform);
    expect(await screen.findByText(/请在创作中心核对实际结果，公开后可在文稿发布页手动补录/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /核对发布结果|只读核对作品归属/ })).toBeNull();
    expect(screen.queryByText(/自动核对已启用/)).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("labels Kuaishou ownership checking separately from public confirmation", async () => {
    connect("kuaishou");
    overviews.kuaishou.uploads = [job("kuaishou", { status: "SUBMITTED", work_id: "3xexactwork" })];
    show("kuaishou");
    fireEvent.change(await screen.findByLabelText("快手核对作品ID"), { target: { value: "3xexactwork" } });
    expect(screen.queryByRole("button", { name: "核对发布结果" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "只读核对作品归属" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/platforms/social/kuaishou/uploads/kuaishou-task/reconcile", { method: "POST", body: { video: "3xexactwork" } }));
  });
});

describe("one finished video across independent platforms", () => {
  it.each(ACTIVE_PLATFORMS)("still prepares %s after that version was published to Bilibili", async (platform, name) => {
    const publication = { id: "bili-record", version: 3, platform: "bilibili", account_uid: "bili-owner",
      url: "https://www.bilibili.com/video/BV1xx411c7mD", recorded_by: "platform-manager" } as Publication;
    const target = (targetPlatform: string, overrides: Partial<Distribution> = {}): Distribution => ({
      platform: targetPlatform, script_id: SCRIPT, version: 3, account_uid: ACCOUNT, account_name: "测试作者",
      connected: true, current_account: true, status: "UNPUBLISHED", can_prepare: true, blocked_reason: "",
      task: null, draft: null, publications: [], ...overrides,
    });
    const video = { id: RENDER, version: 3, files: [{ name: "video.mp4" }] } as VideoRender;
    render(<MemoryRouter><PublicationFlow scriptId={SCRIPT} version={3} render={video}
      publications={[publication]} canEdit distributions={[
        target("bilibili", { status: "PUBLISHED", can_prepare: false, publications: [publication] }), target(platform),
      ]} /></MemoryRouter>);
    expect(screen.getByText(/已发布至 1 个平台/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: `准备${name}发布` }));
    expect(await screen.findByRole("form", { name: `${name}发布包` })).toBeTruthy();
    expect(api).toHaveBeenCalledWith(`/api/platforms/social/${platform}/preview/${RENDER}`, expect.anything());
    expect(screen.getByRole("link", { name: "查看作品" }).getAttribute("href")).toBe(publication.url);
    expect(writes()).toEqual([]);
  });
});
