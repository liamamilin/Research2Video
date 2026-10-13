# M0｜AutoVideo V0.1 实测基线

**Stage:** M0

**Status:** PARTIAL

**基线日期：** 2026-10-13

**基线代码提交：** `0d52757`（G4 clean-install 验证）
**阶段授权：** 用户已明确验收 G4 并要求进入 M0。

## 目标与结论

冻结 G4 Golden-3 的真实生成产物、Video IR v0.2、测试样例、依赖锁和已知问题，供后续实验作对照。M0 的工程证据已整理；按 16 号路线要求的人工盲评尚无 0–5 评分，因此当前为 **PARTIAL**，不能据此进入 H1。

本报告的运行统计只覆盖 G4 的 Golden-3 五次生成尝试（3 次成功、2 次失败），不混入 G0/G2 探索运行、G3 UI 演示任务、QC 负例或 clean-install 重放。样本量很小，只描述本次实验，不代表生产成功率。

## Golden-3 基线

| 案例 | 最终媒体 | 成功尝试耗时 | 成功尝试费用上界 | 计入失败重试后的单例费用上界 | 结果 |
|---|---:|---:|---:|---:|---|
| A 通信噪声 | 19.968 秒，599 帧 | 85.877 秒 | $0.0090840 | $0.0219144 | PASS |
| B 批处理与流处理 | 17.835 秒，535 帧 | 99.430 秒 | $0.0126021 | $0.0126021 | PASS |
| C 虚构纸飞机数据 | 19.477 秒，584 帧 | 81.225 秒 | $0.0079275 | $0.0177441 | PASS |

三例均为真实 DeepSeek `deepseek-flash` + 本地 Kokoro-82M v1.0（`zf_xiaoxiao`）+ 固定 Video IR v0.2/Remotion renderer，无 mock。媒体为 1920×1080、30 fps、H.264 + AAC、48 kHz 双声道；三例均通过完整解码和 G4 语义/视觉 QC。视频、接触表、QC、TTS、字幕、IR、日志和请求记录均保存在各自运行目录中，具体路径与 SHA-256 见 [`M0_ARTIFACTS.json`](M0_ARTIFACTS.json) 和 [`g4/G4_MANIFEST.json`](../g4/G4_MANIFEST.json)。

## 成功率、失败与成本/耗时口径

- **生成尝试：** 5 次；3 次通过、2 次失败，观察到的尝试成功率为 **3/5（60%）**。首轮成功为 **1/3（33.3%）**；A、C 各重试一次后均成功，最终 Golden-3 完成率为 3/3。N 太小，不能外推。
- **失败分类：** 沙箱 DNS/网络失败 1 次（1 个 API 请求，费用上界 $0.0128304，0.023 秒）；非法 `bar:*` 动作目标导致结构化校验失败 1 次（3 个 API 请求，费用上界 $0.0098166，48.865 秒）。修复和失败证据仍保留在 `g4/runs/failed/`。QC 路由负例是故意注入的测试，不计作生成失败。
- **模型 API 费用上界：** 五次尝试合计 **$0.0522606**；仅成功尝试平均 **$0.0098712/条**；将失败重试计入每条最终交付视频后平均 **$0.0174202/条**。这是按未命中缓存的价格和记录 token 用量得出的保守上界，不是账单实扣金额。
- **耗时：** 成功尝试平均 **88.844 秒/条**；含失败重试后平均 **105.140 秒/条最终交付视频**。耗时取每次 `commands.jsonl` 的 `all` 端到端命令计时；失败与成功尝试分别计入。它是本机样片流水线时间，不含人工操作等待。
- **未计量费用：** Kokoro 云调用费用为 $0；本地 TTS、渲染的 CPU/电力成本为 **NOT MEASURED**。DeepSeek 数值未与供应商账单独立核对。

## 冻结边界与依赖

- 数据契约固定为 **Video IR v0.2**：顶层 schema、六个组件 schema、`component-registry-v0.2.json`；Golden-3 输入和主要测试样例已纳入 artifact hash 清单。IR v0.1 仍为历史归档。
- 运行环境实测：macOS 26.6.2 arm64、Python 3.10.13、Node 22.21.1、npm 10.9.4、Chrome 155.0.8059.40；Remotion 4.0.535，模板提交 `a7a1befca086f72eaf721a8b85d9e5038453da94`，FFmpeg 7.0。
- 依赖锁和 Schema/fixture 的 SHA-256 固定在 `M0_ARTIFACTS.json`；G4 发布 manifest 共 351 个文件，本轮逐项复核 **351/351 匹配**。
- `.env` 不进入基线 artifact 或 Git；真实 API key 不被读取、复制或写入报告。

## 主观质量评测

**PARTIAL：** 评议者 `liam`（2026-10-13）已给三条编码样片的前四项评分。按样片汇总，忠实性均分 **3.67/5**、可理解性 **3.33/5**、视觉成熟度 **2.00/5**、音质 **4.00/5**；样本是 3 条视频、1 位评议者，只作描述性人工意见。评议者回复打分前看过运行信息，因此这组分数**不计作盲评**。用户的 G4 “通过”是阶段/样片验收，不等于第五项已完成。

视觉成熟度均分低于 `08_QA_AND_ACCEPTANCE.md` 建议的 3/5 复盘线，是需要关注的质量信号；由于只有一位且非盲评，只作线索记录，不外推为总体质量结论。

“音画对应”评分仍待补充；当前理由为“无特别问题”。若需完成盲评基线，应由未查看运行信息的评议者独立评分，并填写五个维度及简短理由。评分表见 [`BLIND_REVIEW_FORM.md`](BLIND_REVIEW_FORM.md)。它隐藏运行标签和案例名称，但视频内容可能暴露题材，不应称为严格双盲。后续实验建议至少两位独立评议者。补齐有效盲评后再重判 M0；即使 M0 PASS，H1 仍需用户单独明确授权。

## 验证记录

- `g0/.venv/bin/python -m pytest g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q --disable-warnings`：首次收集失败，原因是 G0 虚拟环境未安装 G3 的 FastAPI；这是环境组合问题。
- `g0/.venv/bin/python -m pytest g4/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q --disable-warnings`：**42 passed**。
- `python3 -m pytest g3/tests -q --disable-warnings`：**11 passed**（使用已有 FastAPI 0.143.0 环境）。运行时测试合计 53 passed。
- `g0/.venv/bin/python -m pytest tests/test_design_contracts.py tests/test_roadmap_contracts.py -q --disable-warnings`：**19 passed**。
- G4 manifest 逐文件 SHA-256 复核：**351 checked, 0 missing, 0 mismatched**。
- 三例 G4 QC 沿用已提交报告中的 PASS 证据；本轮未重跑模型、TTS、渲染或媒体 QC。

## 已知风险与未验证项

- 授权审计仍有分发阻断项：eSpeak NG GPLv3 动态库/语音数据、当前 FFmpeg 的 GPL/x264/x265 配置、H.264/AAC 专利事项；Remotion 授权也需按实际使用主体复核。详见 [`g4/G4_LICENSE_AUDIT.md`](../g4/G4_LICENSE_AUDIT.md)。G4/M0 技术验收不表示商业分发许可已解决。
- 仅验证单机 10–20 秒 Golden-3；产品目标 45–90 秒、长片、并发、多用户、生产稳定性和真实强杀恢复均未证明。
- 本地计算成本、独立账单、盲评结果均未测；源文会发送到 DeepSeek API。

## M0 交付与恢复点

新增文件：[`STAGE_PLAN.md`](../STAGE_PLAN.md)、本报告、[`M0_ARTIFACTS.json`](M0_ARTIFACTS.json)、[`BLIND_REVIEW_FORM.md`](BLIND_REVIEW_FORM.md)、`M0_REVIEW_KEY.json` 和 `review/clip-01.mp4` 至 `review/clip-03.mp4` 三个只读别名。没有修改产品代码、Video IR、provider 或依赖。恢复点为基线提交 `0d52757`；这些 M0 文件作为独立提交记录在 Git 历史中，提交号见本轮交付记录。当前建议：补齐盲评分项，重新评估 M0；在此之前停止于 M0，不启动 H1。
