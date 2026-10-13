# G3 本地应用

G3 提供本机 FastAPI + SQLite 服务和 React 界面，把已通过 G2 实测的生成流水线放进单 worker 队列。只绑定 `127.0.0.1`，不接受任意文件路径或生成代码；任务源文会存入独立任务工作区。生成讲稿与分镜时，源文会发送给 DeepSeek API；语音合成和视频渲染在本机执行。

## 配置与启动

1. 在项目根目录 `.env` 中设置 `DEEPSEEK_API_KEY=...`（权限应为仅当前用户可读）。密钥不会出现在 API 响应或日志中。
2. 确保 G0 的 Kokoro、Remotion 和浏览器依赖可用。若当前 Python 环境没有 FastAPI，按 `g3/requirements.txt` 在隔离环境安装后启动。前端构建：

   ```sh
   cd g3/web
   NODE_PATH="../../g0/vendor/remotion-audiogram/node_modules" \
     ../../g0/vendor/remotion-audiogram/node_modules/.bin/esbuild src/main.tsx \
       --bundle --minify --format=esm --jsx=automatic --outdir=static --entry-names=app
   ```

3. 在项目根目录创建隔离 Python 环境并启动服务（G2 worker 会单独调用 G0 Python 环境）：

   ```sh
   cd ../..
   python3 -m venv g3/.venv
   g3/.venv/bin/pip install -r g3/requirements.txt
   g3/.venv/bin/python -m uvicorn g3.app:app --host 127.0.0.1 --port 8000
   ```

4. 在浏览器打开 `http://127.0.0.1:8000/`。默认数据目录为 `g3/data/`，可通过 `AUTOVIDEO_DATA_DIR` 指定独立的本地数据位置。服务首次启动会将上次停止时仍在运行的尝试记录为可重试失败，不会伪报为运行中或完成。

### React 构建

项目仓库保留构建后的 `web/static/app.js` 和 `app.css`，以便 Python 服务无需运行 Node 即可启动。开发者依赖来自 G0 的 vendored npm 包；`package.json` 记录兼容版本，构建不需要联网。

## 服务行为

- SQLite `BEGIN IMMEDIATE` 原子领取队列任务，进程文件锁阻止第二个 worker 同时工作。
- 每个任务源文和每次尝试都使用单独工作目录；重试保留前次日志和报告。
- 取消会终止当前流水线进程组；重启恢复会检查并回收仅匹配本项目 G2 命令的孤儿进程。
- 服务只公开完成且 QA 为 PASS 的产物；下载前核验路径归属与 SHA-256。
- 配置预检只返回各依赖是否就绪，不返回 API 密钥内容。
- 已验证的产品范围是 10–20 秒、单机、单 worker、深色知识图解和“小晓”普通话旁白。

### 可选：Draw Things 本地场景图

新建页可选择为 3 个分镜分别生成一张背景图，再由固定 Remotion renderer 叠加已登记图解，并在每个场景里执行限幅推近和平移。图像只进入当前任务工作区；Video IR v0.2 和组件动作 Registry 不变。

此功能通过 Draw Things 应用内置的 HTTP API 工作，不下载或另装图像服务。先在 Draw Things「设置 → 高级」开启 HTTP API 服务器并选择 HTTP（默认端口 7860），再在 AutoVideo 页点击「重新检测」。生成时沿用应用当前加载的模型、采样器、步数和指导设置；AutoVideo 只提交每个分镜的画面意图、1280×720 尺寸和固定负面提示词。连接目标固定为 `127.0.0.1`，拒绝远程主机。

AutoVideo 在创建时检测本地 API；未连接时，勾选场景图的任务不会进入队列。关闭选项仍使用原有的纯程序化图解流程。源文仍会发送给 DeepSeek 生成讲稿和分镜；发送给 Draw Things 的是从分镜规划出的本机背景图提示词。

任务成功完成后，详情页会显示三张场景背景预览。服务端先验证已登记清单文件的 SHA-256，再读取当前尝试工作区内的固定 PNG 路径，并核验图片签名、尺寸和 SHA-256；未完成任务不公开预览，图片与清单哈希不一致时请求失败。

## 测试

```sh
python3 -m pytest g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q
```

测试不调用外部模型；真实生成验收需要通过 Web 界面提交任务，产物和执行日志保存在 `g3/data/jobs/<job-id>/attempts/<attempt>/g2-run/artifacts/`。固定 G4 Golden Case 输入和可重复的发布验收流程见 [`../g4/README.md`](../g4/README.md)。
