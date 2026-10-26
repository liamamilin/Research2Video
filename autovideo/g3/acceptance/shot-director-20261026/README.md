# 自动镜头调度第一版 · 2026-10-26

四段上游讲稿自动生成：文件查询流程、月度趋势、本地与云端多组比较、四个方案的成本与速度关系。模型选择原生组件，镜头安排从现有旁白时间点和对象位置派生；没有手工编排每个镜头，没有增加镜头规划模型请求。

## 实际结果

- 原始自动任务`729eca97-4a40-4d26-9495-39183c8bad7d`使用真实模型及本机Kokoro配音，在隔离任务库完成。
- 自动得到`flow_diagram → data_chart(trend) → data_chart(grouped) → scatter_chart`，四页均使用`explain`节奏。
- 内容片95.467秒、2864帧，包装片98.967秒，1920×1080／30fps，两者完整解码通过。原始配音与解码配音相关系数约0.99994。
- 从真实MP4提取21个整体、局部、比较及总结画面；已查看流程关系、趋势近景及总结、系列比较、单点与双点比较。标题、字幕、单位、图例与数值卡保持固定。
- 近景出现半个坐标刻度后，调整为随放大渐隐刻度、回到全景恢复；原数据标注及固定数值卡保留。
- 多组比较原计划按月份提供短语，已让这种情况回到系列名称在旁白中首次出现的位置；完整系列占满画面时保持全景，避免不必要的放大。
- 最终渲染复用原始模型分镜与原配音，仅重编译画面时间点和镜头；没有新增模型或配音调用。原旁白、音频身份、画面数据与文字保持一致。
- 正式8011服务已加载，原有283条任务保留，队列空闲。见`service.json`。

原始成片在`g3/data/shot-director-20261026-first/`。最终成片在`g3/data/shot-director-20261026-release-2/video.mp4`，完整渲染日志在同目录`render-run/logs/`。重编译辅助脚本首次因读取持久化对齐信息时缺少`rows`而中止，未开始渲染；对应中间目录`g3/data/shot-director-20261026-release/`保留。

## 自动流程与边界

研映标准制作和矩阵的现有`visual_storytelling`请求自动进入该环节；显式流程图也支持。密集焦点合并，短段落保持全景，镜头按配方选择快讯、普通讲解和深入讲解节奏。

本轮统一流程、趋势、多组比较及散点镜头。网页截图继续使用原有区域聚焦；统一图片／网页镜头与段落拆合分别列在第二、三版计划，见`g3/SHOT_DIRECTOR.md`。

## 回归与重放

引擎回归797项通过、1项跳过。排除已有冻结设计包文件覆盖测试1项，该测试遍历含运行数据和vendored依赖的工作目录，本轮保持冻结清单不变。渲染器TypeScript通过；真实浏览器打包、媒体解码及`git diff --check`通过。

回归输出见`validation.json`，媒体结果见`qa-summary.json`与`decode.json`，21个镜头的时间与画面见`shots.json`、`frames.json`及同名PNG。

从autovideo目录运行，每次使用新的输出目录：

```sh
python3 g3/acceptance/shot-director-20261026/run.py --output g3/data/shot-demo-new
python3 g3/acceptance/shot-director-20261026/frames.py --source g3/data/shot-demo-new
python3 g3/acceptance/shot-director-20261026/finalize.py --source g3/data/shot-demo-new --output g3/data/shot-demo-polished
```

`run.py`运行真实模型与配音；`finalize.py`使用已保存的模型分镜和原配音；`frames.py`按实际镜头时间提取MP4画面并完整解码。全部使用隔离任务库和新输出目录，不写入正式作品库。
