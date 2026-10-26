# 自动段落拆合与画面衔接 · 第三版

第三版把实际配音时长接到混合分镜之前，自动决定画面单元，再安排同页承接和换图衔接。沿用上游旁白、数据、图片及区域，调用方不增加必填字段或人工编排步骤。

## 真实样片

原任务`22de61dd-fb01-44f8-9337-ff9e3c16a46d`在隔离任务库生成，使用现有模型配置及本机Kokoro配音。复用第二版通过Playwright取得的FlowNote演示页面截图及DOM区域，通过`POST /api/creative-images`登记图片UUID。演示页面用于观察画面效果。

两章、5个原始段落，17个配音分句自动组织为7个画面单元：

- 两个短开场合并为同一页，实测旁白约4.84秒。
- 长网页讲解拆成文件区20.89秒、提问区21.56秒、结果区15.06秒及收尾6.40秒；对应同一已登记图片，镜头连续承接。
- 月度趋势沿用原生图表，约13.48秒；同章换图时只溶解画面区。
- 第二章独立建立新主题，约10.35秒；不跨章节合并。

上述为各单元旁白跨度，不含画面边界分配的句间空隙。音频实测96.900秒；30fps成片2907帧，容器96.917秒；包装片3012帧、100.4秒，1920×1080。

每章仍使用原有的一项分镜规划。本次实际模型请求3次：第一章因趋势图携带散点图字段`focus_steps`走了一次既有结构重试，第二章一次完成；没有新增镜头规划请求，也没有人工修改模型分镜。首轮与全部中间输出保留。

首轮观察后补充短收尾的返回全景规则。最终片复用相同模型分镜、配音、图片及时间线，只重新渲染，没有新增模型或配音调用。

## 衔接行为

- 同一网页：前一单元保留最终焦点；下一单元继承该位置，按新旁白移动到下一区域，末页回到整体。
- 双图：相同素材组合继续使用之前的主次比例。
- 换图或换图表：同章做10–17帧的画面区溶解，短画面相应缩短；音轨、字幕和标题不叠加。
- 声音或倍速修订：开场位置从前一单元重定时后的beat推导，未冻结绝对镜头时间。图片或组件改变时自动采用普通衔接。
- 旧任务没有`scene_flow`时使用原行为；快速卡片、聊天、轮播和文学路线维持其现有制作方式。

`continuity.json`记录6处画面边界全部连续，3处同页承接的进入与离开位置、缩放和画面尺寸完全一致。真实成片共38个抽帧，包含边界前、进入、中间、之后；已查看最终收尾全景、网页转图表、图表总结及第二章建立画面。内容片与包装片完整解码通过，最终旁白相关系数约0.99994。

## 验证与文件

引擎821项回归通过、1项跳过，排除已有冻结发布清单覆盖测试1项；该测试遍历运行数据及vendored依赖，本轮保持冻结发布清单不变。最终收尾调整后63项相关回归通过，渲染器TypeScript通过。结果在`validation.json`。

- 首轮：`g3/data/shot-director-v3-20261026-first/`。
- 最终：`g3/data/shot-director-v3-20261026-release/video.mp4`及`content.mp4`。
- 完整渲染及包装日志：最终目录的`render-run/logs/`。
- 拆合映射、调度及镜头：`scene-assembly.json`、`generation-schedule.json`、`shot-director.json`、`model-material-cues.json`。
- 画面与媒体：`frames.json`、同名PNG、`continuity.json`、`decode.json`和`qa-summary.json`。
- 正式队列状态：`service.json`。加载脚本在发现一条运行中的任务后退出，没有发送停止信号；本轮不重启服务。工作进程为每个任务启动新的`g2/run_g2.py`，后续新任务会读取当前生成代码。运行中任务保持原进程继续制作。

## 重放

在autovideo目录运行，各次使用新输出目录：

```sh
python3 g3/acceptance/shot-director-v3-20261026/run.py --fixtures g3/data/shot-director-v2-20261026-pages-2 --output g3/data/scene-video-new
python3 g3/acceptance/shot-director-v3-20261026/finalize.py --source g3/data/scene-video-new --output g3/data/scene-video-polished
python3 g3/acceptance/shot-director-v3-20261026/frames.py --source g3/data/scene-video-polished
```

首次fixture可用第二版`fixtures.py`通过本机Chrome重新生成。拆合采用实际配音时长，因此混合讲解改为配音后分镜；其他路线保留既有并行安排，未增加模型规划阶段。
