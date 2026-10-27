import { createElement, type ComponentProps } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { AskPage } from "./Ask";
import { useAuthStore } from "@/lib/auth-store";

const parses = vi.hoisted(() => vi.fn());
vi.mock("react-markdown", async (original) => {
  const module = await original<typeof import("react-markdown")>();
  return { ...module, default: (props: ComponentProps<typeof module.default>) => {
    parses(props.children);
    return createElement(module.default, props);
  } };
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); useAuthStore.setState({ user: null }); });

it("typing and receiving a new response do not parse the completed history again", async () => {
  sessionStorage.clear();
  useAuthStore.setState({ user: { username: "editor", role: "editor" } as never });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/stream")) return { ok: true, status: 200, body: new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: "done", result: {
        answer: "新的回复", actions: [], conversation_id: "conversation",
      } })}\n\n`));
      controller.close();
    } }) } as Response;
    const data = url.includes("/workspace") ? { objects: [], operations: [], active: false } : {
      id: "conversation", requests: [{ id: "previous", message: "上次的问题", status: "completed",
        result: { answer: "# 已完成的长报告\n以前的正文", actions: [] } }],
    };
    return { ok: true, status: 200, text: async () => JSON.stringify(data) } as Response;
  }));
  render(<MemoryRouter><AskPage /></MemoryRouter>);
  await screen.findByRole("heading", { name: "已完成的长报告" });
  await waitFor(() => expect(screen.getByLabelText("消息")).toHaveProperty("disabled", false));
  parses.mockClear();
  fireEvent.change(screen.getByLabelText("消息"), { target: { value: "下一个问题" } });
  expect(parses).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(await screen.findByText("新的回复")).toBeTruthy();
  expect(screen.getByText("以前的正文")).toBeTruthy();
  expect(parses.mock.calls.some(([text]) => String(text).includes("以前的正文"))).toBe(false);
});
