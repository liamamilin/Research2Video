# 数据画面与混合分镜 V2

2026-10-25。增加二维散点关系、随讲述出现的对象、坐标提示、指标卡和按旁白切换的比较重点。继续把上游讲稿、数据与结论作为输入，工作集中在视频表达。

## 自动样片

真实模型自动选择页面截图 → 成本与速度散点图 → 单次成本与得分散点图 → 使用量趋势 → 文字总结。

首条完整自动生成任务为 `53acd7a5-f44e-49ac-82d7-2d49dd25a60a`，内容片69.184秒、1920×1080、30fps、H.264/AAC，使用真实本机Kokoro中文配音，完整解码通过。

模型自行编排了两步比较：先同时突出A/D，再突出B/C，最后突出B；另一张图自动选择0.002、0.006、0.01美元与得分两个维度。未手工指定这些分镜或对象位置。

首条片及原始任务保留于 `g3/data/data-storytelling-v2-20261025-first/`。最终布局通过原有声音／倍速修订入口重新渲染，复用同一份旁白和画面数据，输出于 `g3/data/data-storytelling-v2-20261025-final/`。

数据为本次演示提供的上游示例，页面图复用已有截图素材。正式作品库不增加验收任务。

## 复现

从autovideo目录执行；输出目录须尚不存在。本机需具备已安装的模型服务配置、配音及渲染依赖。

```sh
python3 g3/acceptance/data-storytelling-v2-20261025/run.py --output g3/data/scatter-recheck
python3 g3/acceptance/data-storytelling-v2-20261025/layout_cases.py --output g3/data/scatter-layout-recheck
python3 g3/acceptance/data-storytelling-v2-20261025/refresh.py --source g3/data/scatter-recheck --output g3/data/scatter-refresh
```

`run.py`使用隔离任务库和正式服务已有的模型配置；`layout_cases.py`只渲染受控布局样例；`refresh.py`保留原计划及音频，另存一次真实视频。均不发送发布请求。


## 画面与运行结果

- [当前对象](single-object.png)、[逐个出现](points-appear.png)、[比较A/D](compare-first.png)、[比较B/C](compare-second.png)、[结尾突出B](conclusion.png)、[小数成本](small-values.png)。
- 受控组件布局：[小数](layout-tiny.png)、[密集名称](layout-dense.png)、[相同位置](layout-equal.png)、[负数](layout-negative.png)。这些是布局夹具，不冒充模型生成的产品内容。
- [自动编排](explanation-plan.json)、[上游示例](upstream-example.json)、[首次真实模型与配音记录](original-qa.json)、[最终复用记录](result.json)、[最终媒体](qa.json)、[包装](package-qa.json)。
- 最终重渲染任务 `9bd9cc0d-6074-4199-a3c2-8091f751ef24`，旁白及图表数据保持一致，内容与包装MP4完整解码通过。
- 567项引擎相关测试通过，1项既有跳过；TypeScript检查与渲染器构建通过。完整记录见 [本轮结果](acceptance.json)。
- 正式队列空闲后已加载V2，283条任务保留，见 [服务记录](service.json)。各矩阵继续共用V1入口，新标准制作自动选择散点与比较步骤，无需另开开关。

另生成了80帧的同坐标对象布局短片，完整解码通过；该片为无旁白的受控组件样例，记录在 `acceptance.json`，不计作真实模型出片。
