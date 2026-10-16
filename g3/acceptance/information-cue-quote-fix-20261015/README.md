# 简洁信息动效 INFORMATION_CUE_QUOTE 修复：真实产品验收

日期：2026-10-15。触发：用户报告任务 `eda487c3` 失败，页面显示 `本次制作未完成 / INFORMATION_CUE_QUOTE: c1_n4`。

## 缺陷

`INFORMATION_CUE_QUOTE` 让第 1 章的分镜规划连续 3 次被本地校验拒绝，整单失败，此前 14 次模型调用的结果作废。

根因是模型把该段旁白的**素材原句**抄进了 `cue.quote`：

- 旁白 `c1_n4`：`Gemini 4 Argon 面向开发者与企业全面开放，Gemini 应用同时上线可持续后台执行的任务模式。`
- 模型写入的 quote：`并在 Gemini 应用中上线可持续后台执行的任务模式`
- 该段 `source_quotes`：`…并在 Gemini 应用中上线可持续后台执行的任务模式`

发给规划器的载荷里，每段同时带 `text` 与 `source_quotes`，两者语义相近却逐字不同（本任务 93 段中 68 段如此），提示只用一句「两者不可混淆」。`cue.quote` 必须落在旁白 `text` 内，于是必然被拒。

第二个放大因素是重试反馈只有 `INFORMATION_CUE_QUOTE: c1_n4`，不含场景、目标或正确文本，模型只能盲改：第 2 次修好 `c1_n4` 却弄坏 s4 开场 cue，第 3 次退回原错误。

## 修复

- `g2/information_motion.py`：发给模型的 `narration_plan.segments` 只保留 `id/text` 等字段，剥离 `source_quotes`；提示明确要求 `cue.quote` 逐字取自对应段 `text`（可能已被改写，不得复制素材原句），并说明开场必须是段首连续片段。
- `INFORMATION_CUE_QUOTE` / `INFORMATION_OPENING_CUE` 的失败信息改为携带场景、目标、错误引文、疑似来源（素材原句）与该段旁白全文，重试反馈可据此修正。
- `InformationFault`/`G2Fault`/`DiagramPlanFault`/`ContentFault`/`InputFault` 增加 `message` 字段，包装时不再拼接已带前缀的字符串，消除 `CODE: CODE: …` 重复。
- `PROMPT_VERSION` 升为 `g2-golden-general-v1.3`，便于区分修复前后的调用记录。

## 真实复验

- 用与失败任务完全相同的素材（10,065 字符）重新提交 `information_motion` + `content` 任务 `c94813f5-ce48-4f4a-9a5c-f5232888df40`。
- 结果 **COMPLETED**：8 章、47 场景、**623.808 秒 / 18,714 帧**，1920×1080 / 30 fps，H.264 + AAC 48 kHz 双声道，73,585,060 bytes。
- **`INFORMATION_CUE_QUOTE` 失败 0 次**。信息动效唯一一次重试为 `INFORMATION_OPENING_CUE`，因反馈已给出所需 target 与段首原文，当场收敛。
- 解码 PASS；解码音轨与原 TTS 相关系数 **0.99994**；8 章覆盖 35/35 规划知识点。
- 22 次真实 DeepSeek 调用，费用保守估算上界约 **$0.209**（非实际账单）。
- 抽帧检查 c2_s2、c4_s4 及 c1 相关场景：文字未溢出，卡片逐个显现。

## 证据

- `before-failure-report.json`、`before-validation_retries.jsonl`：修复前的失败记录（保留任务 `eda487c3` 数据作对照）。
- `validation_retries.jsonl`、`qa.json`、`media-summary.json`：修复后任务的重试、媒体 QA 与汇总（含 MP4 哈希）。
- `scene-c2-s2.png`、`scene-c4-s4.png`：全分辨率抽帧。
- 成片：`g3/data/jobs/c94813f5-ce48-4f4a-9a5c-f5232888df40/attempts/1/g2-run/artifacts/G2_自动生成.mp4`。

## 离线回归

`python3 -m pytest g1/tests g2/tests g3/tests g4/tests -q`：全部通过（`tests/test_final_release.py` 的清单覆盖失败是修复前已存在，与本次无关）。`python3 g0/verify_design_snapshot.py`：45 份文件哈希匹配、23 项设计测试通过。`git diff --check` 通过。新增回归覆盖：载荷不含 `source_quotes`、抄错素材原句被拒且报出旁白全文、开场 cue 报出所需 target、错误信息不重复 code。

## 边界

结构覆盖与媒体 QA 不代替语义正确性判断。本次证明同一类素材不再因 `INFORMATION_CUE_QUOTE` 整单失败，尚未证明所有题材一次通过；措辞与画面观感仍需真实观看判断。
