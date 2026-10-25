import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SystemConfig } from "./SystemConfig";

const PARSED = {
  ai: { model: "old-model", base_url: "https://x/v1", api_key_env: "LLM_API_KEY" },
  search: { provider: "parallel", max_results: 10, api_key_env: "PARALLEL_API_KEY" },
  research: { mode: "auto", max_rounds: 12 },
  budget: { monthly_usd_limit: 0 },
  notifications: { enabled: false, notify_on: ["failed"] },
};

const PARSED_WITH_REVIEW_AI = {
  ...PARSED,
  review_ai: { model: "deepseek-v4-pro", base_url: "https://x/v1", api_key_env: "REVIEW_API_KEY" },
};

const SECRETS = {
  secrets: [
    { env: "LLM_API_KEY", label: "AI 模型（LLM）", configured: true, masked: "sk-1****ab" },
    { env: "PARALLEL_API_KEY", label: "搜索服务", configured: false, masked: "" },
  ],
};

const PROVIDERS = [
  {
    id: "open-design",
    label: "Open Design（amr-link）",
    base_url: "https://amr-link.open-design.ai/v1",
    api_key_env: "LLM_API_KEY",
    extra_headers: { "User-Agent": "BaseCodingCLi-ResearchAgent/2.0" },
    models: ["deepseek-v4.1-flash"],
    free_models: [],
    note: "OpenAI 兼容。",
  },
  {
    id: "opencode-zen",
    label: "opencode zen（按量计费）",
    base_url: "https://opencode.ai/zen/v1",
    api_key_env: "LLM_API_KEY",
    extra_headers: {},
    models: ["deepseek-v4-flash", "beam-free"],
    free_models: ["beam-free"],
    note: "按量付费。",
  },
];

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status < 300,
    status,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockFetch(parsed: unknown = PARSED) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("/models")) {
      return jsonResponse(200, { models: ["deepseek-v4-flash", "glm-5.3-flash", "deepseek-v4.1-flash", "deepseek-v4-pro", "beam-free"],
        free_models: String(url).includes("opencode-zen") ? ["beam-free"] : [], prices: {}, updated_at: 1792700000, error: null });
    }
    if (String(url).includes("/api/config/secrets")) {
      if (init?.method === "PUT") {
        return jsonResponse(200, { ok: true, secrets: SECRETS.secrets });
      }
      return jsonResponse(200, SECRETS);
    }
    if (String(url).includes("/api/config/providers")) {
      return jsonResponse(200, { providers: PROVIDERS });
    }
    if (String(url).includes("/api/config/test-")) {
      return jsonResponse(200, { ok: true, latency_ms: 42, model: "old-model" });
    }
    if (init?.method === "PUT") {
      return jsonResponse(200, { ok: true, mtime: 123 });
    }
    return jsonResponse(200, {
      content: "ai:\n  model: old-model\n",
      parsed,
      mtime: 100,
    });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

afterEach(() => {
  localStorage.clear();
  cleanup();
  vi.unstubAllGlobals();
});

describe("SystemConfig form", () => {
  it("renders parsed values into the form", async () => {
    mockFetch();
    render(<SystemConfig />);
    expect(await screen.findByDisplayValue("old-model")).toBeTruthy();
    expect(screen.getByDisplayValue("https://x/v1")).toBeTruthy();
    expect(screen.getByText("API 密钥")).toBeTruthy();
    expect(screen.getByText("预算护栏")).toBeTruthy();
  });

  it("warns when the model is not one the provider has, fallbacks included", async () => {
    mockFetch({
      ...PARSED,
      ai: { model: "minimax-m3.1-flash-free", fallback_models: ["glm-5.3-flash", "minimax-m3"],
            base_url: "https://amr-link.open-design.ai/v1", api_key_env: "LLM_API_KEY" },
    });
    render(<SystemConfig />);
    expect(await screen.findByText(/没有这个模型：minimax-m3.1-flash-free、minimax-m3/)).toBeTruthy();
  });

  it("keeps a half-typed fallback list: the comma typed is not swallowed", async () => {
    mockFetch();
    render(<SystemConfig />);

    const input = (await screen.findByLabelText("ai 兜底模型")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "minimax-m3, " } });
    expect(input.value).toBe("minimax-m3, ");
    fireEvent.change(input, { target: { value: "minimax-m3, deepseek-v4-pro" } });
    expect(input.value).toBe("minimax-m3, deepseek-v4-pro");
  });

  it("switching the search provider switches the key variable with it", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    await screen.findByDisplayValue("PARALLEL_API_KEY");
    fireEvent.change(screen.getByLabelText("搜索服务商"), { target: { value: "exa" } });
    expect(screen.getByDisplayValue("EXA_API_KEY")).toBeTruthy();  // not left on Parallel's variable
    fireEvent.change(screen.getByLabelText("搜索服务商"), { target: { value: "doubao" } });
    expect(screen.getByDisplayValue("DOUBAO_SEARCH_API_KEY")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索服务商"), { target: { value: "exa_mcp" } });
    expect(screen.getByText(/Exa 公开端点无需密钥/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索服务商"), { target: { value: "youcom" } });
    expect(screen.getByText(/每天 100 次查询/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索服务商"), { target: { value: "duckduckgo" } });
    expect(screen.getByText("DuckDuckGo 无需密钥。")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索服务商"), { target: { value: "exa" } });

    fireEvent.click(screen.getByText("保存配置"));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT" && !c.url.includes("secrets"))).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const search = JSON.parse(String(put?.init?.body)).patch.search;
    expect(search.provider).toBe("exa");
    expect(search.api_key_envs.exa).toBe("EXA_API_KEY");
    expect(search.api_key_env).toBe("PARALLEL_API_KEY");  // Parallel's own stays as it was
  });

  it("shows configured API keys masked", async () => {
    mockFetch();
    render(<SystemConfig />);
    expect(await screen.findByPlaceholderText("已配置 · sk-1****ab")).toBeTruthy();
    expect(screen.getByPlaceholderText("未配置")).toBeTruthy();
  });

  it("saves changed fields as a patch with mtime", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const modelInput = await screen.findByDisplayValue("old-model");
    fireEvent.change(modelInput, { target: { value: "new-model" } });
    fireEvent.click(screen.getByText("保存配置"));

    await waitFor(() => {
      expect(calls.some((c) => c.init?.method === "PUT")).toBe(true);
    });
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.ai.model).toBe("new-model");
    expect(body.patch.search.provider).toBe("parallel");
    expect(body.patch.research.mode).toBe("auto");
    expect(body.expected_mtime).toBe(100);
  });

  it("writes typed API keys through the secrets endpoint", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const input = await screen.findByPlaceholderText("已配置 · sk-1****ab");
    fireEvent.change(input, { target: { value: "typed-key" } });
    fireEvent.click(screen.getByText("保存密钥"));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("/secrets") && c.init?.method === "PUT"))
        .toBe(true);
    });
    const put = calls.find((c) => c.url.includes("/secrets") && c.init?.method === "PUT");
    const body = JSON.parse(String(put?.init?.body));
    expect(body.values.LLM_API_KEY).toBe("typed-key");
  });

  it("tests the LLM with the current form values", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const modelInput = await screen.findByDisplayValue("old-model");
    fireEvent.change(modelInput, { target: { value: "form-model" } });
    fireEvent.click(screen.getByText("测试 LLM"));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("/test-llm"))).toBe(true);
    });
    const call = calls.find((c) => c.url.includes("/test-llm"));
    const body = JSON.parse(String(call?.init?.body));
    expect(body.config.ai.model).toBe("form-model");
    expect(await screen.findByText(/可用/)).toBeTruthy();
  });

  it("includes notification toggles in the patch", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const toggle = await screen.findByLabelText("启用通知");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByText("保存配置"));

    await waitFor(() => {
      expect(calls.some((c) => c.init?.method === "PUT")).toBe(true);
    });
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.notifications.enabled).toBe(true);
  });

  it("fills base_url, env var and headers together when a provider is picked", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    // The saved URL (https://x/v1) matches no preset, so the dropdown starts
    // custom -- for both ai and the unconfigured review_ai card, so take ai's.
    await screen.findByDisplayValue("https://x/v1");
    const [select] = screen.getAllByDisplayValue("自定义（当前地址未匹配预设）");
    fireEvent.change(select, { target: { value: "open-design" } });
    fireEvent.change(screen.getAllByPlaceholderText("deepseek-v4.1-flash")[0], { target: { value: "deepseek-v4.1-flash" } });

    fireEvent.click(screen.getByText("保存配置"));
    await waitFor(() => {
      expect(calls.some((c) => c.init?.method === "PUT")).toBe(true);
    });
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.ai.base_url).toBe("https://amr-link.open-design.ai/v1");
    expect(body.patch.ai.api_key_env).toBe("LLM_API_KEY");
    expect(body.patch.ai.extra_headers["User-Agent"]).toBeTruthy();
  });

  it("puts back the model last used with a provider, or leaves the field empty, and will not save an empty one", async () => {
    const calls = mockFetch({ ...PARSED, ai: { ...PARSED.ai, base_url: "https://amr-link.open-design.ai/v1",
                                              model: "deepseek-v4.1-flash", fallback_models: ["deepseek-v4-pro"] } });
    render(<SystemConfig />);

    const modelInput = () => screen.getAllByPlaceholderText("deepseek-v4.1-flash")[0] as HTMLInputElement;
    await screen.findByDisplayValue("deepseek-v4.1-flash");
    const select = screen.getAllByDisplayValue("Open Design（amr-link）")[0];
    fireEvent.change(select, { target: { value: "opencode-zen" } });  // never used here: nothing to put back
    expect(modelInput().value).toBe("");
    fireEvent.click(screen.getByText("保存配置"));
    await screen.findByText(/AI 模型：还没选模型/);
    expect(calls.some((c) => c.init?.method === "PUT" && !c.url.includes("secrets"))).toBe(false);

    fireEvent.change(modelInput(), { target: { value: "beam-free" } });
    fireEvent.change(screen.getAllByDisplayValue("opencode zen（按量计费）")[0], { target: { value: "open-design" } });
    expect(modelInput().value).toBe("deepseek-v4.1-flash");  // what the card had with Open Design before
    expect(screen.getByDisplayValue("deepseek-v4-pro")).toBeTruthy();  // and its fallback
    fireEvent.change(screen.getAllByDisplayValue("Open Design（amr-link）")[0], { target: { value: "opencode-zen" } });
    expect(modelInput().value).toBe("beam-free");
  });

  it("does not remember a model the provider does not have", async () => {
    mockFetch({ ...PARSED, ai: { ...PARSED.ai, base_url: "https://amr-link.open-design.ai/v1", model: "minimax-m3.1-flash-free" } });
    render(<SystemConfig />);

    await screen.findByText(/没有这个模型：minimax-m3.1-flash-free/);  // the live list has arrived
    fireEvent.change(screen.getAllByDisplayValue("Open Design（amr-link）")[0], { target: { value: "opencode-zen" } });
    fireEvent.change(screen.getAllByDisplayValue("opencode zen（按量计费）")[0], { target: { value: "open-design" } });
    expect((screen.getAllByPlaceholderText("deepseek-v4.1-flash")[0] as HTMLInputElement).value).toBe("");
  });

  it("warns when an API key is typed into the env-name field", async () => {
    mockFetch();
    render(<SystemConfig />);

    await screen.findByDisplayValue("LLM_API_KEY");
    const envInput = screen.getByDisplayValue("LLM_API_KEY");
    fireEvent.change(envInput, { target: { value: "sk-amr-abc123" } });

    expect(await screen.findByText(/请求不会带 Authorization 头/)).toBeTruthy();
  });

  it("says so when the key slot of the provider in use is empty", async () => {
    mockFetch({ ...PARSED, ai: { ...PARSED.ai, api_key_env: "PARALLEL_API_KEY" } });  // listed in SECRETS, not configured
    render(<SystemConfig />);

    expect(await screen.findByText(/这个服务商的密钥（PARALLEL_API_KEY）还没填/)).toBeTruthy();
  });

  it("takes the key next to the provider and saves it with the config", async () => {
    const calls = mockFetch({ ...PARSED, ai: { ...PARSED.ai, api_key_env: "PARALLEL_API_KEY" } });
    render(<SystemConfig />);

    fireEvent.change((await screen.findAllByLabelText("PARALLEL_API_KEY 密钥"))[0], { target: { value: "sk-typed" } });  // the search card shares it
    fireEvent.click(screen.getByText("保存配置"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("secrets") && c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.url.includes("secrets") && c.init?.method === "PUT");
    expect(JSON.parse(String(put?.init?.body)).values).toEqual({ PARALLEL_API_KEY: "sk-typed" });
  });

  it("configures report cleanup like the other models: provider picks address, key and model together", async () => {
    const calls = mockFetch({ ...PARSED, extraction: { enabled: false, model: "m", base_url: "https://x/v1", api_key_env: "LLM_API_KEY" } });
    render(<SystemConfig />);

    await screen.findByText("报告清理（可选）");
    const selects = screen.getAllByDisplayValue("自定义（当前地址未匹配预设）");
    fireEvent.change(selects[selects.length - 1], { target: { value: "open-design" } });  // the last card is the cleanup one
    fireEvent.change(screen.getAllByPlaceholderText("deepseek-v4-flash").pop()!, { target: { value: "deepseek-v4.1-flash" } });
    fireEvent.click(screen.getByLabelText("启用生成后清理（extraction）"));
    fireEvent.click(screen.getByText("保存配置"));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT" && !c.url.includes("secrets"))).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.extraction).toEqual({
      enabled: true, model: "deepseek-v4.1-flash", base_url: "https://amr-link.open-design.ai/v1", api_key_env: "LLM_API_KEY" });
  });

  it("stays quiet when that slot is filled", async () => {
    mockFetch();  // ai uses LLM_API_KEY, which is configured
    render(<SystemConfig />);

    await screen.findByDisplayValue("https://x/v1");
    expect(screen.queryByText(/这个服务商的密钥/)).toBeNull();
  });

  it("shows free models as clickable chips and sets the model field", async () => {
    mockFetch();
    render(<SystemConfig />);

    await screen.findByDisplayValue("old-model");
    // open-design has no free_models in the fixture; switch ai to opencode-zen,
    // whose fixture preset below carries one.
    const [select] = await screen.findAllByDisplayValue("自定义（当前地址未匹配预设）");
    fireEvent.change(select, { target: { value: "opencode-zen" } });

    fireEvent.click((await screen.findAllByText("beam-free"))[0]);
    expect(await screen.findByDisplayValue("beam-free")).toBeTruthy();
  });

  it("edits the paid fallback list as a comma-separated field and saves it", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    // Both the ai and the review_ai cards share this placeholder; ai's is first.
    const [fallbackInput] = await screen.findAllByPlaceholderText("minimax-m3, deepseek-v4.1-flash");
    fireEvent.change(fallbackInput, { target: { value: "minimax-m3, deepseek-v4.1-flash" } });
    fireEvent.click(screen.getByText("保存配置"));

    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.ai.fallback_models).toEqual(["minimax-m3", "deepseek-v4.1-flash"]);
    // review_ai was never configured in this fixture: deleting it is a no-op,
    // but it must be explicit null, not an omitted key (see llmPatch's comment).
    expect(body.patch.review_ai).toBeNull();
  });

  it("renders review_ai as a separate, optional card with its own test button", async () => {
    mockFetch();
    render(<SystemConfig />);

    expect(await screen.findByText(/审校模型（review_ai/)).toBeTruthy();
    expect(screen.getByText("测试审校模型")).toBeTruthy();
    expect(screen.getByText("测试 LLM")).toBeTruthy(); // ai's button keeps its own short label
  });

  it("deletes a configured review_ai section on save", async () => {
    const calls = mockFetch(PARSED_WITH_REVIEW_AI);
    render(<SystemConfig />);

    // Only review_ai ever gets a remove button -- ai is never optional.
    fireEvent.click(await screen.findByText("删除此配置"));
    fireEvent.click(screen.getByText("保存配置"));

    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.review_ai).toBeNull();
  });
});


it("preserves unsaved form changes when switching editing modes is cancelled", async () => {
  mockFetch(); render(<SystemConfig />);
  const input = await screen.findByDisplayValue("old-model");
  fireEvent.change(input, { target: { value: "local-draft-model" } });
  fireEvent.click(screen.getByRole("button", { name: "YAML（高级）" }));
  expect(screen.getByText(/有未保存的配置或密钥修改/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
  expect(screen.getByDisplayValue("local-draft-model")).toBeTruthy();
});
