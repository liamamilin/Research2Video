import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DouyinJob } from "@/api/douyin";
import { DouyinConnectionStatus } from "./DouyinConnectionStatus";

afterEach(cleanup);

const verifiedAt = Date.parse("2026-10-29T17:59:58+08:00") / 1000;
const account = { connected: true, uid: "123", name: "测试创作者", verified_at: verifiedAt };
const record = (id: string, updatedAt: number, values: Partial<DouyinJob> = {}): DouyinJob => ({
  id, kind: "RECONCILE", status: "FAILED", phase: "准备", error: "未能验证抖音登录，请重新扫码。",
  work_id: "", script_id: "", version: 0, render_id: "", created_at: updatedAt - 2, updated_at: updatedAt,
  payload: {}, url: "", can_retry: false, ...values,
});

describe("Douyin connection status", () => {
  it("moves old lookup failures into dated history after a successful login", () => {
    const checks = [record("old", verifiedAt - 3600)];
    render(<DouyinConnectionStatus account={account} checks={checks} />);
    expect(screen.getByText(/抖音账号已连接 · 最近登录验证成功/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("list", { name: "最近抖音检查" })).toBeNull();
    const summary = screen.getByText("历史检查记录（1）");
    expect(summary.closest("details")?.open).toBe(false);
    fireEvent.click(summary);
    expect(within(screen.getByRole("list", { name: "历史抖音检查" })).getByText(checks[0].error)).toBeTruthy();
    expect(screen.getByText("2026/10/29 16:59:58")).toBeTruthy();
    expect(checks[0].status).toBe("FAILED");
  });

  it("keeps failures after the last successful login visible and identifies their kind", () => {
    render(<DouyinConnectionStatus account={account} checks={[
      record("old", verifiedAt - 3600), record("new", verifiedAt + 60),
      record("verify", verifiedAt + 120, { kind: "VERIFY", error: "登录验证请求失败" }),
    ]} />);
    const current = within(screen.getByRole("list", { name: "最近抖音检查" }));
    expect(current.getAllByRole("alert")).toHaveLength(2);
    expect(current.getByText(/登录验证 · 失败/)).toBeTruthy();
    expect(current.getByText(/作品回查 · 失败/)).toBeTruthy();
    expect(screen.getByText("历史检查记录（1）")).toBeTruthy();
  });

  it("does not claim verified login or suppress failures for a disconnected account", () => {
    render(<DouyinConnectionStatus account={{ ...account, connected: false }} checks={[record("old", verifiedAt - 3600)]} />);
    expect(screen.queryByText(/最近登录验证成功/)).toBeNull();
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/历史检查记录/)).toBeNull();
  });

  it("does not assume a connected account without a verification timestamp was verified", () => {
    render(<DouyinConnectionStatus account={{ connected: true }} checks={[record("old", verifiedAt - 3600)]} />);
    expect(screen.queryByText(/最近登录验证成功/)).toBeNull();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("keeps an ongoing check visible even when it started before the latest successful login", () => {
    render(<DouyinConnectionStatus account={account} checks={[
      record("running", verifiedAt - 1, { kind: "VERIFY", status: "RUNNING", phase: "正在检查", error: "" }),
    ]} />);
    expect(within(screen.getByRole("list", { name: "最近抖音检查" })).getByText(/登录验证 · 进行中/)).toBeTruthy();
    expect(screen.queryByText(/历史检查记录/)).toBeNull();
  });
});
