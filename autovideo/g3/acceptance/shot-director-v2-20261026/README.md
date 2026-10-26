# 统一页面、图片与图表镜头 · 第二版

本轮把已有配图、图片区域和原生图表接到同一套旁白镜头安排。重点是讲到哪处就让观众看哪处，以及素材之间的观看连续性；沿用上游讲稿、数据和素材，不增加事实核对环节。

## 自动样片

- 原始任务`367091ae-afbf-44fa-9023-08e468b47b5f`，隔离任务库，使用正式服务的现有模型配置和本机Kokoro配音。
- Playwright拍摄本地FlowNote演示工作台的整体、输入与输出状态，通过`locator.bounding_box()`取得文件区、提问区和结果区位置。演示页面用于观察镜头能力，不代表外部产品。
- 沿用`POST /api/creative-images`上传登记，再以UUID、说明、用途、段落和区域元数据提交。没有新增调用方必填字段。
- 一次正常的`c1-explanation`模型调用自动返回四页：工作台区域 → 输入输出并列对比 → 月度趋势 → 回到工作台，并同时为图片填写区域与旁白短语。没有人工编排镜头，也没有额外镜头规划模型调用。
- 同一张工作台图片在首段和结尾复用；每段按其模型选择的区域讲解，不强制巡视该图片的所有区域。没有短语提示的旧素材仍能按已有说明或旁白位置安排。
- 单页、双图和趋势统一使用`explain`节奏、相同标题及字幕区域。双图讲到哪侧就扩大到约65%宽度，另一侧保留；总结时恢复等宽及全景。

内容片66.219秒、1986帧，包装片69.7秒，1920×1080、30fps，两者完整解码通过。16个整体、聚焦及总结关键帧在本目录；已查看页面两处聚焦、双图两侧主次、恢复并列、趋势及回到工作台。配音解码相关系数约0.99994。

第一轮观察到等宽双图的当前界面偏小，调整为随镜头改变主次，并减少图片近景的留白。最终样片复用原有模型分镜、图片、旁白及音频，只重新渲染镜头，无新增模型或配音调用。首轮与最终输出均保留。

## 文件

- 首轮：`g3/data/shot-director-v2-20261026-first/`。
- 最终视频：`g3/data/shot-director-v2-20261026-release/video.mp4`；内容片为同目录`content.mp4`。
- 完整渲染、解码与包装日志：最终目录的`render-run/logs/`。
- `model-material-cues.json`是模型返回的图片镜头短语；`dom-regions.json`为Playwright实际位置。
- `shots.json`、`frames.json`及同名PNG记录最终镜头；`qa-summary.json`、`decode.json`记录媒体结果。
- `service.json`记录正式8011服务加载：队列空闲，288条既有任务保留。

## 回归及兼容

引擎回归808项通过、1项跳过，输出在`validation.json`。排除已有冻结设计包文件覆盖测试1项；该测试遍历包含运行数据及vendored依赖的目录，本轮保持冻结发布清单不变。渲染器TypeScript、实际浏览器打包与`git diff --check`通过。

回归覆盖跨配音分句的区域短语、旧图片的自动安排、同一页面的段落区域选择、图片修订保留资产、倍速使用的beat引用、双图主次、换图淡入及随机跳帧一致性。旧任务没有`shot_plan`时沿用原画面，短卡片、聊天和轮播路线保持原有组件。

## 重放

在autovideo目录运行，每次给新输出目录：

```sh
../AI_research/.AI_research/bin/python g3/acceptance/shot-director-v2-20261026/fixtures.py --output g3/data/page-fixtures-new
python3 g3/acceptance/shot-director-v2-20261026/run.py --fixtures g3/data/page-fixtures-new --output g3/data/page-video-new
python3 g3/acceptance/shot-director-v2-20261026/frames.py --source g3/data/page-video-new
python3 g3/acceptance/shot-director-v2-20261026/finalize.py --source g3/data/page-video-new --output g3/data/page-video-polished
```

截图复用已安装的Chrome；首次尝试默认Playwright浏览器时发现其缓存版本未安装，未产生图片，原目录保留。`finalize.py`与关键帧脚本复用第一版的通用验收工具，保留原图、原声和原任务。各步骤使用隔离任务库，不把演示视频写入正式作品库。

第三版将处理段落与画面数量的自动拆合、段落间衔接，见`g3/SHOT_DIRECTOR.md`。
