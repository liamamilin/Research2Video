# G3 验收报告

**Status: PASS** — 本地服务、单 worker 任务队列、SQLite 持久化、工作区隔离、取消/重试、React UI 与真实 G2 流水线集成均完成。关闭旧标签并在服务重启后打开新标签，原任务状态仍准确显示为 `COMPLETED`，八个已验收制品仍可列出和下载。

## 实测记录

- 时间：2026-10-13（Asia/Shanghai）。
- 运行配置：Python FastAPI `0.143.0`、Pydantic `2.14.0`、Uvicorn `0.54.0`；Node `22.21.1`、React `19.2.3`、esbuild `0.28.1`。后端只监听 `127.0.0.1:8000`。
- Web 操作新建任务 `657867ac-6fda-422c-8be2-b792ba52cadb`，输入为 G2 通信噪声样例，目标时长 18 秒。UI 显示六个阶段全部完成，并可从本机 API 播放成片及下载产物。
- 浏览器持久化门：完成任务后关闭任务标签；重启服务；创建新的浏览器标签打开原任务地址。SQLite 和 UI 均恢复为 `COMPLETED`，尝试次数为 1，八个制品仍可读取。新标签中的任务详情截图已附在本次 G3 验收对话中。
- 真实生成验收：DeepSeek API 两次真实调用、Kokoro 真实旁白、无 mock 调用；MP4 时长 `17.109s`，H.264、`1920×1080`、`30fps`、513 帧，AAC、48 kHz、双声道。完整音视频解码通过，解码旁白相关性 `0.999943`；3 个场景、5 个 beats，Beat 前后画面变化均通过 G2 QA。实际模型费用上界记录为 `$0.0120147`。
- `.env` 权限为 `0600`；自动扫描本次任务工作区的 54 个文件，没有发现 API key。密钥只由后端读取，不返回给 UI。

## 验证命令

| 命令 / 操作 | 结果 |
|---|---|
| `python3 -m py_compile g2/run_g2.py g3/*.py` | 通过 |
| `python3 -m pytest g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q --disable-warnings` | `40 passed` |
| `NODE_PATH="$PWD/g0/vendor/remotion-audiogram/node_modules" g0/vendor/remotion-audiogram/node_modules/.bin/esbuild g3/web/src/main.tsx --bundle --minify --format=esm --jsx=automatic --outdir=g3/web/static --entry-names=app` | 通过；离线生成 `app.js` 与 `app.css` |
| `python3 -m uvicorn g3.app:app --host 127.0.0.1 --port 8000` | 服务启动；使用浏览器完成人工 UI、真实生成、视频取流和重开页验收 |

测试覆盖 SQLite 多连接竞争下无重复 claim、worker 文件锁、服务启动时恢复中断任务、取消与重试、独立 attempt 工作区、输入范围校验、配置 key 不泄漏、HTTP API 重启持久化、制品路径限制与 SHA-256 完整性。测试套件本身不调用外部模型；真实调用通过上述 Web 任务单独验证。

## 交付路径

- 应用与启动说明：`g3/app.py`、`g3/README.md`、`g3/requirements.txt`
- 任务 API/状态存储/worker：`g3/app.py`、`g3/store.py`、`g3/worker.py`、`g3/config.py`
- React UI 与静态构建：`g3/web/src/main.tsx`、`g3/web/src/styles.css`、`g3/web/static/`
- G3 自动化测试：`g3/tests/test_g3.py`
- 本次真实任务视频：`g3/data/jobs/657867ac-6fda-422c-8be2-b792ba52cadb/attempts/1/g2-run/artifacts/G2_自动生成.mp4`
- 执行日志：`g3/data/jobs/657867ac-6fda-422c-8be2-b792ba52cadb/attempts/1/worker.log`
- QA 报告：`g3/data/jobs/657867ac-6fda-422c-8be2-b792ba52cadb/attempts/1/g2-run/artifacts/qa.json`
- 用量报告：`g3/data/jobs/657867ac-6fda-422c-8be2-b792ba52cadb/attempts/1/g2-run/artifacts/model-cost.json`
- 本机任务地址：`http://127.0.0.1:8000/#job/657867ac-6fda-422c-8be2-b792ba52cadb`

任务输入、SQLite 数据库和生成制品保存在被 `.gitignore` 排除的 `g3/data/` 中，以免把用户输入或本地运行记录混入代码提交；文件仍保留在本机，可由任务页面访问。

## 变更与风险

变更包含 G3 FastAPI/SQLite 后端、受控单 worker G2 子进程、React 单页界面、离线静态资源、requirements/README、恢复/并发/隔离测试，以及 G2 CLI 的受限项目内源文、时长和运行目录参数。默认 G2 案例路径与行为继续通过原测试。

验收边界仍为单机单 worker、10–20 秒、`minimal_dark` 主题和 `zf_xiaoxiao` 普通话音色；服务没有用户账户或跨设备支持。文本规划和分镜会将素材发送至 DeepSeek API，语音和渲染在本机完成。运行代码只在本机工作区内保存，下载端点只开放已通过 QA 的产物并在每次读取时检查路径与哈希。

**Git commit：** 本报告与实现随 G3 提交；最终提交 SHA 在交付答复中列出。
