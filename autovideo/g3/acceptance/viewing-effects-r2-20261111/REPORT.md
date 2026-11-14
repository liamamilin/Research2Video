# 视频观看效果打磨：第二轮，2026-11-11

本轮目标是让观众看清旁白正在解释的截图。真实对照显示，把空间还给原图、减少重复旁白的侧栏，比继续增加识别和镜头规则更有效。已将这一做法接入正常编译与渲染流程；这是局部画面可读性的改善，不代表整片节奏、留存或涨粉已经改善。

## 从观看问题到取舍

上一轮留下的主要问题之一：截图主体偏小，右侧长段文字与旁白、字幕重复争夺注意力。教程网页尤其明显，标题、列表、表格虽都存在，却需要费力辨认。

本轮比较了原版、扩大整图展示区、扩大展示区并收掉纯白外围三个方向。只扩大区域仍会浪费在截图自己的大块白边上，因此保留第三种。实现只计算所有非白像素的包围区域并保留余量；近白色线条、压缩噪声也视为内容。原文件不改写，不识别文字，不猜测主体，不按视频标题或案例 ID 分支。

早期样片的底部全局说明离字幕太近，最终去掉阅读模式中的重复全局说明。单图保留场景标题；多图保留各自名称及已有的主次切换。未采用“更多镜头变化”作为效果指标，也没有再扩展上一轮收益不稳定的 OCR 镜头方案。整图加局部小窗仅曾考虑，未实际测试，不作为比较结论。

## 可直接观看的受控对照

四组均使用原讲稿、原音频、原字幕、原时间轴。改后由未改写的原始 Video IR 经过正常编译产生，不是人工为样例重写分镜。改前使用本轮开始时冻结的渲染器（已包含上一轮改动）。

| 结构 | 时长 | 改前 | 改后 | 五个时间点 |
| --- | --- | --- | --- | --- |
| 教程网页：标题、列表、表格 | 18.53 秒 | [改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-tutorial-card_3/before/video.mp4) | [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-tutorial-card_3/after/video.mp4) | [时间轴对照](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-tutorial-card_3/comparison.jpg) |
| 手机入口截图 | 13.10 秒 | [改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-comparison-card_3/before/video.mp4) | [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-comparison-card_3/after/video.mp4) | [时间轴对照](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-comparison-card_3/comparison.jpg) |
| 三栏密集总结 | 14.23 秒 | [改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-comparison-card_11/before/video.mp4) | [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-comparison-card_11/after/video.mp4) | [时间轴对照](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-comparison-card_11/comparison.jpg) |
| 另一条视频：手机＋流程双图 | 20.53 秒 | [改前](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-recorder-card_2/before/video.mp4) | [改后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-recorder-card_2/after/video.mp4) | [时间轴对照](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-recorder-card_2/comparison.jpg) |

观察结果：

- 教程网页：标题、列表及表格明显放大，同时保留完整内容，收益最明确。
- 手机截图：入口更突出，保留完整手机界面；没有额外增加游走镜头。
- 三栏总结：整体更大、干扰减少，但低分辨率的密集小字仍不适合逐字阅读。对此停止追加分栏识别或特定镜头规则。
- 双图验证：选取另一条真实视频的“手机入口＋流程图”组合，没有为它调规则，原主次切换仍可用，流程图获得更多空间。它是新视频及新组合，不是全新素材；其中图片与前面的比较视频有复用。

检查了四组各五个时间点，并查看了部分完整分辨率帧。这不是用户观看实验，也不把抽帧检查称为连续观看评价。

## 自动接入与适用范围

`g1/image_reading.py` 计算可见内容范围；`g1/video_ir.py` 在正常编译中生成内部 `image_reading` 元数据并按素材缓存。模型输入协议不变，原场景 props、旁白与节拍不变。`DirectedMaterials.tsx` 用该范围安排更大的阅读区，`Root.tsx` 协调标题及快速混合模式画面窗口。

范围限于没有显式局部焦点的单图、双图 `object_showcase` / `material_collage`。已有 focus 区域及超过两张图片的轮播沿用原路径。图片无法测量时沿用原布局；非白内容满幅时保留整图。这里不承诺一套形式适合所有截图。

正常 G2 renderer 会复制这些受信组件，传递完整编译结果；hybrid 路径也传递完整编译结果。另生成了快速混合模式的真实画面片段，验证新画面窗口不会切掉文档标题和表格：[混合模式画面](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/viewing-effects-r2-20261111/final-hybrid-tutorial-card_3/after/video.mp4)。该文件仅含画面，不含正常下游合成的旁白与字幕；本轮未重新运行完整混合模式成片任务。

## 验证证据

- 四组正常编译输出重新核对一致，原始内容与字幕未改，原素材 SHA 一致；可见非白像素均包含在展示范围内。
- 四组均为 1920×1080，帧数与原片段时间线一致，包含音轨，完整解码通过；解码音轨相关系数约为 1.0。
- 核心接入后全套离线回归：1066 passed、1 skipped。随后完成混合模式窗口和适用范围兼容调整，最终相关回归：48 passed。`git diff --check` 通过。
- 新测试覆盖淡色内容保留、透明背景、满幅/空白图、不可读文件降级、正常编译接入及已有焦点/多图边界。
- MP4、渲染日志、解码日志、输入 props、渲染器快照及对照帧保留在上述 runtime 目录。仓库内 `verification.json` 记录路径、哈希和校验结果。

## 复现与范围说明

本目录 `try_reading.py` 保留隔离样片实验；它不是正式工作流验证。`render_case.py` 用正常编译制作最终 A/B；`verify.py` 重跑媒体、内容与时间轴检查。运行目录依赖当前机器保留的原任务、素材和本轮 baseline 快照。为保留已有尝试，渲染脚本遇到同名输出目录会停止；复跑须先为新尝试指定独立输出位置，不能覆盖现有证据。

最终渲染源快照保留各次实际源码。后续小改动涉及 hybrid 专用窗口和未被四组样例触发的多图兼容路径；不声称所有快照与当前源码逐字相同。最终编译一致性及相关回归已重新检查。

本轮没有调用外部模型、没有发布视频，也没有修改已有任务成片。没有改研究端业务逻辑；工作区中其他既有修改保持原样。

## 完成审计与下一轮方向

本轮完成条件逐项满足：真实观看问题有定位；同内容同声同时间轴有对照；有可见改善才接入；不同结构和未参与调试的真实组合有验证；原内容与历史输出保留；实际 MP4、日志、测试和限制均有交付。

仍未解决的是密集截图逐字阅读、科普插画长时间缺少有效变化、开头吸引力及整片节奏。下一轮应重新看成片、选择影响最大的观看问题；如果密集截图需要逐字讲解，可比较用现有字幕/关键句配合整图定位或调整信息组织的方式，先做样片再决定。不把继续完善白边算法、复杂关系图或某种镜头系统列为长期目标。
