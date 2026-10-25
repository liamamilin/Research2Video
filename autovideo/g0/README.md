# G0 中文有声动画实验

本目录只做 10–20 秒声画链路验证。固定中文文本 → 本地 Kokoro 真实 TTS → 实测短语边界 → **未修改的 Remotion 官方 Audiogram 模板** → FFmpeg 固定合成 → 完整解码、音频和关键帧检查。

不包含 G1 的 Video IR v0.2 编译器、六组件库或 G2 的真实 LLM 工作流。`narration.json` 是本次实验的数据，不是产品 IR。所有渲染 JS/TS 都来自 `trusted-source.json` 记录的上游 commit，执行前校验文件哈希；没有模型响应、用户文本或 JSON 被解释成 JS/shell。

## 本机重放

当前本机依赖已安装在本目录。先将整个 `artifacts/` 重命名为新的留档目录，再依次运行下面四个命令；程序拒绝覆盖已存在的 MP4。`prepare` 会重建实验数据，但不改原始规范。

```console
g0/.venv/bin/python -u g0/run_g0.py prepare
g0/.venv/bin/python -u g0/run_g0.py render
g0/.venv/bin/python -u g0/run_g0.py finish
g0/.venv/bin/python -u g0/run_g0.py verify
g0/.venv/bin/python -u g0/verify_design_snapshot.py
```

运行位置是本项目根目录。当前渲染脚本固定使用本机 Chrome 路径与 macOS 系统字体，其他系统需要另行适配。旁白按短语分别推理，然后按实际 PCM 样本数拼接；时间线仅声明短语级，不声明逐词对齐。

## 新环境准备

- Python 3.10.13 + `python3 -m venv g0/.venv`；在该虚拟环境按 `requirements.lock` 安装依赖。
- Node 22.21.1、npm 10.9.4、Google Chrome；仅在 `g0/vendor/remotion-audiogram` 安装 Node 依赖。
- 克隆 [Remotion 官方模板](https://github.com/remotion-dev/template-audiogram) 到 `g0/vendor/remotion-audiogram`，检出 `a7a1befca086f72eaf721a8b85d9e5038453da94`。将 `g0/remotion-package.json` 和 `g0/remotion-package-lock.json` 分别复制为模板目录的 `package.json` 和 `package-lock.json`，执行 `npm ci --ignore-scripts --no-audit --no-fund`。所有 Remotion 包锁定为 4.0.535；图表使用 ECharts 6.1.0；统一对象图标使用 Lucide React 0.460.0，短动作使用 `@remotion/lottie` 4.0.535 与 lottie-web 5.13.0，均纳入同一锁文件。
- 下载 [Kokoro ONNX 模型](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx) 与 [音色文件](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin) 到 `g0/assets/`，核对 `artifacts/tts-metrics.json` 的 SHA-256。下载文件约 347 MB，不入 Git。
- 下载 [static-ffmpeg 使用的 macOS ARM64 压缩包](https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin_arm64.zip)，在 `g0/` 解压得到 `darwin_arm64/ffmpeg` 和 `ffprobe`，增加本地执行权限。本包链接目录名为 v8.0，**实测二进制版本为 7.0**，以版本日志为准，不依据 URL 推断。
- FFmpeg 二进制、模型、虚拟环境、下载缓存与模板克隆不入 Git；版本锁、来源哈希、运行脚本、音视频和报告入 Git。

## 验证入口

`artifacts/qa.json` 记录机械检查；`logs/commands.jsonl` 保存实际固定参数、退出码、耗时和原始输出文件位置。`logs/design-tests.log` 保留在扩展后的项目根目录运行原规范包测试时的清单覆盖失败；`verify_design_snapshot.py` 校验原包全部文件哈希，并在仅含原清单文件的临时副本运行原测试，不修改测试或清单。

项目增加 G0 文件以后，原测试 `test_manifest_hashes_and_file_coverage` 的“目录里只能有设计发布清单文件”前提不再成立。不要修改冻结 `PACKAGE_MANIFEST.json` 去收录虚拟环境和依赖。

授权、费用、失败过程和未验证项详见根目录 `G0_REPORT.md`。
