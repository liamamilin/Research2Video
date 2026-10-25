import { describe, expect, it } from "vitest";
import type { BilibiliCategory } from "@/api/platforms";
import { recommendBilibiliCategories, searchBilibiliCategories } from "./bilibili-categories";

const categories: BilibiliCategory[] = [
  { tid: 801, name: "科学科普", path: "知识 / 科学科普", description: "科学与自然知识" },
  { tid: 802, name: "计算机技术", path: "科技 / 计算机技术", description: "软硬件开发/人工智能/大数据/深度学习/IT运维等" },
  { tid: 803, name: "人文历史", path: "知识 / 人文历史", description: "历史、考古与人文" },
  { tid: 804, name: "科工机械", path: "科技 / 科工机械", description: "工程、机械与制造" },
  { tid: 805, name: "财经商业", path: "知识 / 财经商业", description: "经济、金融与商业" },
];
const recommend = (title: string, description = "", tags = ["AI", "快讯", "开发者"]) => recommendBilibiliCategories(categories, { title, description, tags });

describe("Bilibili category suggestions", () => {
  it.each([
    ["海床下七百米的坑：远古撞击怎样被还原", "北海地下的陨石坑与地质证据。", "科学科普"],
    ["DeepSeek 开源 DeepInfer：缺的是服务层", "API 与推理框架部署。", "计算机技术"],
    ["四件历史与考古新线索，最值得辨真伪的是日食传说", "古籍与博物馆史料。", "人文历史"],
    ["新工厂之外，美国军工高纯铝缺在哪", "制造与冶炼的工业环节。", "科工机械"],
    ["财报里的营收与利润，为什么影响股价", "企业的商业模式。", "财经商业"],
  ])("recommends %s from content instead of generic generated tags", (title, description, name) => {
    const first = recommend(title, description)[0];
    expect(first.category.name).toBe(name);
    expect(first.reason).toContain("标题包含");
  });

  it("uses current official IDs and never invents an unavailable category", () => {
    const result = recommend("陨石与撞击坑");
    expect(result[0].category.tid).toBe(801);
    expect(recommendBilibiliCategories(categories.filter(row => row.name !== "科学科普"), { title: "陨石与撞击坑", description: "", tags: [] })).toEqual([]);
  });

  it("ignores bibliography noise, generic AI tags and Latin substrings", () => {
    expect(recommend("第一期", "普通说明。\n\n报告补充资料：\nDeepSeek Agent API 开源框架", ["AI", "科技", "快讯", "开发者"])).toEqual([]);
    expect(recommend("capital campaign", "https://example.com/agent/api/deepseek", [])).toEqual([]);
  });

  it("shows the strongest source of evidence and caps alternatives at three", () => {
    const result = recommend("DeepSeek 推理框架", "陨石与撞击的科学解释。", ["历史", "考古", "财经"]);
    expect(result[0].category.name).toBe("计算机技术");
    expect(result.length).toBeLessThanOrEqual(3);
    expect(result.every(row => row.reason && row.score >= 3)).toBe(true);
  });
});

describe("category search", () => {
  it("matches category names, parent names, explanations and aliases", () => {
    expect(searchBilibiliCategories(categories, "知识 科普").map(row => row.tid)).toEqual([801]);
    expect(searchBilibiliCategories(categories, "IT运维").map(row => row.tid)).toEqual([802]);
    expect(searchBilibiliCategories(categories, "AI").map(row => row.tid)).toEqual([802]);
    expect(searchBilibiliCategories(categories, "科技 / LLM").map(row => row.tid)).toEqual([802]);
    expect(searchBilibiliCategories(categories, " ")).toHaveLength(5);
    expect(searchBilibiliCategories(categories, "完全不存在的词")).toEqual([]);
  });
});
