# 写稿输入门禁与成稿收口（2026-11-20）

三处收敛点的执行约定。防回归用代码门禁；本文只定边界。

## 1. 作者请求白名单

作者 system/user 里只放：

- 观众要解决的问题与收获
- 可用证据（报告正文、原始文章内容）
- 栏目 / 配方的观众向目标

不得进入作者请求：

- 合集与单条的制作分工（如「同批合集」「合集只」「发布概况只作必要背景」「与单条共同规划」）
- 采集书签（`材料编号`、`采集状态`、单独的「来源角色」备忘行）
- 对「其他视频 / 合集续集」的制作说明

实现：`core/video/author_input_gates.py`；写稿前清洗方向；测试断言请求文本。

联合规划只分配材料与问题；`writing_direction` / `viewer_takeaway` 只写观众向口径。制作备忘留在规划 JSON，不进口播方向。

## 2. 净化只有两段

| 阶段 | 职责 | 入口 |
|------|------|------|
| 输入清洗 | 剔采集元数据等书签 | `report_material.for_narration` |
| 成稿收口 | 取材套话、续集旁白、画面说明、标题工作名 | `narration_stance.settle_sections`、`writer.spoken_only`、`writer.viewer_section_title`（均在 `writer.write` → `_finalize` / `settle` 路径） |

`polish` 只处理「有具体命中才改」的表达问题，不另开一套 stance 语义模块。

**新口播毛病只加进上表对应入口，禁止新建第 4 条净化路径。**

### 成稿收口清单（索引）

| 毛病 | 处理处 |
|------|--------|
| 材料点了 / 公开材料说 / 材料显示 | `narration_stance._without_source_attrib` |
| 别的视频讲的是 / 今天不重复讲上线 | `narration_stance._without_sequel_meta` |
| 材料里没有…、自我免责 | `narration_stance.repair` / `defects` |
| 画面里可以… | `writer.spoken_only` |
| 章节「开场：…」「结尾」 | `writer.viewer_section_title` |

## 3. 试验与主链

- 隔离证据放 `state/validation/<试验名>/`（外接盘），含简短 README：问题、改法、采纳/放弃
- 未采纳改动不长期挂在主链 WIP
- 主链改动用 pytest +（需要时）隔离写稿请求抽检验收

## 停损

同一收敛点连续两轮没有新回归、却要再扩系统时：停，只改清单或白名单，不新开模块。净化层物理合并（阶段 3）仅在连续两轮「同病多处修」时再开。
