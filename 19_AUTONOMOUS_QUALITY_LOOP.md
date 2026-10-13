# 19｜自主审片与有限自动修复设计

**注意：** 自动审美与视觉理解不是机械验证。评价模型可能误判，评分不等于作品事实。系统应能诚实地失败。

## 1. QA 划分

- **Hard Gate**：文件打不开、缺视频/音轨、时长不合、素材缺失、边界越界、严重字幕遮挡、非法动作、错误图表数据。失败即不允许发布为完成品。
- **Evidence Gate**：解说稿内容是否来自原文，数字、实体、因果词是否被改写，视觉是否支持陈述；低置信或矛盾应明确标记，必要时保守改写或 FAIL。
- **Perceptual Review**：版面是否拥挤、画面是否真的解释、节奏是否拖沓、跨镜头色彩与主题是否一致、音色是否自然。分数需用真实案例校准。
- **Preference Review**：风格喜好，不能直接等同于“正确/错误”，默认只作为诊断建议。

## 2. 审查输入

单镜头：`source span + narration + visual_intent + beats + aligned audio + scene frames / short clip + style bible + asset manifest`。

全片：`shot list + transitions + decoded final video + mix analysis + QA history`。

不可只把 storyboard JSON 交给评委模型，再假装“看过最终视频”。至少抽取动作前后关键帧；对视频模型生成的运动，必须对真实视频片段或足够密集的时序样本评估。

## 3. 可执行的发现 → 修复流程

```text
Verify Media → Check Hard Gates → Semantic Review → Perceptual Review
       ↓ Fail or Poor Quality
Issue {scope, severity, evidence, target, suggested_fix, confidence}
       ↓
Repair Planner {candidate fixes + expected cost + blast radius}
       ↓
Cheap/local change preferred → regenerate only target scene/segment
       ↓
Compile/Render Target → Check Original Issue + Full Regression
       ↓
PASS / retry under cap / fallback / fail with explicit reason
```

候选修复顺序：改排版/时长参数 → 修改动作/镜头结构 → 新增或替换素材 → 重新生成视频镜头 → 修改旁白（最后手段；涉及事实必须重新审稿和音频）。

## 4. 问题表示协议（草案）

```json
{
  "issue_id": "qa-0001",
  "kind": "SUBTITLE_OVERLAP",
  "severity": "BLOCKER",
  "scene_id": "scene_03",
  "time_ms": [9200, 11600],
  "evidence_assets": ["qa/frame_0097.png"],
  "description": "字幕覆盖图表数据标签",
  "repair_options": ["reflow_labels", "move_visual_region"],
  "confidence": 0.97
}
```

仅供未来讨论，不代表当前 Schema 支持上述字段。应区分 `BLOCKER / MAJOR / MINOR / PREFERENCE`；每个 issue 能定位到场景/时间/证据，不能只输出“整体美感不足”。

## 5. 自动修复必须有的守卫

1. 同一 issue 的修复尝试次数上限，默认先评估 **2 次**作为研究基线。
2. 单任务费用和时间上限；估算不可得时不能无限调用付费模型。
3. 修复前 snapshot 与 artifact hash；新成品可回滚，历史最优版本可恢复。
4. 修复只能修改被批准的工作区，不许写入其他项目或系统服务。
5. 原始问题通过后还需复查受影响的跨场景衔接；防止局部好转、全片恶化。
6. 未达到硬门槛就 `FAILED`，允许向用户返回尚未合格的预览，但不能标作完成。

## 6. 评价器的可靠性

- 研发时对同一作品使用盲评人的独立分数，估计模型的漏检与误报；当人类评分差异很大时，把样例标为主观分歧。
- 明确区分“模型没发现问题”与“作品没有问题”。
- 保存 QA prompt、评估模型、版本和每次 repair 的效果；不能在没有对照时声称模型升级提高了质量。
- 优先采用规则和可测证据解决确定性错误；多模态审查留给解释力/审美等难机械化问题。

## 7. H3 的实证验收

在冻结的缺陷数据集上比较：

- `before` 与 `after` 真实媒体，而不是模型自己的文字报告。
- 可定位问题的召回率、误报、修复成功率、回归失败率。
- 平均/最坏调用开销和每条视频额外成本。
- 对用户不可接受错误，系统是否明确 FAIL。

**没有定义好的评测集与可追踪的修复证据，不能宣称已经完成“自主审片”。**
