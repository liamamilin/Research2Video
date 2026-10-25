# G4 干净安装重放证据

本目录保存 G4 完成后在隔离 `/tmp` 检出中进行的安装和 G0 本地流水线重放证据。检出提交为 `8fa65f1`；没有复用主工作区虚拟环境、模型、voices、FFmpeg、ffprobe 或 Remotion `node_modules`。下载的模型、二进制、包缓存与虚拟环境均留在临时目录，没有复制进本目录。

`clean-install-evidence.json` 记录运行时版本、下载文件 SHA-256、Fresh G3 就绪状态和 G0 QC 结果。`g0/artifacts/` 是新环境重新合成的 14 秒 MP4、TTS、字幕、关键帧、ffprobe、QA 和媒体清单。`logs/commands.jsonl` 只保留此次重放期间追加的 17 条媒体命令记录；同目录日志包含第一次沙箱拦截和随后成功重跑的 Chrome 日志。

隔离环境的 Python 包清单在 `g0-python-freeze.txt` 与 `g3-python-freeze.txt`。固定 Remotion 模板提交记录在 `template-head.txt`，npm 锁文件副本为 `remotion-package-lock.json`。冷启动安装命令和结果详见 [`../../G4_ACCEPTANCE.md`](../../G4_ACCEPTANCE.md)。
