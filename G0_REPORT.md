# G0 验收报告

**Status: PARTIAL** · 2026-10-13（Asia/Shanghai）
**真实声画技术链路：PASS。人工播放、试听与用户验收：PENDING。**

已真实生成一条 **14.000 秒中文有声动画 MP4**，含中文 TTS、实际音频驱动的波形动画、烧录字幕及同源 SRT。已经停止在 G0，没有进入 G1。总体保留 PARTIAL，是因为 `10_AGENT_EXECUTION_BRIEF.md` 要求的人工播放验证尚未由人完成；本报告不以媒体解码或音频相关性代替人对中文自然度、声音清晰度和观看体验的判断。

## 1. 交付物

路径均相对于本项目根目录 `/Users/milin/2026/软件开发/AutoVideo_Final_Docs`。

| 产物 | 路径 |
|---|---|
| 最终视频 | `g0/artifacts/G0_声音与波形.mp4` |
| 真正合成的中文旁白 | `g0/artifacts/narration.wav`，以及 `n1.wav`–`n4.wav` |
| 字幕与时间证据 | `g0/artifacts/captions.srt`、`alignment.json` |
| 实际媒体检查 | `g0/artifacts/ffprobe.json`、`qa.json` |
| 9 张关键帧 | `g0/artifacts/keyframes/` |
| 关键帧总览 | `g0/artifacts/contact-sheet.jpg` |
| 产物尺寸与 SHA-256 | `g0/artifacts/manifest.json` |
| 实际命令、退出码、耗时 | `g0/logs/commands.jsonl` |
| 渲染原始输出 | `g0/logs/remotion-render.log`、`remotion-render-02.log`、`remotion-render-03.log` |
| TTS 实测输出 | `g0/logs/tts-generation.log`、`g0/artifacts/tts-metrics.json` |
| 依赖安装／下载／检查日志 | `g0/logs/`，索引见 `bootstrap-commands.json` |
| 复现说明与实验脚本 | `g0/README.md`、`run_g0.py` |
| 输入／运行参数 | `g0/narration.json`、`props.json` |
| 依赖锁与可信源码证据 | `g0/requirements.lock`、`remotion-package-lock.json`、`trusted-source.json` |

早期可解码版本保存在 `g0/artifacts/attempt-01/`、`attempt-02/`、`attempt-03/`，仅用于记录修正过程，最终交付以根 `artifacts/` 的 MP4 为准。旧 manifest 是对应运行时的原始快照，其原路径不随归档改写；最终 manifest 包含归档文件目前位置与哈希。

## 2. 工作区审计与边界

- 开始时目录只有设计文档、Schema、示例、测试和 `.DS_Store`，没有产品源码；`git status` 返回“not a git repository”。不存在可以报告的原工作树提交或未提交源码差异。
- 已读 `START_HERE.md`、`README.md`、`AGENTS.md`、`RELEASE_STATUS.md`、`AGENT_FIRST_TASK.md` 和 00–14 全文；浏览 15–24 的未来阶段边界。
- 原发布清单的 **45 个文件全部保持 SHA-256 与字节数不变**。没有改动 Schema、Registry、规范、原测试、系统配置或其他项目。
- G0 所有实验、依赖、模型、缓存和日志放在 `g0/`。新建本项目 Git 仓库；第三方克隆、虚拟环境、模型、二进制和缓存不入 Git。
- 没有开发 Web UI、通用 provider 接口、IR v0.2 编译器、六组件库或 LLM 工作流。旁白稿是本次固定实验输入；不是 G2 自主出片。

## 3. 实测环境与技术路线

| 项目 | 实测值 |
|---|---|
| 系统 | macOS 26.6.2，ARM64 |
| Node / npm | v22.21.1 / 10.9.4 |
| Python | 3.10.13，专用虚拟环境 `g0/.venv` |
| Remotion | 4.0.535，安装的全部 Remotion 包版本一致 |
| Chrome | 155.0.8059.40 |
| FFmpeg / ffprobe | 7.0 / 7.0，macOS ARM64，项目内二进制 |
| TTS | 本地 Kokoro-82M v1.0，真实 ONNX 推理 |
| 中文音色 | `zf_xiaoxiao`；speed=1.0，无音频倍速处理 |
| 推理与中文 G2P | kokoro-onnx 0.6.1、onnxruntime 1.23.2、misaki 0.9.4 |
| 原始 TTS 音频 | 24,000 Hz，mono，PCM 16-bit WAV |

实际执行的是 [Remotion 官方 Audiogram 模板](https://github.com/remotion-dev/template-audiogram) 的 commit `a7a1befca086f72eaf721a8b85d9e5038453da94`。`src/` 与 `remotion.config.ts` 未改，执行前用 `trusted-source.json` 的哈希检查。输入只更换 JSON 参数与本地媒体；官方 Oscilloscope 按实际 PCM 计算波形。Python 使用固定 FFmpeg 参数做画面布局和中文字幕叠加，子进程采用参数数组、固定路径和超时，没有 `eval`、`shell=True`，没有把用户文本或模型返回内容当源码运行。

本机常见云 TTS 密钥未配置，仅记录变量是否存在，没有读取或记录密钥值。macOS 的中文 `say` 音色存在，但**未使用**：系统声音许可限制公开分享及商业场景，因此选择本地 Kokoro 实验。[Apple macOS Tahoe 许可 §2F](https://www.apple.com/legal/sla/docs/macOSTahoe.pdf)

## 4. 中文音频、字幕与动画证据

主题：“声音，如何变成波形？”原文与四句讲稿保存在 `narration.json`。每句由同一音色实际生成 WAV，按实际样本数拼接并插入明确停顿；开头留白 250 ms，尾部仅补零到完整视频帧，不拉伸声音。

| 旁白 | 实测字幕窗口 |
|---|---|
| 声音可以用波形表示。 | 0.250–2.126 s |
| 看，说话时，曲线随声音上下起伏。 | 2.685–5.767 s |
| 停顿一下，曲线就接近水平。 | 6.328–8.808 s |
| 这条动画，直接来自真实的语音信号。 | 10.105–13.191 s |

边界来源是**分别合成的真实短语 PCM**，结合 `abs(sample)>0.003` 的实测活动范围及 50 ms 边缘保护。SRT 与烧录字幕使用同一份时间线；时间落到画面时受 30fps 的一帧量化影响。没有伪造 provider 词级时间戳，没有声称强制逐词对齐。原模板的英文字幕分页被隐藏，中文字幕以测量过宽度的 PNG 在固定底部安全区显示，每句一行、54px 字号，未直接套用英语空格断行。

关键帧为 0、22、56、130、210、287、340、405、419。最终波形区域中：

- 说话时的采样帧垂直变化明显，显示幅度大于停顿时至少 8 px；最终最大波形范围小于 145 px，避免裁剪。
- 第 287 帧（9.567 s）位于真实插入的停顿中，曲线接近水平，约 9 px 的线宽范围；第 130／210／340 帧对应中文旁白中的实际起伏。
- 动画读取真正音频样本，每帧更新，不是静态截图、随机预设运动或静音演示。

代理已目视检查真实关键帧：中文无乱码，标题／波形／字幕分区清楚，字幕没有遮住波形。**代理没有听到视频声音；人声自然度、多音字准确性、完整播放感受和手机阅读体验未由人验证。**

## 5. 媒体验收与耗时

| 检查 | 实测结果 | 状态 |
|---|---|---|
| 容器与视频 | MP4，H.264，1920×1080，yuv420p | PASS |
| 帧率、帧数、时长 | 30/1 fps，420 帧，14.000 s | PASS |
| 音频流 | AAC，48,000 Hz；由真正 TTS WAV 编码 | PASS |
| 完整实际解码 | `ffmpeg -v error -xerror` 解码全部音视频，退出码 0 | PASS |
| 音频来源一致性 | 解码音频与原 TTS 相关系数约 0.9979 | PASS |
| 响度与峰值 | -16.4 LUFS；true peak -4.1 dBFS，无峰值削波证据 | PASS |
| 动画变化 | 说话帧与停顿帧呈现可辨认的波形差异 | PASS |
| 中文字幕 | 4 个实测短语窗口，同源 SRT／烧录，无超宽 | PASS |
| 原设计包保护 | 45 个原文件哈希未变；隔离原包测试 23 passed | PASS |
| 人工播放／中文试听 | 未取得人工验收结果 | PENDING |

本机生成与准备 TTS／画面资产约 **3.009 s**；这个数包含模型加载及资产准备，不是独立纯推理 benchmark。首条成功 Remotion 渲染 **38.771 s**，降低增益后的最终 Remotion 渲染 **34.071 s**，并发 2；FFmpeg 画面合成约数秒，准确值在 `commands.jsonl`。仅一份固定材料，不能据此推断平均故障率、全离线性能或产品吞吐。

## 6. 实际命令与失败记录

主要操作命令（所有具体 Node／FFmpeg argv、工作目录、返回码和耗时见 `g0/logs/commands.jsonl`）：

```console
python3 -m venv g0/.venv
g0/.venv/bin/python -m pip install kokoro-onnx 'misaki[zh]' soundfile pillow pytest jsonschema static-ffmpeg
git clone --depth 1 https://github.com/remotion-dev/template-audiogram.git g0/vendor/remotion-audiogram
npm install --ignore-scripts --no-audit --no-fund --cache ../../.npm-cache
g0/.venv/bin/python -u g0/run_g0.py prepare
g0/.venv/bin/python -u g0/run_g0.py render
g0/.venv/bin/python -u g0/run_g0.py finish
g0/.venv/bin/python -u g0/run_g0.py verify
g0/.venv/bin/python -m pytest tests -q
g0/.venv/bin/python -u g0/verify_design_snapshot.py
git init -b main
```

`npm install` 在模板目录执行；模型与 FFmpeg 的真实下载命令见 `bootstrap-commands.json`，进度日志另存。安装 Python 依赖的完整输出最初只在工具会话中出现，未保存成完整磁盘日志；报告不将后补索引冒充安装原始日志。

已保留的问题与修正：

1. 默认沙箱的外网 DNS 失败；仅对授权的项目下载和 Chrome 渲染采用工具允许的扩展权限。
2. `static-ffmpeg` 成功下载解压后对输出目录的推断错误，报 FileNotFoundError；实际二进制在 `g0/darwin_arm64/`，本项目内修正执行权限后运行成功。URL 中 v8.0 不代表二进制版本，实际为 7.0。
3. 直接运行 Remotion 自带 ffmpeg/ffprobe 缺少其依赖的 dylib 搜索环境，失败；独立媒体检查使用前述实际可运行的 FFmpeg 7.0。Remotion CLI 自身渲染正常。
4. 第一次 Remotion 渲染由于手填 public URL 前缀返回 404；修正为沿用官方 `staticFile` 默认地址并替换本地媒体，没有修改模板 JS/TS。后两次渲染均成功。
5. 首版关键帧发现默认显示增益使波峰被裁剪；仅把 JSON `amplitude` 从 4 降为 1，重新渲染后修复。最终波形合成又增加左右内边距，保留卡片边框。
6. 根目录原测试结果为 **22 passed / 1 failed**：发布清单覆盖测试要求磁盘只能有原清单文件，但本轮新增了 G0 工程文件，且开始已有 `.DS_Store`。未篡改清单或测试；在只含原清单内容的临时副本上复测 **23 passed**。这只是设计包保护证据，不是视频验收依据。
7. 合成日志发现 PNG 输入默认 25fps 导致补帧；最终把所有图像输入显式设为 30fps，并采用 passthrough 帧同步，保留逐帧波形时基。一次修正调用同时指定 `-r` 与 passthrough 被 FFmpeg 拒绝，保留错误日志后移除冲突的 `-r` 参数。
8. 首次暂存后 `git diff --cached --check` 报原规范中的 Markdown 换行空格及真实终端日志的进度行尾空格。没有为消除检查提示而改动冻结文档或原始日志；新增代码和数据单独做空白检查。

## 7. 费用与授权

- **TTS 云请求：0；API 费用：0。** 使用本地模型推理，不需要用户密钥。设备、电费、带宽成本没有测量，不能作为“总成本为零”的结论。
- [Kokoro-ONNX](https://github.com/thewh1teagle/kokoro-onnx) 标示代码 MIT；[Kokoro-82M 官方模型卡](https://huggingface.co/hexgrad/Kokoro-82M) 标示 Apache-2.0。实际下载文件 SHA-256 在 `tts-metrics.json`；模型、音色文件不打包进 Git。权重许可证核查不代替商业产品的全部声音权利审查。
- [Remotion 当前许可](https://www.remotion.dev/docs/license/faq) 允许个人、至多 3 人团队及尚未商用的技术评估使用免费许可。本次按本地技术评估使用；若团队、客户协作或运营主体变化，应按实际条款核实，公司自动化许可公开价为 $0.01/render、$100/月最低消费。[价格页](https://www.remotion.dev/docs/license/pricing)
- 字体使用本机 macOS 的 Hiragino Sans GB 生成栅格文字，未分发或嵌入字体源文件；跨系统的可分发中文字体仍需 G1 选型。[Apple 字体条款 §2E](https://www.apple.com/legal/sla/docs/macOSTahoe.pdf)
- 无 BGM、无第三方图片、无原模板播客音轨进入成片。画面资产为本次程序化绘制，声音为本次真实合成。
- FFmpeg 本机包启用 GPL／libx264，本轮只在本地运行且二进制不入 Git；后续软件打包分发应遵循实际构建许可证。[FFmpeg 官方说明](https://ffmpeg.org/legal.html)

## 8. 路线判断、风险与下一步

| 路线 | 本轮测量 |
|---|---|
| A：自由 LLM 代码 | NOT TESTED；本轮禁止执行该路线 |
| B：可信组件／结构化数据 | 官方固定波形组件＋JSON 的 G0 声画链路通过；尚未实现产品 IR／LLM 编排 |
| C：图像模型＋动画 | NOT TESTED；未调用图像／视频模型 |

本轮只证明一段固定中文旁白能经本地 TTS、Remotion 和 FFmpeg 成为真实有声动画，不证明任意文本都能自主生成知识解释视频，不证明 G1–G4 已完成。官方模板可能需要首次联网加载字体；完全断网、从空机器安装、词级对齐、多音色自然度、手机观看、其他操作系统、产品级路径／Schema 安全测试都未验证。

建议用户先播放最终样片，验收声音和字幕节奏。**只有用户明确接受 G0 并批准 G1 后**，再按 `13_G1_TEST_MATRIX.md` 开发 v0.2 的受控 IR／Renderer，优先验证通信噪声的 `reveal → activate_path → mark_disrupted`。本次停止，不执行该建议。

## 9. 文件变更与提交

新增 `.gitignore`、本报告，以及 `g0/` 内的实验脚本、JSON 输入、依赖锁、版本记录、可信上游哈希、真实媒体、关键帧和日志。原始规范／Schema／测试未改动。完整本轮新增文件清单见 `g0/CHANGED_FILES.txt`；初始化 Git 后原包文件会首次被纳入版本管理，不能把“首次 tracked”当作内容修改。

**Git commit：交付时补入具体证据提交哈希；随后仅记录报告提交。** 未推送远程，未部署，未进入 G1。
