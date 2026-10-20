# 选题池（Video topics）

状态：已实现（2026-10-20，架构第 2 期，见 `spec/video-architecture.md`）。

## 为什么

以前选题不存表，每次请求都重新解析两种报告：快讯发现、情报轮次第 07 步。来源名 `flash`、`opportunities` 写死在 6 处代码里。这带来三个问题：

- 新建的 Job 进不了选题池；
- 选题不能手动添加；
- 文稿页手填的题目不进选题池。

## 规则

1. **选题来源 = 在 YAML 里声明了 `topics:` 的 Job。** 数量不限，代码里不写任何来源名。
2. **报告按对账扫描导入 `topics` 表**（`state/video.db`）。Job 的报告无论是谁跑出来的，都会在下次读选题时导入：
   - 周期调度、命令行、情报轮次、控制台都算；
   - 只有大小或修改时间变了的报告才会重新解析；
   - 第一次扫描会把已有的报告全部导入。
3. **选题 id 不变：`<报告日期>/<编号>`。** 以前的文稿、批次和“不写”标记都指向同一批选题。这一点用真实数据核对过：迁移前后每个配方的待写 / 已写 / 不写数，以及两种来源每一期的 id、标题、字段，逐条一致。
4. **手动添加的选题**来源为 `manual`，id 为 `manual/<n>`。文稿页手填标题建稿时，也会先存成手动选题。

## 在 Job 里声明

```yaml
output: "output/topic_discovery/{date}_{name}.md"   # 必须带 {date}；{name} 和引擎写文件时一样替换
topics:
  alias: deep_topics   # 可选：配方和批次用的短名；不写就用 Job 路径（如 monitoring/深度选题）
  format: items        # flash | opportunities | items
  prefix: D            # 只给 items：选题编号 D1、D2…；1–3 个大写字母，不能是 F、C，各来源不同
  refresh: true        # 可选：允许自动流水线在当天没有报告时启动这个 Job（会花搜索额度）
```

三种 `format`：

| format | 报告长什么样 | 编号 |
|---|---|---|
| `flash` | `### 1.1 标题` 加 `- **字段**：值`，末尾有来源清单 | F1、F2… |
| `opportunities` | `### C1. 标题` 加 `- **字段**：值` | C1、C2…（照报告里写的） |
| `items` | 任意 `###` 标题后面跟 `- **字段**：值`；没有字段的标题（概述、小节标题）不算选题 | prefix + 序号 |

声明有错时，这个来源照样列出来、标明原因，但不导入。常见错误：

- format 不认识；
- `items` 没写 prefix，或用了 F / C；
- 两个来源重名，或编号前缀相同；
- output 里没有 `{date}`；
- 写了不认识的字段。

同一天跑了两次时，引擎会写出 `_1`、`_2` 后缀的文件，这里只读那天最新的一份。

现在声明了来源的 Job：

- `monitoring/ai_flash_discovery`：`alias: flash`，`refresh: true`。
- `practical_ai_intelligence/07_product_content_opportunities`：`alias: opportunities`。它是情报轮次的一步，不允许自动流水线单独启动。

新模板 `jobs/_templates/_topic_discovery.yaml`（「选题发现」）建出来的 Job 默认带 `topics: {format: items, prefix: D}`，不定时，也不会被自动流水线启动。

## 配方怎么用

配方的 `topic.sources` 写来源名（alias 或 Job 路径），也可以写 `manual`。它只决定自动流水线从哪挑题，以及选题页默认显示哪个来源；手动建稿不受影响。

写了一个没有 Job 声明的名字时，配方照样能保存，但会在两处提示：

- 配方接口（列表和校验）返回 `warnings`；
- 选题页标红。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/video/topics/sources` | 所有来源：Job、格式、最新一期、报告条数、声明错误、最新一期读不出选题的原因；外加 `manual` |
| GET | `/api/video/topics?source=&round=` | 某来源某一期的选题（默认最新一期）；`source=manual` 列出所有手动添加的选题 |
| POST | `/api/video/topics` | 手动添加：`{title, angle, audience, fields}` |
| GET/POST | `/api/video/topics/discovery?source=` | 只对 `refresh: true` 的来源：查看状态，或现在就跑它的 Job |

## 界面

选题页先选格子（配方），再在来源标签之间切换：配方声明的来源，加上「手动添加」。盘点和对比这两种格子没有「手动添加」标签。

- 「手动添加选题」：填标题、角度、受众。
- 允许刷新的来源有「生成今天的选题」按钮，确认时显示上次用了几次搜索。
- 其他来源给出它的 Job 链接。

## 没做的

- 选题不能编辑。导入的选题跟着报告走，报告重写后会整体替换；文稿里存的是建稿时的那一份。
- 早期 23 份第 07 步报告是旧格式，读不出选题（以前也读不出）。来源列表只报告最新一期的问题。
