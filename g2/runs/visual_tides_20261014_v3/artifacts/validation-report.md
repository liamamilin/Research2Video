# 可控视觉叙事验证与下一步选择

日期：2026-10-14。范围：一条真实潮汐样片及开源布局工具可行性验证。

## 结论

现有 DeepSeek + 中文 TTS + Remotion 可以生成由内容决定的六场景二维解释动画，摆脱固定三场景文字卡。但本次需要人工指出概念表达与排版问题后，再让模型生成和修订；尚不能宣称任意素材一次生成即可达到可观看质量。

本轮样片验证到此收尾。下一步优先把一种表现形式做成完整产品能力：**流程与机制解释的动态图解**。推荐将 ELKjs 接入现有 Remotion，负责节点布局和连线路径，模型负责内容关系及讲解顺序。这是基于实际样片与工程可控性的判断；不同视频类型尚未做成片对照试验。

## 真实样片与媒体证据

- 最终视频：`runs/visual_tides_20261014_v3/artifacts/G2_自动生成.mp4`。
- QA：`runs/visual_tides_20261014_v3/artifacts/qa.json`。
- 执行日志：同目录 `logs/execution.log`、`logs/commands.jsonl`、`logs/visual-render.log`、`logs/llm_calls.jsonl`。
- 请求、响应、分镜和来源核验：`artifacts/llm_calls/`、`visual-plan.json`、`compiled-visual-plan.json`、`baseline-reference.json`、`model-plan-provenance.json`。
- 制品校验清单：`artifacts/manifest.json`，包括运行时渲染源码快照与本报告副本。

| 检查项 | 实际结果 |
| --- | --- |
| 成片 | 60.437 秒，1,813 帧，1920×1080，30fps，H.264/AAC |
| 场景 | 模型生成 6 个解释场景；只执行受控 JSON 图元及动作 |
| 媒体检查 | 全流解码 PASS |
| 原旁白保留 | 解码 PCM 与对照视频完全相同；TTS 相关系数 0.9999399678 |
| 最终修订范围 | 只修订 `s5` 小潮场景；其余五个场景 JSON 与 V2 相同 |
| 真实调用 | V3 复用 V2 的未编辑模型响应，新增一次真实单场景修订调用（34.376 秒） |
| 渲染 | 131.108 秒；媒体验证 2.554 秒 |

对照来自任务 `877e83a6-b9a7-41b2-a433-22a847a4e97c` 的 attempt 1，位于 `g3/data/jobs/.../attempts/1/g2-run/`。其讲稿、音轨、字幕及总帧数被复用，避免把声音或时长变化误判成画面提升。原样片与原音轨没有被覆盖。

原 WAV SHA-256：`051061afb2694e955b8bb2e70a49863aa5531d83abb59b917468fcba07cb13ad`。

最终 MP4 SHA-256：`66bf001f765eba4ab2399940c26f149e82c0f5e184c26c2809562474a726b806`。

## 过程与证据边界

1. `visual_tides_20261014_v1`：真实模型响应已返回（89.295 秒），大小写 ID 被初版过严规则拒绝；自动重试遇到 TLS EOF。调整安全标识符规则后，`v1_render` 原样重放响应，生成完整视频。
2. V1 画面审查发现水面形状、月球与潮汐轴线、海岸标记位置等表达问题。人工反馈保存在 V2 请求中；没有人工写分镜 JSON 或坐标。
3. `visual_tides_20261014_v2`：真实模型重新生成六场景（126.114 秒），连续水面与绑定地球旋转的海岸标记改善；小潮场景仍有主体接触和标签位置问题。
4. `visual_tides_20261014_v3`：真实模型仅修订小潮场景。反馈副本见最终运行的 `artifacts/review-feedback/`。抽帧检查显示主要天体分离、标签移开；技术 QA 通过。

本次是**有人提供反馈的模型生成实验**，不是一次调用全自动质量验收。图形为概念示意，不是物理仿真。关键帧审查与媒体 QA 不代替完整观看、理解效果和观感判断；没有新增独立评议要求。代码为显式调用的实验入口，尚未接入 Web 默认生成流程，也未改变 Video IR v0.2。

## 实现与测试

新增入口 `g2/visual_narrative.py`、视觉计划 schema、可信 React/SVG 渲染器和测试。校验包括完整有序旁白覆盖、来源引文、对象引用、父子关系环、动作区间、危险文本、单场景修订边界及时间到帧的转换。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  g1/tests g2/tests g3/tests g4/tests -q -p no:cacheprovider
git diff --check
```

本轮结果：**79 passed**，其中新增视觉叙事测试 **15 项**。可信渲染器 bundle 构建检查与 Python 语法检查通过。此前原有未提交修改均保留。

重新运行会创建新的独立运行目录，需要现有 `.env`、TTS/Remotion/Chrome 依赖及上述对照制品。最简新分镜验证：

```sh
AUTOVIDEO_G2_RUN_ID=visual_tides_next \
  g0/.venv/bin/python -m g2.visual_narrative \
  --baseline g3/data/jobs/877e83a6-b9a7-41b2-a433-22a847a4e97c/attempts/1/g2-run
```

该命令使用新的真实模型调用，默认输出名为 `G2_CaseA_通信噪声.mp4`，文件名不改变输入内容。指定 `AUTOVIDEO_G2_SOURCE_FILE` 时输出名为 `G2_自动生成.mp4`。已有运行目录不会被覆盖。

## 开源工具选择

| 工具 | 已核实的能力 | 对本项目的判断 |
| --- | --- | --- |
| [ELKjs](https://github.com/kieler/elkjs) | 接收节点尺寸及关系，计算位置和边的路由；不负责绘制和动画 | 优先接入现有可信 SVG 渲染器，减少让模型猜坐标的工作 |
| [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli) | 将文本图表定义输出为 SVG/PNG/PDF | 快速完整出图很合适；逐对象按旁白控制动画仍需适配 |
| [Manim](https://docs.manim.community/en/stable/tutorials/quickstart.html) | 通过 Python 场景制作形状变换及数学动画 | 数学/几何类专题的候选；当前优先流程表达，没有充分理由另接一套渲染流程 |
| [Motion Canvas](https://motioncanvas.io/docs/) | TypeScript 矢量动画、实时预览及旁白同步 | 有相关能力；改用它仍需解决内容关系与布局，当前缺口更适合先补布局工具 |

上述集成判断是工程推断，不是几套引擎在 AutoVideo 中的成片质量对比。

实际在临时目录安装并执行 **ELKjs 0.12.0**，顺序（5 节点）、分支（5 节点）、汇合（6 节点）三种结构均通过：节点无重叠、边有有效路由、相同输入两次布局几何结果一致。原始宽高分别为 1440×140、1582×232、1440×280；分支图适配 1560×580 舞台需约 0.986 缩放。首轮将“不允许缩放”设为断言而失败，随后改为记录实际适配比例；不将这个局部假设当作产品门槛。

验证文件位于最终运行的 `artifacts/tool-probe/`：`probe.cjs`、固定版本 `package.json`/lockfile、三份布局 JSON/SVG 与 `probe-result.json`。这些图关系由人工写作技术测试用例；**只证明布局接口可用，不证明模型内容质量或视频质量**。没有安装到业务依赖，也没有宣称已完成产品集成。

## 下一步：验证一种完整表现形式

输入素材 → 模型提取步骤/分支及对应原文 → ELKjs 排版 → Remotion 按旁白逐步显现、移动标记与重点高亮 → 真实有声成片。

先用两份不同内容、相同形式的素材（例如网页请求、订单履约）产出真实视频，复用同一套渲染与布局规则。检查讲述关系正确、画面确实解释旁白、文字可读、节奏可跟随，以及是否需要逐题人工修改坐标；保留失败与反馈证据。通过后接入现有 Web 主流程。

到达“完整、清楚、可观看且可复用”的水平即可转入整体使用验证，不继续按题目打磨每一种科学动画。ELKjs 不解决事实准确性、故事组织或复杂物理运动；这些仍需素材引用、内容规划和真实观看证据。
