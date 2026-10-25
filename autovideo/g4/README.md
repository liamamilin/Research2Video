# G4 发布验收运行说明

G4 使用真实 DeepSeek 结构化输出、Kokoro 中文语音、PCM 短语对齐和 Video IR v0.2 固定组件渲染器，完成三个固定 Golden Cases。模型输出只作为 JSON；任何输出都要通过 schema、来源引用和组件/动作校验。渲染只调用仓库内固定 Remotion renderer，不执行模型提供的代码。

## 本机运行要求

- Apple Silicon macOS；Python 3.10.13；Node 22.21.1、npm 10.9.4；Google Chrome。
- 在 `g0/.venv` 安装 `g0/requirements.lock` 中锁定的 Python 依赖。
- 将 Remotion Audiogram 上游模板检出到 `g0/vendor/remotion-audiogram` 的固定 commit `a7a1befca086f72eaf721a8b85d9e5038453da94`，使用仓库锁定的 Remotion 4.0.535 依赖。
- 下载 Kokoro v1.0 模型和 voices 文件到 `g0/assets/`，将 macOS ARM64 FFmpeg/ffprobe 放到 `g0/darwin_arm64/`。具体来源、哈希和系统前置条件见 [`../g0/README.md`](../g0/README.md)。这些大型二进制、模型和依赖目录不放入 Git。
- 在仓库根目录从 `.env.example` 复制创建 `.env`，填入 `DEEPSEEK_API_KEY` 后运行 `chmod 600 .env`。生成期间源文会发送给 DeepSeek API；API key 不会进入运行产物。
- 应用环境按 [`../g3/README.md`](../g3/README.md) 安装 `g3/requirements.txt` 并启动本机 FastAPI 服务。前端静态文件随仓库提供，构建工具来自 G0 vendor 依赖。

## 三个真实 Golden Case

固定输入位于 `golden_cases/case-a|case-b|case-c/source.txt`。每个案例在独立目录保留输入、模型请求/响应、讲稿、TTS WAV、对齐、字幕、IR、MP4、ffprobe、完整命令日志和费用上界。Case C 的固定类别、数值、单位和零基线见 `golden_cases/case-c/expected-chart.json`。

在项目根目录分别运行以下命令。每次使用新的输出目录；失败目录应保留用于诊断。

```console
AUTOVIDEO_G2_CASE_ID=case-a AUTOVIDEO_G2_SOURCE_FILE="$PWD/g4/golden_cases/case-a/source.txt" AUTOVIDEO_G2_RUN_ID=G4CaseA AUTOVIDEO_G2_RUN_DIR="$PWD/g4/runs/case-a-retry" g0/.venv/bin/python -u g2/run_g2.py all
AUTOVIDEO_G2_CASE_ID=case-b AUTOVIDEO_G2_SOURCE_FILE="$PWD/g4/golden_cases/case-b/source.txt" AUTOVIDEO_G2_RUN_ID=G4CaseB AUTOVIDEO_G2_RUN_DIR="$PWD/g4/runs/case-b" g0/.venv/bin/python -u g2/run_g2.py all
AUTOVIDEO_G2_CASE_ID=case-c AUTOVIDEO_G2_SOURCE_FILE="$PWD/g4/golden_cases/case-c/source.txt" AUTOVIDEO_G2_RUN_ID=G4CaseC AUTOVIDEO_G2_RUN_DIR="$PWD/g4/runs/case-c-retry" g0/.venv/bin/python -u g2/run_g2.py all
g0/.venv/bin/python -m g4.qc case-a
g0/.venv/bin/python -m g4.qc case-b
g0/.venv/bin/python -m g4.qc case-c
```

每个视频独立封顶 7 次 API 请求（失败请求也占额度）；请求 token 用量计入费用上界，按当前价格最高不超过 $0.09。G4 独立 QC 会重新运行 ffprobe 和完整解码，验证真实链路、逐场景抽帧、Beat 前后变化、字幕安全区运行时校验、案例语义和费用上限。接触表和机器可读验收 JSON 写在 `g4/runs/<case>/g4-qc/` 与 `g4/runs/<case>/g4-qc.json`。

运行离线回归：

```console
g0/.venv/bin/python -m pytest g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q
```

G4 当前验证 10–20 秒单机样片管线，不等于 45–90 秒生产目标已经通过；本阶段不加入额外 provider、图像或视频模型、任意代码执行、自动发布、多用户服务或后续阶段工作。
