# 产品视觉功能进展：Draw Things 本地场景图

**状态：PASS（Draw Things 本地生成、柔化背景与任务详情预览）** · **范围：本地视频产品的可选视觉能力** · **2026-10-13**

## 结果

AutoVideo 新建页现在可检测 Draw Things 本机 HTTP API，并可为三个分镜生成本地背景图。生成图保存在对应任务尝试的 fixture 工作区，记录提示词、尺寸、字节数和 SHA-256；renderer 只接受固定的任务内 PNG 相对路径，在现有已登记图解下执行限幅推近和轻微平移。实际生成验收发现 Draw Things 会在背景中画出伪文字和伪图表，renderer 已增加固定柔化与暗化，让这些区域退为气氛纹理。未修改 Video IR v0.2、组件 props schema 或动作 Registry。

Draw Things 真实生成任务 `84e04a6b-85d7-4f9f-b658-26349cc3722b` 已在本机完成。三个场景图均由 `http://127.0.0.1:7860` 返回，实测尺寸 1280×704，PNG 解码有效且 SHA-256 与清单一致。18.709 秒成片为 1920×1080 H.264 + 48 kHz 双声道 AAC，561 帧；QA 和独立 FFmpeg 全流解码均 PASS，七个任务阶段全部成功。

修正 renderer 后，第二条真实任务 `520801e1-e2a1-42fc-83c6-099c4f36a6c0` 通过 Draw Things 生成了三张 1280×704 PNG，哈希全部匹配。16.213 秒成片为 1920×1080 H.264 + 48 kHz 双声道 AAC、486 帧；七个阶段、QA 和独立 FFmpeg 全流解码均 PASS。人工检查此任务的 9 帧联系表，背景伪字不再可读，正式图解清晰，柔化后的暗色氛围符合此主题，因此本产品功能验收为 PASS。H1 的独立评议仍由用户取消，H1 研究结论继续为 PARTIAL；本 PASS 不代表 H1 视觉质量对照实验通过。

任务详情现在为已完成任务展示三张场景背景预览。服务端先核验已登记清单文件的 SHA-256，再从当前尝试的固定路径读取图片，并验证 PNG 签名、尺寸和清单中的图片 SHA-256；篡改图片会返回 409。任务 `520801e1-e2a1-42fc-83c6-099c4f36a6c0` 的开场、内容展开和结尾预览均在本机 UI 实际显示，三张图的哈希与生成清单一致。

## 变更文件

- `g2/draw_things.py`：仅允许 loopback 的 Draw Things HTTP 客户端；三场景图像请求、PNG 校验、资产和哈希清单。
- `g2/run_g2.py`、`g1/renderer/src/Root.tsx`：将可选场景背景作为 Video IR 之外的受控渲染 sidecar；加入固定柔化/暗化和限幅相机运镜。
- `g2/tests/test_draw_things.py`：接口请求、loopback 限制、离线状态与 PNG 校验测试。
- `g3/app.py`、`g3/config.py`、`g3/worker.py`、`g3/store.py`、`g3/tests/test_g3.py`：可选任务输入、API 预检、场景图阶段及已完成任务的安全预览接口和完整性测试。
- `g3/web/src/main.tsx`、`g3/web/src/visual.css`、`g3/web/static/app.js`、`g3/web/static/app.css`：本机连接状态、场景图开关、任务阶段与响应式场景图预览。
- `g3/README.md`：本地 API 开启、功能与预览说明。

## 验证

- `python3 -m pytest g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q`：54 passed。
- `cd g3/web && NODE_PATH="../../g0/vendor/remotion-audiogram/node_modules" ../../g0/vendor/remotion-audiogram/node_modules/.bin/esbuild src/main.tsx --bundle --minify --format=esm --jsx=automatic --outdir=static --entry-names=app`：成功。
- 使用临时背景图和已有音轨，经 Remotion 实际渲染 3 帧；`ffprobe` 确认 H.264 1920×1080 + AAC，`ffmpeg` 全流解码通过。临时制品：`/private/tmp/autovideo-draw-things-smoke/smoke.mp4`。
- 真实任务原视频：`g3/data/jobs/84e04a6b-85d7-4f9f-b658-26349cc3722b/attempts/1/g2-run/artifacts/G2_自动生成.mp4`；原始联系表：`g3/data/jobs/84e04a6b-85d7-4f9f-b658-26349cc3722b/attempts/1/g2-run/artifacts/contact-sheet.jpg`。
- 真实背景复用修正 renderer 后的临时成片：`/private/tmp/autovideo-draw-things-smoke/soft.mp4`；检查联系表：`/private/tmp/autovideo-draw-things-smoke/soft-contact.jpg`。临时输出仅用于对照，没有覆盖或登记到原任务。
- 修正后的第二条真实 UI 任务：`g3/data/jobs/520801e1-e2a1-42fc-83c6-099c4f36a6c0/attempts/1/g2-run/artifacts/G2_自动生成.mp4`；QA 联系表：`g3/data/jobs/520801e1-e2a1-42fc-83c6-099c4f36a6c0/attempts/1/g2-run/artifacts/contact-sheet.jpg`；执行日志：`g3/data/jobs/520801e1-e2a1-42fc-83c6-099c4f36a6c0/attempts/1/worker.log`。
- `git diff --check`：通过。
- AutoVideo 本机 UI 的第二条任务通过已连接的 Draw Things API 完成，详情可由任务 ID 持续查看。
- 场景图列表 API 返回三张经清单校验的本地图片；预览接口返回 `200 image/png`，修改图片后列表 API 返回 `409 SCENE_IMAGE_HASH_MISMATCH`。
- 刷新本机 AutoVideo 任务详情后，页面显示“场景背景预览”及三张图；端口 `8011` 服务正常，端口 `8000` 未触碰。
- 端口 `8000` 由独立项目 `video-opportunity-desk` 使用，未停止或修改该项目。

## 制品、限制与下一步

新建页可在本机 AutoVideo 服务 `http://127.0.0.1:8011/#new` 使用。真实任务及所有正式产物保存在 `g3/data/jobs/84e04a6b-85d7-4f9f-b658-26349cc3722b/attempts/1/`。

下一项建议是支持单独重新生成一个场景背景并复用现有讲稿、旁白和 Video IR，让用户修正单张素材时无需重跑整条任务；继续使用现有 Draw Things 连接，不引入新模型或服务。

**风险：** 真实生成已验证服务端点为 `127.0.0.1:7860`。图像 API 请求 1280×720，但模型返回 1280×704；renderer 使用 cover 填充画面并柔化背景。较复杂的风格/场景仍需逐主题做抽样检查。
