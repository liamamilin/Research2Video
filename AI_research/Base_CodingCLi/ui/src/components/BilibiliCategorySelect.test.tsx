import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CategoryContent } from "@/lib/bilibili-categories";
import { BilibiliCategorySelect } from "./BilibiliCategorySelect";

const categories = [
  { tid: 201, name: "科学科普", path: "知识 / 科学科普", description: "科学与自然知识" },
  { tid: 231, name: "计算机技术", path: "科技 / 计算机技术", description: "人工智能与编程" },
  { tid: 228, name: "人文历史", path: "知识 / 人文历史", description: "历史与考古" },
];
const resource = { data: { categories, updated_at: 100, stale: false }, loading: false, error: "", reload: vi.fn(async () => {}) };
afterEach(cleanup);

function Harness({ content, initial = 231 }: { content?: CategoryContent; initial?: number }) {
  const [value, setValue] = useState(initial);
  return <><BilibiliCategorySelect label="投稿分区" value={value} onChange={setValue} resource={resource} required content={content} /><output aria-label="选中的编号">{value}</output></>;
}

describe("searchable Bilibili category picker", () => {
  it("filters names and chooses the matching category without clearing a prior choice while typing", () => {
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "投稿分区" });
    expect(input).toHaveProperty("value", "科技 / 计算机技术");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "历史" } });
    expect(screen.getByText(/找到 1 个分区/)).toBeTruthy();
    expect(screen.queryByRole("option", { name: /科学科普/ })).toBeNull();
    expect(screen.getByLabelText("选中的编号").textContent).toBe("231");
    fireEvent.click(screen.getByRole("option", { name: /知识 \/ 人文历史/ }));
    expect(input).toHaveProperty("value", "知识 / 人文历史");
    expect(screen.getByLabelText("选中的编号").textContent).toBe("228");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("supports keyboard choice, Escape and IME composition without submitting a form", () => {
    const submit = vi.fn(event => event.preventDefault());
    render(<form onSubmit={submit}><Harness /></form>);
    const input = screen.getByRole("combobox", { name: "投稿分区" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "知识" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(screen.getByLabelText("选中的编号").textContent).toBe("231");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByLabelText("选中的编号").textContent).toBe("228");
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(input);
    fireEvent.change(input, { target: { value: "科普" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveProperty("value", "知识 / 人文历史");
  });

  it("keeps the saved choice when a search has no results and allows explicit clearing", () => {
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "投稿分区" });
    fireEvent.change(input, { target: { value: "没有这种分区" } });
    expect(screen.getByText(/没有匹配的分区/)).toBeTruthy();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByLabelText("选中的编号").textContent).toBe("231");
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "清除投稿分区" }));
    expect(screen.getByLabelText("选中的编号").textContent).toBe("0");
  });

  it("explains a recommendation, applies it only on click and preserves manual choices as content changes", () => {
    const first = { title: "海床下的远古撞击坑", description: "科学家研究陨石撞击。", tags: ["AI"] };
    const view = render(<Harness content={first} />);
    expect(screen.getByLabelText("选中的编号").textContent).toBe("231");
    expect(screen.getByText(/标题包含“撞击”/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "采用推荐：知识 / 科学科普" }));
    expect(screen.getByLabelText("选中的编号").textContent).toBe("201");
    view.rerender(<Harness content={{ title: "DeepSeek 推理框架", description: "API 服务", tags: [] }} />);
    expect(screen.getByRole("button", { name: "采用推荐：科技 / 计算机技术" })).toBeTruthy();
    expect(screen.getByLabelText("选中的编号").textContent).toBe("201");
  });

  it("does not force a category when there is insufficient content evidence", () => {
    render(<Harness initial={0} content={{ title: "第一期", description: "普通介绍", tags: ["AI", "快讯"] }} />);
    expect(screen.getByText(/暂未找到明确推荐/)).toBeTruthy();
    expect(screen.getByLabelText("选中的编号").textContent).toBe("0");
  });
});
