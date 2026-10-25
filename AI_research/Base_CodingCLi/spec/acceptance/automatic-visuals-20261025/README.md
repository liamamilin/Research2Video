# 无人值守来源配图、图片理解与聚焦镜头

2026-10-25。本轮按 V1 自动闭环 → V2 区域定位 → V3 像素复核、恢复及真实出片实施，随后纳入用户补充的 Qwen3-VL 8B 和元素坐标镜头。
MP4 **输入素材**、录屏和自动发布仍留在后续独立路线。

## 已验证的行为

- 普通标准制作与自动批次共用 `video_render.create/process`，无需逐张获取、选择段落或确认图片。
- 按段落的事实／引用选择来源；原始意图与实际提交分别冻结；已发送或结果未知的提交不重新抓图。
- 先在实际截图中检查可读文字、价格及条件，再使用本地 Qwen3-VL 理解内容和匹配既有段落；失败保留文字画面。
- 记录 DOM 矩形及截图坐标变换。Remotion 用同一变换处理图片、放大和 SVG 标记，保留原图作整体参照。
- 支持整体后放大、重点框／圈／下划线／荧光标记、长段落连续聚焦、两图等宽并列；短段落限制焦点数量。
- 人工选择优先，改稿重新计算位置，按哈希去重、有时效缓存、调用预算、重启续登记及提交恢复均有回归覆盖。

## 真实公开网页与视频

来源为 [Playwright 官方截图说明](https://playwright.dev/python/docs/screenshots)。真实匿名 Chrome 获取页面，未替换网络或图片。
最初的真实链路中，DeepSeek 文本复核和本机 Apple Vision OCR 通过，实际 AutoVideo 工作进程使用本机 Kokoro `zf_xiaoxiao` 配音并生成 MP4。
1 个图片段落、2 个文字段落，27 份制品回传 SHA-256 检查通过，内容和包装 MP4 均完整解码。
见 [首轮真实链路](real-chain-qa.json)、[自动结果界面](automatic-panel.png) 和 [对应段落画面](mapped-screenshot.png)。

最后增加 Qwen3-VL 和 DOM 聚焦的完整链路，另存于 `state/automatic_visual_acceptance/20261025/real-chain-final/`；
最终结果见 [完整链路QA](final-chain-qa.json)、[页面整体](final-page-overview.png)、[实际聚焦帧](final-page-focus.png) 和 [图片理解界面](automatic-understanding-panel.png)。
自动准备27.989秒，1个配图场景、2个文字场景，27份制品回传校验，内容及包装MP4完整解码通过。
原成功、失败任务及模型反例均保留，未覆盖旧任务。

## 本机视觉模型：可用性与边界

本机为 M4 Pro、48GB 内存。下载 [Ollama 的 Qwen3-VL 8B Instruct Q4_K_M](https://ollama.com/library/qwen3-vl:8b-instruct-q4_K_M)，权重实际 6,190,971,795 字节。
`yanying-vision:8b` 共享同一份权重，专用参数 `num_ctx=8192`，实际运行上下文8192、GPU占用约6.9GB；见 [版本与运行记录](model-receipt.json)。
原本262K上下文在本机载入超时，首次失败记录保留；未改动其他模型的参数。

官方描述的图片理解、定位和文档能力不代表本项目已经可靠覆盖所有任务。
初次5例实测中，模型把图中的25美元与讲稿20美元矛盾解释为“修正讲稿即可”，原反例见 [价格误判](qwen-price-counterexample.json)。
因此 OCR、金额／币种／套餐／周期检查具有否决权；模型不改稿，不生成网址、选择器或代码。
加强提示和确定性守卫后，5例通过，实际模型调用约13–22秒；见 [最终本机评估](qwen-eval-qa.json)。
这是小样本集成验收，不是通用准确率评测。模型输出的聚焦框仍可能漏掉上下文，只作为建议保存；实际镜头使用测得的 DOM 矩形。

## 浏览器负例与镜头验收

[11例结果](browser-cases-qa.json) 使用**人工构造 HTML + 真实 Chrome + 真实本机 OCR**，用于复现正确月付价格、金额／币种／年付／套餐／产品／版本不符、登录、验证码、弹窗遮挡及截屏前价格变化。
这部分是受控测试夹具，不冒充真实产品官网；真实公开网站访问由上节单独验证。

另有2条真实 TTS／MP4：[镜头结果](focus-movies-qa.json)。连续聚焦片长13.12秒，并列片长11.008秒，均通过完整解码。
已抽查以下实际视频帧的目标、标记和文字布局：

| 整体 | 框选 | 圈选 | 下划线 | 双图并列 |
| --- | --- | --- | --- | --- |
| [整体](focus-overview.png) | [标题重点](focus-box.png) | [说明重点](focus-circle.png) | [代码重点](focus-underline.png) | [并列＋荧光标记](side-by-side.png) |

并列视频的价格页是虚构夹具，视频内已注明“虚构测试价格页，仅作布局演示”。Remotion仍为4.0.535，未新增包或升级依赖。

## 复现

在研究模块运行，输出目录必须尚不存在，所有脚本均隔离状态和引擎任务：

```sh
../.AI_research/bin/python spec/acceptance/automatic-visuals-20261025/browser_cases.py --output state/automatic_visual_acceptance/recheck-browser
../.AI_research/bin/python spec/acceptance/automatic-visuals-20261025/qwen_material_eval.py --output state/automatic_visual_acceptance/recheck-vision --fixtures state/automatic_visual_acceptance/recheck-browser
../.AI_research/bin/python spec/acceptance/automatic-visuals-20261025/real_chain.py --output state/automatic_visual_acceptance/recheck-chain --vision
../.AI_research/bin/python spec/acceptance/automatic-visuals-20261025/browser_result.py --run-dir state/automatic_visual_acceptance/recheck-chain --output state/automatic_visual_acceptance/recheck-ui
../.AI_research/bin/python spec/acceptance/automatic-visuals-20261025/focus_movies.py --output state/automatic_visual_acceptance/recheck-focus --fixtures state/automatic_visual_acceptance/recheck-browser
```

运行真实视频需要本机既有 TTS、渲染环境和已经下载的视觉模型；OCR工具在每个隔离目录预编译。
首个未预热OCR的最终链路尝试触发35秒超时并按约定降级，随后原默认视频形式碰到既有云端HTTP402；该失败目录 `real-chain-vision-focus/` 保留。
正式环境已单独预热OCR工具。配图降级不代表可以绕过原视频形式所需的其他服务依赖。

## 回归与遗留项

- 最新配图／理解／聚焦／原出片专项166项通过。
- AutoVideo专项86项通过；全量756项通过、1跳过，1项历史冻结发布清单哈希／覆盖范围不符。未重写旧发布清单。
- 研究全量3103项通过、3跳过，仍有2项已存在的 Flash 配方黄金快照差异：`test_recipe_audiences`、`test_video_golden[flash]`。
- 配置验证仍有4个既有 assistant 验收 job 缺少 `keywords`，不是新增视觉配置报错；未改动其他任务。
- 最终前端556项测试、lint、完整类型检查及生产构建通过；实际浏览器查看模型描述、聚焦建议和自动段落选择，无截图获取／保存写请求，见 [界面QA](final-ui-qa.json)。

不声明整个共享工作区全绿。完整运行日志保留在 `/private/tmp/yanying-visual-final-check.log`、`/private/tmp/yanying-engine-final-check.log` 等本轮日志中。

提交前另导出暂存区独立快照：后端165项通过，引擎86项通过，前端22项通过且完整类型检查与构建通过。
独立快照的 `test_api_a_publication_records_a_link_and_a_date_and_marks_the_script_and_topic` 仍使用不完整的 Bilibili 示例链接，
被既有链接校验拒绝；在未含本轮修改的 `a7cf1e1` 基线中单独复现同一失败，未纳入本轮修复。

## 最终记录

2026-10-25 22:21，本轮范围完成并在当前服务启用。配置为3页／3图／3次模型调用／累计180秒，必需本机视觉复核；失败自动降级。
正式视频完成后才加载新引擎接口，新的 `focus` 合同已在线；研究进程已加载最终源码，配置逐次读取，无需额外重启。
282条既有引擎任务、138份文稿、208个版本、218条制作记录全部保留，未创建正式验收视频任务。
数据库备份保存在忽略的验收状态目录。见 [上线核对](release.json) 和 [引擎加载记录](engine-release.json)。

可直接查看最终示例：
`state/automatic_visual_acceptance/20261025/real-chain-final/state/video_renders/654a1bc42612475e94636090aa5a6035/video.mp4`。
这一自动理解路径覆盖新增的来源截图；用户已明确保存的配图和历史视频保持原映射，不批量重配旧素材。
