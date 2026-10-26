# 整片节奏与视觉主线 · 第四版

前三版已提交并推送GitHub：`534c0eade2a2230370265249120bddc6ec4ca434`。第四版在同一次章节分镜中安排讲解作用、主要对象和重要程度，再由现有组件表现停留节奏、对象颜色及视觉回顾。

## 实现

- 分镜页新增可选`story_role`（question/explain/compare/result/bridge/recap）、`importance`（key/normal/support）及`entities`（name/targets）。编译结果保存为可选`props.story`，冻结MVP schema及动作注册表不变。
- 整片对象登记表使用稳定ID和统一颜色。规范化全半角、空格及大小写，保留有意义的标点和版本号；跨章节向原有分镜请求传入已使用的名称，单场景重排沿用原片色彩。
- 图片标记、双图边框、原生图表系列／散点、对比卡及流程节点使用同一对象色；画面上方显示简短对象标签，不展示内部编排字段。
- 关键比较、结论和密集对象增加焦点停留及总结预留；简短承接保持稳定画面。所有安排在原有旁白时长内完成，字幕和音轨不变。
- 已有回顾旁白自动复用前面的最多两张相关画面，优先重点、同图去重；展示已有结论及对象名称，不重新播放源音轨或字幕。画面引用场景ID，随声音和倍速修订重定时。
- 标准制作及矩阵既有混合讲解入口自动启用。旧分镜没有第四版标记时沿用既有呈现；短卡片、聊天、轮播与文学路线保持原有组件。不增加镜头规划模型阶段。

## 三类真实样片

使用现有模型配置及本机Kokoro配音，通过隔离FastAPI任务库运行。Playwright拍摄本地「本地方案／云端方案」演示页面，并取得成本、响应时间及操作流程的DOM区域；页面和数据用于观察表现，不代表外部产品。

| 类型 | 原任务 | 内容片 | 包装片 | 主要画面 |
| --- | --- | --- | --- | --- |
| 快讯 | `0d9e4c80-a461-490c-aa0d-3e7da4458e09` | 50.304秒／1509帧 | 52.8秒 | 双图 → 散点比较 → 页面与比较图回顾 |
| 教程 | `95077ef1-4563-4ec0-b5a4-570a4e2a7cae` | 70.037秒／2101帧 | 71.533秒 | 双图 → 分支流程 → 对比卡 → 流程与使用方式回顾 |
| 深入讲解 | `e29d35f0-0fb8-4c4b-97a2-95ce72344d35` | 75.968秒／2279帧 | 79.467秒 | 双图 → 多组成本图 → 散点比较 → 两图回顾 |

三个样片中，本地方案持续使用绿色、云端方案持续使用蓝色。模型自动选择实体目标、讲解作用及原生组件，没有人工修改模型分镜。已有回顾句以「最后回顾」开头时会触发视觉回顾，即使模型将该页归为result。

快讯正常规划一次模型请求。教程首轮因可选连线文字缺失而在布局时失败，补充空标签默认处理后，通过现有重试API完成第二次尝试；两个尝试各一次模型请求。深入讲解发生一次已有结构重试，共两次请求。没有额外的整片／镜头规划调用。首轮失败、旧样片和所有重渲染输出均保留。

抽帧后将回顾卡调整为显示已有结论和两个对象，修正散点横轴最右端刻度的裁切；最终画面重渲染全部复用原模型分镜、图片、配音和时间线，没有新增模型或配音调用。

## 最终文件

- 快讯：`g3/data/story-direction-v4-20261026-flash-ready/video.mp4`。
- 教程：`g3/data/story-direction-v4-20261026-tutorial-final/video.mp4`。
- 深入讲解：`g3/data/story-direction-v4-20261026-deep-ready/video.mp4`。
- 同目录有`content.mp4`、完整渲染／解码／包装日志`render-run/logs/`及所有抽帧；原始任务与前面各轮输出均另存。
- 本目录各类型子目录保存镜头、对象编排、媒体结果与选帧。总体记录在`acceptance.json`，正式服务记录在`service.json`。

## 验证

完整引擎830项回归通过、1项跳过；排除已有冻结设计包文件覆盖测试1项，本轮保持冻结发布清单不变。最终对象名称规范化相关9项通过，渲染器TypeScript及`git diff --check`通过，记录见`validation.json`。

回归覆盖跨图表对象顺序改变、图片到图表的对象对应、修订保留色彩、跨章节名称上下文、回顾去重及源画面引用、讲解作用与密度改变焦点停留、缺省连线标签。三类真实片均做内容片和包装片完整解码及旁白检查，最终摘要见`acceptance.json`。

正式8011服务在队列空闲后加载第四版，290条既有任务保留，健康及主工作进程正常；没有打断正在制作的任务。

## 重放

在autovideo目录，各次使用新输出目录：

```sh
../AI_research/.AI_research/bin/python g3/acceptance/story-direction-v4-20261026/fixtures.py --output g3/data/direction-pages-new
python3 g3/acceptance/story-direction-v4-20261026/run.py --case flash --fixtures g3/data/direction-pages-new --output g3/data/direction-flash-new
python3 g3/acceptance/story-direction-v4-20261026/finalize.py --source g3/data/direction-flash-new --output g3/data/direction-flash-polished
python3 g3/acceptance/story-direction-v4-20261026/frames.py --source g3/data/direction-flash-polished
```

`--case`也支持tutorial、deep。`retry.py`用于保留首轮的正常任务重试。新版新任务无需人工选择角色、对象色或回顾画面。
