import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VideoStorageConfig } from "./VideoStorageConfig";
const current = { directory: "", suggested_directory: "/Volumes/Disk/视频输出", online: true, free_bytes: 100 * 1024 ** 3, migration: {} };
const reply = (body: unknown, status = 200) => ({ ok: status < 300, status, text: async () => JSON.stringify(body) } as Response);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("VideoStorageConfig", () => {
  it("browses folders and saves the selected absolute path with migration", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      if (String(url).includes("/folders")) return reply({ directory: "/Volumes/Disk", parent: "/Volumes", folders: [{ name: "视频输出", path: "/Volumes/Disk/视频输出" }] });
      if (init?.method === "PUT") return reply({ ...current, migration: { status: "waiting", message: "等待视频任务完成后迁移" } });
      return reply(current);
    }));
    render(<VideoStorageConfig />);
    await screen.findByText(/可用空间 100.0 GB/);
    fireEvent.click(screen.getByRole("button", { name: "浏览文件夹" }));
    await screen.findByRole("dialog", { name: "选择输出文件夹" });
    fireEvent.click(screen.getByRole("button", { name: "使用此文件夹" }));
    expect((screen.getByLabelText("输出文件夹") as HTMLInputElement).value).toBe("/Volumes/Disk");
    fireEvent.click(screen.getByRole("button", { name: "保存并迁移" }));
    await screen.findByText("等待视频任务完成后迁移");
    expect(calls.find((call) => call.init?.method === "PUT")?.init?.body).toBe(JSON.stringify({ directory: "/Volumes/Disk" }));
    expect((screen.getByLabelText("输出文件夹") as HTMLInputElement).disabled).toBe(true);
  });
  it("shows failure and leaves the entered path editable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => init?.method === "PUT"
      ? reply({ error: { code: "storage_directory_invalid", message: "外接硬盘未连接" } }, 422) : reply(current)));
    render(<VideoStorageConfig />);
    await screen.findByText(/可用空间/);
    fireEvent.click(screen.getByRole("button", { name: "保存并迁移" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("外接硬盘未连接");
    expect((screen.getByLabelText("输出文件夹") as HTMLInputElement).disabled).toBe(false);
  });
});
