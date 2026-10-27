import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BrandingSettings } from "./BrandingSettings";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const reply = (body: unknown, status = 200) => ({ ok: status < 300, status, text: async () => JSON.stringify(body) } as Response);

it("loads when opened, creates Demo and activates the saved profile", async () => {
  const calls: { url: string; body: unknown }[] = [];
  let saved: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)); calls.push({ url: String(url), body });
      if (String(url).endsWith("/demo")) { saved = [{ ...body, assets: {} }]; return reply(saved[0]); }
      return reply(body);
    }
    return reply({ active: { enabled: false }, profiles: saved });
  }));
  render(<BrandingSettings />);
  await screen.findByText("当前默认：未启用");
  fireEvent.click(screen.getByRole("button", { name: "保存并设为默认" }));
  await screen.findByText("已设为新视频的默认品牌。");
  expect(calls[0].url).toBe("/api/video-brands/demo");
  expect(calls[0].body).toMatchObject({ name: "Demo Studio", intro_seconds: 0, corner_reveal_seconds: .6,
    cta: { enabled: true, message: "喜欢这期内容，欢迎" } });
  expect(calls[1]).toEqual({ url: "/api/video-brands/active", body: { enabled: true, profile: "demo" } });
});

it("keeps editing available after a failed save", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => init?.method === "PUT"
    ? reply({ error: { code: "brand_unavailable", message: "品牌服务暂不可用" } }, 422)
    : reply({ active: { enabled: false }, profiles: [] })));
  render(<BrandingSettings />);
  await screen.findByText("当前默认：未启用");
  fireEvent.change(screen.getByLabelText("片头时长（0 为关闭）"), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "保存品牌" }));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("片头时长（0 为关闭）") as HTMLSelectElement).value).toBe("0");
  await waitFor(() => expect((screen.getByLabelText("品牌名称") as HTMLInputElement).disabled).toBe(false));
});

it("can turn the generic call to action off while retaining the brand", async () => {
  const calls: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); calls.push(body); return reply(body); }
    return reply({ active: { enabled: false }, profiles: [] });
  }));
  render(<BrandingSettings />);
  await screen.findByText("当前默认：未启用");
  fireEvent.click(screen.getByLabelText("品牌片尾前显示「点赞 · 收藏 · 关注」"));
  expect(screen.queryByLabelText("互动引导文案")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存品牌" }));
  await screen.findByText(/品牌已保存/);
  expect(calls[0]).toMatchObject({ name: "Demo Studio", cta: { enabled: false }, intro_seconds: 0 });
});

it("saves content palette choice independently from the corner and CTA", async () => {
  const calls: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); calls.push(body); return reply(body); }
    return reply({ active: { enabled: false }, profiles: [] });
  }));
  render(<BrandingSettings />);
  await screen.findByText("当前默认：未启用");
  expect((screen.getByLabelText("正文沿用品牌配色") as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByLabelText("正文沿用品牌配色"));
  fireEvent.click(screen.getByRole("button", { name: "保存品牌" }));
  await screen.findByText(/品牌已保存/);
  expect(calls[0]).toMatchObject({ apply_to_content: true, cta: { enabled: true }, corner_reveal_seconds: .6 });
});

it("uses the editable brand slogan for the optional corner subtitle", async () => {
  const calls: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); calls.push(body); return reply(body); }
    return reply({ active: { enabled: false }, profiles: [] });
  }));
  render(<BrandingSettings />);
  await screen.findByText("当前默认：未启用");
  fireEvent.change(screen.getByLabelText("品牌 Slogan"), { target: { value: "Make knowledge visible" } });
  fireEvent.click(screen.getByLabelText("角标显示品牌 Slogan"));
  fireEvent.click(screen.getByLabelText("互动图标短音效"));
  fireEvent.click(screen.getByRole("button", { name: "保存品牌" }));
  await screen.findByText(/品牌已保存/);
  expect(calls[0]).toMatchObject({ tagline: "Make knowledge visible", corner_tagline: true, intro_seconds: 0, cta: { sound: true } });
});
