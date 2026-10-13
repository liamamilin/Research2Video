# 产品视觉功能进展：Draw Things 本地场景图

**状态：PARTIAL** · **范围：本地视频产品的可选视觉能力** · **2026-10-13**

## 结果

AutoVideo 新建页现在可检测 Draw Things 本机 HTTP API，并可为三个分镜生成本地背景图。生成图保存在对应任务尝试的 fixture 工作区，记录提示词、尺寸、字节数和 SHA-256；renderer 只接受固定的任务内 PNG 相对路径，在现有已登记图解下执行限幅推近和轻微平移。未修改 Video IR v0.2、组件 props schema 或动作 Registry。

本机 Draw Things 当前加载 `FLUX.2 [klein] 4B`，但其 API 服务器关闭，协议当前选中 gRPC。因此代码、界面、校验和模拟接口测试均已完成，真实 Draw Things 生成及带本地模型的 MP4 尚未实测。H1 的独立评议继续保持已取消，H1 原结论仍为 PARTIAL。

## 变更文件

- `g2/draw_things.py`：仅允许 loopback 的 Draw Things HTTP 客户端；三场景图像请求、PNG 校验、资产和哈希清单。
- `g2/run_g2.py`、`g1/renderer/src/Root.tsx`：将可选场景背景作为 Video IR 之外的受控渲染 sidecar；为背景加入固定相机运镜。
- `g2/tests/test_draw_things.py`：接口请求、loopback 限制、离线状态与 PNG 校验测试。
- `g3/app.py`、`g3/config.py`、`g3/worker.py`、`g3/store.py`、`g3/tests/test_g3.py`：可选任务输入、API 预检和可见的场景图阶段。
- `g3/web/src/main.tsx`、`g3/web/src/visual.css`、`g3/web/static/app.js`、`g3/web/static/app.css`：本机连接状态、场景图开关和任务阶段界面。
- `g3/README.md`：本地 API 开启和功能说明。

## 验证

- `python3 -m pytest g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q`：53 passed。
- `cd g3/web && NODE_PATH="../../g0/vendor/remotion-audiogram/node_modules" ../../g0/vendor/remotion-audiogram/node_modules/.bin/esbuild src/main.tsx --bundle --minify --format=esm --jsx=automatic --outdir=static --entry-names=app`：成功。
- 使用临时背景图和已有音轨，经 Remotion 实际渲染 3 帧；`ffprobe` 确认 H.264 1920×1080 + AAC，`ffmpeg` 全流解码通过。临时制品：`/private/tmp/autovideo-draw-things-smoke/smoke.mp4`。
- `git diff --check`：通过。
- AutoVideo 本机 UI 已在 `http://127.0.0.1:8011/#new` 加载；UI 正确显示 Draw Things 尚未连接，生成按钮在本地 API 未开启时禁用。
- 端口 `8000` 由独立项目 `video-opportunity-desk` 使用，未停止或修改该项目。

## 制品、限制与下一步

本轮没有真实 Draw Things 图像或含这些图像的 MP4 制品。界面检查实例使用本机 AutoVideo 服务，服务端口为 8011。

用户需在 Draw Things「设置 → 高级」开启 HTTP API 服务器并选择 HTTP，回到 AutoVideo 点「重新检测」。API 打开后，真实生成一次 10–20 秒视频，检查 `scene-backgrounds.json`、任务日志、视频播放与全流解码，方可把该功能从 PARTIAL 升为 PASS。

**风险：** Draw Things 当前 UI 显示 API 服务器关闭；本轮没有更改该网络接口设置，也没有测试 API 服务的监听范围。AutoVideo 客户端只连接 `127.0.0.1`，但服务端绑定范围应以 Draw Things 设置/实际监听状态为准。
