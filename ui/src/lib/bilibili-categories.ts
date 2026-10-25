import type { BilibiliCategory } from "@/api/platforms";

export type CategoryContent = { title: string; description: string; tags: string[] };
export type CategoryRecommendation = { category: BilibiliCategory; reason: string; score: number };

const RULES: Record<string, string[]> = {
  科学科普: ["科学", "科普", "陨石", "撞击", "天文", "宇宙", "行星", "太空", "航天", "地质", "海洋", "物理", "化学", "生物", "量子", "黑洞", "医学", "医疗", "癌症", "免疫", "基因", "气候", "地震", "火山"],
  计算机技术: ["人工智能", "大模型", "机器学习", "深度学习", "神经网络", "编程", "算法", "推理", "框架", "开发者", "开发", "开源", "服务器", "数据库", "编译", "代码", "算力", "deepseek", "deepinfer", "chatgpt", "llm", "llms", "agent", "agents", "api", "http", "python", "gpu"],
  软件应用: ["软件", "应用教程", "使用教程", "使用指南", "安装教程", "工具测评", "办公软件", "工作流", "剪辑教程", "剪映", "photoshop", "excel", "obsidian", "notion"],
  数码: ["数码", "手机", "平板", "笔记本", "耳机", "相机", "显示器", "电脑测评", "显卡测评", "处理器测评", "跑分", "iphone", "macbook"],
  科工机械: ["机械", "工厂", "制造", "军工", "高纯铝", "冶炼", "生产线", "航空", "飞机", "机器人", "材料工艺", "半导体制造", "工业"],
  人文历史: ["历史", "考古", "古代", "文物", "古籍", "博物馆", "文明", "传说", "史料", "遗址", "朝代", "帝国", "军史"],
  财经商业: ["财经", "商业", "经济", "金融", "财报", "营收", "利润", "估值", "融资", "股价", "股票", "投资", "产业链", "商业模式", "供需"],
  "社科·法律·心理": ["社会学", "法律", "法条", "司法", "心理", "哲学", "人格", "认知心理"],
  职业职场: ["职场", "职业", "面试", "简历", "求职", "办公技巧", "工作效率"],
  "AI影像": ["ai影像", "ai短片", "ai电影", "文生视频", "图生视频", "ai动画"],
  汽车知识科普: ["汽车", "发动机", "变速箱", "底盘", "自动驾驶"],
  新能源车: ["新能源车", "电动汽车", "特斯拉", "比亚迪", "动力电池"],
  美食制作: ["烹饪", "菜谱", "做菜", "烘焙", "料理", "食谱"],
  健身: ["健身", "增肌", "减脂", "力量训练", "运动训练"],
};

function contains(text: string, term: string): boolean {
  if (/^[a-z0-9_]+$/i.test(term)) {
    return new RegExp(`(^|[^a-z0-9_])${term}(?=$|[^a-z0-9_])`, "i").test(text);
  }
  return text.toLocaleLowerCase().includes(term.toLocaleLowerCase());
}

/** Rank only available official categories; generic generated AI tags carry no topic evidence. */
export function recommendBilibiliCategories(categories: BilibiliCategory[], content: CategoryContent): CategoryRecommendation[] {
  const description = content.description.split(/\n\s*(?:参考来源|报告补充资料|资料来源|来源清单)/u)[0]
    .replace(/https?:\/\/\S+/gi, "").slice(0, 800);
  const fields = [
    { name: "标题", text: content.title, weight: 5 },
    { name: "标签", text: content.tags.filter(tag => !/^(ai|科技|快讯|专题速览|大众观众|开发者)$/i.test(tag.trim())).join("，"), weight: 3 },
    { name: "简介", text: description, weight: 2 },
  ];
  return categories.flatMap(category => {
    const terms = [...new Set([...(RULES[category.name] || []), ...category.name.split(/[·/、\s]+/u).filter(word => word.length >= 2)])];
    let score = 0;
    const evidence: { field: string; terms: string[] }[] = [];
    for (const field of fields) {
      const matched = terms.filter(term => contains(field.text, term));
      if (matched.length) {
        score += Math.min(matched.length, 4) * field.weight;
        evidence.push({ field: field.name, terms: matched.slice(0, 2) });
      }
    }
    if (score < 3) return [];
    const first = evidence[0];
    return [{ category, score, reason: `${first.field}包含${first.terms.map(term => `“${term}”`).join("、")}，与${category.path}相关。` }];
  }).sort((a, b) => b.score - a.score || a.category.path.localeCompare(b.category.path, "zh-CN")).slice(0, 3);
}

export function searchBilibiliCategories(categories: BilibiliCategory[], query: string): BilibiliCategory[] {
  const words = query.trim().toLocaleLowerCase().split(/[\s/／]+/u).filter(Boolean);
  return categories.filter(category => {
    const aliases = [...(RULES[category.name] || []), ...(category.name === "计算机技术" ? ["ai"] : [])];
    const text = `${category.path} ${category.description} ${aliases.join(" ")}`.toLocaleLowerCase();
    return words.every(word => text.includes(word));
  });
}
