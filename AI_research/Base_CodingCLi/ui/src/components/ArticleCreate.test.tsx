import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ArticleCreate } from "./ArticleCreate";

const cells = [{ id: "topic_digest", key: "topic_digest", usable: true, label: "专题速览", column: { topic_kind: "single" } }];
let calls: { url: string; body?: unknown }[];
let rejectUpload = false;
function response(body: unknown, status = 200) { return { ok: status < 400, status, text: async () => JSON.stringify(body) } as Response; }
beforeEach(() => {
  calls = []; rejectUpload = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body });
    if (String(url).includes("/api/video/cells")) return response(cells);
    if (String(url).includes("/api/production")) return response([]);
    if (String(url).includes("/articles/import")) return rejectUpload ? response({ detail: { code: "article_invalid", message: "扫描 PDF 需要 OCR" } }, 422)
      : response({ id: "source-1", text: "雨停以后，风翻动了书页。", filename: "散文.txt", chars: 14, warnings: ["请检查提取正文"] });
    return response({ id: "article-1" }, 202);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("article creation", () => {
  it("creates a pasted article using the selected mode and shared recipe", async () => {
    const created = vi.fn(); render(<ArticleCreate onCreated={created} />);
    fireEvent.change(screen.getByLabelText("文章正文"), { target: { value: "这是一篇信息文章，保留适用条件。" } });
    fireEvent.change(screen.getByLabelText("处理方式"), { target: { value: "adapt" } });
    fireEvent.change(screen.getByLabelText("文章类型"), { target: { value: "information" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "生成文稿" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "生成文稿" }));
    await waitFor(() => expect(created).toHaveBeenCalledWith("article-1"));
    const request = calls.find(c => c.url.includes("/articles/scripts"));
    expect(JSON.parse(String(request?.body))).toMatchObject({ mode: "adapt", kind: "information", column: "topic_digest", text: "这是一篇信息文章，保留适用条件。" });
    expect(calls.filter(c => c.url.includes("/articles/scripts"))).toHaveLength(1);
  });
  it("uploads with multipart and retains the original import when the preview is edited", async () => {
    render(<ArticleCreate onCreated={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("上传文章"), { target: { files: [new File(["原文"], "散文.txt")] } });
    await screen.findByText("请检查提取正文");
    expect(calls.find(c => c.url.includes("/articles/import"))?.body).toBeInstanceOf(FormData);
    fireEvent.change(screen.getByLabelText("文章正文"), { target: { value: "整理后的正文。" } });
    fireEvent.click(screen.getByRole("button", { name: "生成文稿" }));
    await waitFor(() => expect(calls.some(c => c.url.includes("/articles/scripts"))).toBe(true));
    expect(JSON.parse(String(calls.find(c => c.url.includes("/articles/scripts"))?.body))).toMatchObject({ import_id: "source-1", text: "整理后的正文。" });
  });
  it("keeps existing pasted text when uploading fails", async () => {
    rejectUpload = true; render(<ArticleCreate onCreated={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("文章正文"), { target: { value: "不能丢失的正文。" } });
    fireEvent.change(screen.getByLabelText("上传文章"), { target: { files: [new File(["scan"], "扫描.pdf")] } });
    await screen.findByRole("alert");
    expect((screen.getByLabelText("文章正文") as HTMLTextAreaElement).value).toBe("不能丢失的正文。");
  });
});
