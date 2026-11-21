# 成片效果优先：第一轮取舍

本轮以真实视频的可读性和旁白同步为目标。保留了两个小改进，没有继续扩展复杂关系图，也没有将失败的截图框选做成生产能力。这一轮只证明局部观看问题有所改善，不能代表整片已打磨完成，更不代表留存或涨粉已验证。

## 保留并接入

1. 数字变化画面先建立可读的比较，再随旁白加强强调。原来未讲到的数字只有 18% 不透明度，部分真实片段持续数秒几乎看不清；现在保持 65% 的基础可见度，原来的逐值强调继续工作。没有新增组件、题材判断或模型调用。
2. 变化标签等待自己的比较。真实降价片段中，输出一行的“减少 10”误用了前面输入价格“10”的时刻，提前约 3.12 秒出现。现在差额匹配排除该行比较之前的数字，避免另一行相同数字提前触发。另用库存/订单、耗时以及新值先讲的结构验证，没有依赖品牌或该条视频。

生产改动仅涉及 `g1/renderer/src/Root.tsx` 的可见度和 `g2/information_motion.py` 的变化提示时机；行为回归补在 `g2/tests/test_explanation.py`。仓库根 `AGENTS.md` 记录了目标优先和更换办法的协作原则。其他已有修改保留。

## 尝试与放弃

| 办法 | 实际观察 | 本轮决定 |
| --- | --- | --- |
| 统一放大数字列表 | 小字变大，但计算前提与结果仍然同等抢眼 | 不接入；字号调整不能替代重点组织 |
| 用现有变化卡突出月账单 | 150→300 更直接，但最初版本前半段很淡；属于人工隔离原型 | 保留表达方向，不写案例转换规则 |
| 将截图整图铺宽 | 容器变宽，窄文档内容依然小，白边占用更多 | 放弃 |
| OCR 每段匹配一个位置 | 标题更清楚，但旁白讲到列表时还停在标题 | 放弃 |
| OCR 逐项精确指向 | 出现重叠框、碎片化移动；原型还超过现有三个区域的合同 | 放弃，不扩合同、不继续补标签规则 |
| 让正常编排自动选择更简洁的表达 | 用户批准调用现有服务后，DeepSeek 返回 HTTP 402，未得到有效编排结果 | 提示词保留在实验脚本，未接入生产 |

因此，“截图还不清楚”是尚未解决的观看问题；本轮没有把框选和复杂镜头当成必须做成的能力。下一步可以比较整体截图配一个清晰局部、分屏展示输入/结果等少量稳定画面，但仍需先看片再决定。

## 真实视频证据

最初检查了数字快讯、MarkItDown 教程、冰块科普、录音工具对比四条真实视频。最终保留改动又在三种真实数字片段上做了严格 A/B：

| 内容结构 | 片段 | 时长 |
| --- | --- | --- |
| 双行数值上升 | `final-news-card_2_p2` | 5.63 秒 |
| 双行数值下降、存在重复数字 | `final-decrease-card_2` | 21.93 秒 |
| 单行小数百分比变化 | `final-rates-card_38` | 13.83 秒 |


- news：[改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-20261111/final-news-card_2_p2/before.mp4) · [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-20261111/final-news-card_2_p2/after.mp4)
- decrease：[改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-20261111/final-decrease-card_2/before.mp4) · [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-20261111/final-decrease-card_2/after.mp4)
- rates：[改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-20261111/final-rates-card_38/before.mp4) · [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-20261111/final-rates-card_38/after.mp4)

所有产物位于 `autovideo/g3/data/validation/viewing-effects-20261111/`。每个最终目录提供 `before.mp4`、`after.mp4`、两版渲染输入、原始语音锚点、渲染命令/日志、解码日志及逐秒对照图。两版均用本轮开始时的同一渲染基础制作，避免把历史版本变化误算为本轮收益。原任务、音频、图片与旧失败尝试都保留。

人工原型和可接入结果严格区分：`news-card_3-number-video` 是比较表达方式的原型；`final-*` 才是本轮生产改动对应的受控对照。失败渲染的 `attempt1-*` 记录保留：隔离脚本最初遗漏了编译后附加的音频和字幕路径，修正脚本后重渲染，没有修改原声或生产管线。

## 验证与边界

- 完整离线回归：1062 passed，1 skipped。首轮沙箱内两项本地端口测试受权限限制，放开本地端口后通过；同步逻辑改动后的完整回归也通过。
- `git diff --check` 通过。
- `verify.py --final` 检查场景文字、数据、时间范围和旁白不变；只允许视觉提示时间变化，并检查真实 MP4 解码、视频时长、音轨一致性和渲染代码一致性。三组全部通过，音频波形相关系数均约为 1.0；结果保存在 `final-verification.json`，本目录 `verification.json` 保存摘要和视频哈希。
- 视觉判断来自原片和试片的时间轴抽帧对照，音轨一致性由同源音频与波形对照支持；没有声称做过观众盲评。
- 科普片的长时间静态人物、截图对象小、内容层级和开场吸引力仍需独立打磨。自动重编排本轮受服务 HTTP 402 阻塞，未用人工修改样例冒充自动能力。

复现入口：`prototype.py` 比较隔离表达方案；`render_final.py --case <name> --scene <id>` 用既有真实任务重渲染受控 A/B；`verify.py --final` 验证最终产物。`plan_candidates.py` 是尚未验证的外部编排实验，运行会向已配置服务发送三个片段的上下文。
