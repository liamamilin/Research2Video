import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoModelConfig } from "./VideoModelConfig";

const CONFIG = {
  label: "DeepSeek（官方）", base_url: "https://api.deepseek.com", model: "deepseek-flash", key_env: "DEEPSEEK_API_KEY",
  source: "默认", needs_key: true, key_ready: true, error: "",
};
const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com/v1", api_key_env: "DEEPSEEK_API_KEY",
    models: ["deepseek-flash"], free_models: [], note: "" },
  { id: "open-design", label: "Open Design（amr-link）", base_url: "https://amr-link.open-design.ai/v1",
    api_key_env: "OPENDESIGN_API_KEY", models: ["deepseek-v4-flash", "glm-5.3-flash"], free_models: ["glm-5.3-flash"], note: "" },
];
const SECRETS = [{ env: "DEEPSEEK_API_KEY", configured: true }, { env: "OPENDESIGN_API_KEY", configured: false }];

function reply(status: number, body: unknown): Response {
  return { ok: status < 300, status, statusText: "x", text: async () => JSON.stringify(body) } as Response;
}

function mockFetch(opts: { reachable?: boolean } = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const u = String(url);
    if (u.includes("/models")) return reply(200, { models: ["deepseek-v4-flash", "glm-5.3-flash"], error: null });
    if (u.endsWith("/api/video/model/test")) return reply(200, { ok: true, status: 200, message: "可用" });
    if (u.endsWith("/api/video/model") && init?.method === "PUT") {
      return reply(200, { ...CONFIG, ...JSON.parse(String(init.body)), source: "设置页" });
    }
    if (u.endsWith("/api/video/model") && init?.method === "DELETE") return reply(200, CONFIG);
    if (u.endsWith("/api/video/model")) {
      return opts.reachable === false
        ? reply(200, { reachable: false, config: null, error: "AutoVideo 无响应" })
        : reply(200, { reachable: true, config: CONFIG, error: null });
    }
    return reply(404, {});
  }));
  return calls;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("VideoModelConfig", () => {
  it("shows what AutoVideo uses now and recognises the provider from its address", async () => {
    mockFetch();
    render(<VideoModelConfig providers={PROVIDERS} secrets={SECRETS} />);
    await screen.findByText(/现在用：DeepSeek（官方） · deepseek-flash/);
    expect((screen.getByLabelText("视频模型服务商") as HTMLSelectElement).value).toBe("deepseek");
  });

  it("choosing a provider fills address, key variable and a model that provider offers, then saves them", async () => {
    const calls = mockFetch();
    render(<VideoModelConfig providers={PROVIDERS} secrets={SECRETS} />);
    await screen.findByText(/现在用/);
    fireEvent.change(screen.getByLabelText("视频模型服务商"), { target: { value: "open-design" } });
    expect((screen.getByLabelText("视频模型接口地址") as HTMLInputElement).value).toBe("https://amr-link.open-design.ai/v1");
    expect((screen.getByLabelText("视频模型密钥变量") as HTMLInputElement).value).toBe("OPENDESIGN_API_KEY");
    expect((screen.getByLabelText("视频模型 ID") as HTMLInputElement).value).toBe("glm-5.3-flash");
    expect(screen.getByText(/OPENDESIGN_API_KEY 还没填/)).toBeTruthy();
    fireEvent.click(screen.getByText("保存并应用"));
    await screen.findByText(/已保存/);
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(JSON.parse(String(put.init!.body))).toEqual({
      base_url: "https://amr-link.open-design.ai/v1", model: "glm-5.3-flash", key_env: "OPENDESIGN_API_KEY" });
    await screen.findByText(/来源：设置页/);
  });

  it("warns when the provider does not have the model typed, and lets the list fill it in", async () => {
    mockFetch();
    render(<VideoModelConfig providers={PROVIDERS} secrets={SECRETS} />);
    await screen.findByText(/现在用/);
    fireEvent.change(screen.getByLabelText("视频模型服务商"), { target: { value: "open-design" } });
    fireEvent.change(screen.getByLabelText("视频模型 ID"), { target: { value: "minimax-m3.1-flash-free" } });
    await screen.findByText(/没有这个模型：minimax-m3.1-flash-free/);
    fireEvent.click(await screen.findByText("deepseek-v4-flash"));
    expect((screen.getByLabelText("视频模型 ID") as HTMLInputElement).value).toBe("deepseek-v4-flash");
    expect(screen.queryByText(/没有这个模型/)).toBeNull();
  });

  it("tests the candidate without saving it", async () => {
    const calls = mockFetch();
    render(<VideoModelConfig providers={PROVIDERS} secrets={SECRETS} />);
    await screen.findByText(/现在用/);
    fireEvent.click(screen.getByText("测试视频模型"));
    await screen.findByText("可用");
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
  });

  it("says so, and disables editing, when AutoVideo is not running", async () => {
    mockFetch({ reachable: false });
    render(<VideoModelConfig providers={PROVIDERS} secrets={SECRETS} />);
    await screen.findByText(/连不上 AutoVideo/);
    expect((screen.getByLabelText("视频模型 ID") as HTMLInputElement).disabled).toBe(true);
  });
});
