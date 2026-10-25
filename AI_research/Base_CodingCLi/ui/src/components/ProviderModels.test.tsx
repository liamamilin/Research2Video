import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isOffered, ProviderModels } from "./ProviderModels";

const PRICE = { input: 1.4, input_max: 1.4, output: 4.4, output_max: 4.4,
  tiers: [{ label: "GLM-5.3", input: 1.4, output: 4.4 }] };
const FREE = { input: 0, input_max: 0, output: 0, output_max: 0, tiers: [] };
const catalog = (models = ["glm-5.3", "beam-free"]) => ({
  models, free_models: models.filter((id) => id.endsWith("-free")),
  prices: { "glm-5.3": PRICE, "beam-free": FREE, "new-free": FREE },
  updated_at: 1792700000, pricing_updated_at: 1792700000,
  pricing_source: "https://opencode.ai/v2/docs/console/go/", error: null,
});
const response = (data: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify(data) }) as Response;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("provider catalog", () => {
  it("shows official input/output prices and picks only the model ID", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(catalog())));
    const pick = vi.fn();
    render(<ProviderModels providerId="price-test" label="Test" model="glm-5.3" onPick={pick} />);
    await screen.findByText("(入 $1.4 / 出 $4.4)");
    expect(screen.getByText("官网价格来源").getAttribute("href")).toBe(catalog().pricing_source);
    fireEvent.click(screen.getByText("glm-5.3"));
    expect(pick).toHaveBeenCalledWith("glm-5.3");
    expect(screen.getByText(/USD \/ 每百万 tokens/)).toBeTruthy();
  });

  it("forces a refresh and updates all cards for that provider, removing old free models", async () => {
    const fetch = vi.fn(async (url: string) => response(catalog(url.includes("refresh=true")
      ? ["glm-5.3", "new-free"] : undefined)));
    vi.stubGlobal("fetch", fetch);
    render(<><section data-testid="ai"><ProviderModels providerId="shared-test" label="Test" model="chosen"
      onPick={() => {}} /></section><section data-testid="review"><ProviderModels providerId="shared-test"
      label="Test" model="chosen" onPick={() => {}} /></section></>);
    await waitFor(() => expect(screen.getAllByText("beam-free").length).toBe(4));
    expect(fetch).toHaveBeenCalledTimes(1);
    fireEvent.click(within(screen.getByTestId("ai")).getByText("检测并更新模型"));
    await waitFor(() => expect(screen.queryByText("beam-free")).toBeNull());
    expect(within(screen.getByTestId("review")).getAllByText("new-free").length).toBe(2);
    expect(fetch.mock.calls[1][0]).toContain("?refresh=true");
    expect(isOffered("shared-test", "beam-free")).toBe(false);
    expect(isOffered("shared-test", "new-free")).toBe(true);
  });

  it("retains the prior catalog and a retry button on network failure", async () => {
    let fail = false;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (fail) throw new TypeError("offline");
      return response(catalog());
    }));
    render(<ProviderModels providerId="offline-test" label="Test" model="glm-5.3" onPick={() => {}} />);
    await screen.findByText("(入 $1.4 / 出 $4.4)");
    fail = true;
    fireEvent.click(screen.getByText("检测并更新模型"));
    await screen.findByText(/保留上次清单/);
    expect(screen.getByText("glm-5.3")).toBeTruthy();
    expect((screen.getByText("检测并更新模型") as HTMLButtonElement).disabled).toBe(false);
  });

  it("marks unknown prices and warns about delisted current and fallback models", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...catalog(["new-model"]), prices: {} })));
    render(<ProviderModels providerId="unknown-test" label="Test" model="old-model"
      fallbacks={["old-fallback"]} onPick={() => {}} />);
    await screen.findByText("(价格未知)");
    expect(screen.getByText(/没有这个模型：old-model、old-fallback/)).toBeTruthy();
    expect(screen.getByText("未检测到已确认的免费模型")).toBeTruthy();
  });

  it("shows price ranges and distinguishes Go's subscription allowance", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...catalog(["glm-5.3"]),
      prices: { "glm-5.3": { ...PRICE, input_max: 2.8, output_max: 8.8 } } })));
    render(<ProviderModels providerId="opencode-go" label="Go" model="glm-5.3" onPick={() => {}} />);
    await screen.findByText("(入 $1.4–2.8 / 出 $4.4–8.8)");
    expect(screen.getByText(/扣减订阅额度，月费另计/)).toBeTruthy();
  });
});
