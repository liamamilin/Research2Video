# G4 验收报告

**状态：PARTIAL（真实样片和技术验收通过；暂不打 V0.1 `ACCEPTED`）**
日期：2026-10-13
范围：G4 品质闭环与发布验收。完成本报告后停止，不进入 M0 或 H 阶段。

## 结果

三个固定 Golden Cases 均由真实 `deepseek-flash` 结构化输出、Kokoro 中文 TTS 和固定 Video IR v0.2 渲染器生成，无 mock；最终视频均通过独立 ffprobe、完整解码、逐场景抽帧、Beat 前后画面变化、字幕与案例语义验收。G4/G3/G2/G1 运行时回归共 **53 passed**。

阶段仍标记 PARTIAL，原因是本轮没有在干净环境重新下载大型 Kokoro 模型、FFmpeg/ffprobe 和浏览器，故 README 的全新机器安装链尚未完全冷启动验证；许可证审计也发现 eSpeak NG、GPL FFmpeg 构建及 H.264/AAC 专利事项需在产品分发前确认。已完成的隔离 Python/npm 安装和应用启动预检见下文。

## 三条真实样片

| 案例 | 结果 | 时长 / 画面 | LLM 请求 | 费用保守上界 | 重点验收 |
|---|---:|---|---:|---:|---|
| A 通信噪声 | PASS | 19.968 秒，1920×1080，30 fps，599 帧 | 2 | $0.009084 | `activate_path`、`mark_disrupted` 前后像素变化分别为 1.64%、3.42%；发送、信道、接收流程完整 |
| B 批处理与流处理 | PASS | 17.835 秒，1920×1080，30 fps，535 帧 | 2 | $0.0126021 | 两侧比较组件呈现不同方法和特征 |
| C 虚构纸飞机数据 | PASS | 19.477 秒，1920×1080，30 fps，584 帧 | 2 | $0.0079275 | 柱图类别、[2, 4, 6] 米、零基线均与输入一致；旁白/字幕说明是虚构演示数据 |

全部视频含可解码 H.264 视频与 AAC 双声道、48 kHz 音频。TTS PCM 相关性约 0.99994–0.99995；全片解码、Video IR 运行时复验和费用上限均 PASS。逐场景关键帧及 Beat 前后帧保存在对应接触表与 PNG 文件中；接触表可直接查看。

- A 视频：[G2_自动生成.mp4](g4/runs/case-a-retry/artifacts/G2_自动生成.mp4)，[机器验收 JSON](g4/runs/case-a-retry/g4-qc.json)，[接触表](g4/runs/case-a-retry/g4-qc/keyframes/contact-sheet.jpg)
- B 视频：[G2_自动生成.mp4](g4/runs/case-b/artifacts/G2_自动生成.mp4)，[机器验收 JSON](g4/runs/case-b/g4-qc.json)，[接触表](g4/runs/case-b/g4-qc/keyframes/contact-sheet.jpg)
- C 视频：[G2_自动生成.mp4](g4/runs/case-c-retry/artifacts/G2_自动生成.mp4)，[机器验收 JSON](g4/runs/case-c-retry/g4-qc.json)，[接触表](g4/runs/case-c-retry/g4-qc/keyframes/contact-sheet.jpg)

每组 `artifacts/` 还保存源文记录、请求/响应、讲稿、分镜、TTS WAV、对齐、字幕、Video IR v0.2、编译时间线、成本、QA、最终 MP4 和 ffprobe/解码日志；`logs/commands.jsonl` 保留命令及流水线日志。

## 失败、修复与预算

- Case A 首次请求在沙箱 DNS 失败，失败请求被记入调用数并按完整单次费用上界计费；随后在允许的网络环境重跑成功。首次失败目录保存在 `g4/runs/failed/case-a-network-blocked/`。
- Case C 首次生成遇到非法 `bar:*` 动作目标，暴露了运行时将格式错误索引转成异常的问题。已将 `line:N`/`bar:N` 索引检查改为字段级结构化拒绝，增加回归测试后重跑成功。失败证据保存在 `g4/runs/failed/case-c-invalid-target/`。
- 失败尝试仍被保留；其保守费用上界分别为 $0.0128304 和 $0.0098166。三次成功加两次失败的记录上界合计 **$0.0522606**，不是供应商账单金额。每次运行独立上限为 7 次请求、$0.089813；失败网络/HTTP/无效 JSON 请求也消耗请求额度。未在上限外重试。

## 验证

自动测试命令及结果：

```console
python3 -m py_compile g1/video_ir.py g2/run_g2.py g3/config.py g3/worker.py g4/qc.py
python3 -m pytest g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q --disable-warnings
# 53 passed
python3 -m pytest tests/test_design_contracts.py -q
python3 g0/verify_design_snapshot.py
git diff --check
```

三个独立媒体/语义 QC 命令均返回 PASS：

```console
python3 -m g4.qc case-a
python3 -m g4.qc case-b
python3 -m g4.qc case-c
```

G3 应用环境验证：在隔离的 `g3/.venv` 按 `g3/requirements.txt` 安装 Python 依赖；用空数据目录启动 FastAPI，并读取 `/api/config/status`。服务返回 `ready: true`、DeepSeek/Kokoro/Remotion 三项就绪、无 blockers，且 `api_key_value_exposed: false`。G0 Python 依赖在新建 `/tmp` 虚拟环境按 `g0/requirements.lock` 安装成功；Remotion 依赖在新建 `/tmp` 目录执行 `npm ci --ignore-scripts --no-audit --no-fund`，358 个包安装成功。上述启动/依赖检查使用了本机已存在的模型、Chrome 和媒体工具，未在新目录重新下载这些大型外部资产，故完整冷启动仍未通过实证。

G3 测试包括重启时将残留 RUNNING 标记为可重试失败、独立尝试目录保留、取消状态、单 worker 锁和应用重启状态恢复；G4 测试验证调用预算预拒绝、失败请求消耗额度、费用封顶拒绝和 B/C 语义约束。上述恢复行为为测试覆盖，未进行真实进程强杀故障注入。

## 变更与交付

- 生成端：修正 DeepSeek 请求预算记账与失败请求处理；泛化数值/比较输入的提示；固定 G4 案例识别与通用 Video IR v0.2 文件名。
- IR：非法动作 target 返回含 `/target_id` 字段路径的结构化错误。
- G3：在不导入 G2 的情况下预检密钥，并在界面披露源文会发给 DeepSeek、AI 输出需核验及提交权限责任。
- G4：三份固定 Golden 输入、案例语义 QC、媒体抽检、许可证审计、重放说明、SHA-256 发布清单与本报告。
- `.env.example` 仅含空密钥占位。真实 `.env` 权限为 `0600`、已 gitignore；对 G4 运行文件按密钥字节扫描为 0 命中。密钥不写入产物或 Git。
- 运行命令、依赖安装步骤、单次费用/请求上限和已验证的 10–20 秒单机范围见 [`g4/README.md`](g4/README.md)。依赖许可证发现和分发前事项见 [`g4/G4_LICENSE_AUDIT.md`](g4/G4_LICENSE_AUDIT.md)。文件大小与 SHA-256 见 [`g4/G4_MANIFEST.json`](g4/G4_MANIFEST.json)。

## 未决事项

1. 重新按 README 从空目录下载并验证 Kokoro 模型/音色、FFmpeg/ffprobe、固定 Remotion 上游模板，并在干净 Chrome 环境完整重放安装流程。
2. 分发产品前处理许可证事项：当前 eSpeak loader wheel 同时携带 eSpeak NG GPLv3 动态库及语音数据；项目所用 FFmpeg 开启 GPL 和 x264/x265；确认模型/语音资产的通知要求，并评估 H.264/AAC 专利责任及目标司法辖区。
3. 本阶段对画面做了关键帧人工目视检查和机器检查，但没有独立评审者的正式 0–5 质量评分；10–20 秒范围外、长片、并发、多用户及生产可靠性均未验证。

完成 G4 后停止。下一步需先由用户验收本阶段；当前没有开始 M0 基线冻结，也没有执行 15–24 号规划内容。
