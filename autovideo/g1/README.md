# G1：Video IR v0.2 技术验证

本目录实现 G1 的受控数据到媒体链路：Python 在渲染前校验顶层与组件 Schema、真实媒体路径和 SHA-256、动作注册表、引用与时间线；Remotion 从固定六组件 switch 渲染通过校验的 JSON。输入文本不会作为程序代码执行。

## 本机重放

本项目的验证环境沿用 `g0/` 已安装的依赖与授权本地模型：Python 3.10.13 虚拟环境、Kokoro-82M v1.0、Remotion 4.0.535、Node 22.21.1、Chrome，以及项目内 FFmpeg/ffprobe。模型权重、虚拟环境、二进制与 `node_modules` 都不进 Git；`run_g1.py render` 会为渲染器链接当前 G0 的锁定 Remotion 安装。

在仓库根目录执行：

```console
g0/.venv/bin/python -u g1/run_g1.py all
```

脚本会生成真实中文短语 TTS 与 Video IR v0.2 fixture，运行 validator pytest，编译帧时间线，渲染、完整解码并比较关键动作帧。原有视频和音频被保留；若要重跑，请先将整个 `g1/artifacts/` 和 `g1/fixtures/assets/` 移到仓库外的归档目录。`g1/logs/commands.jsonl` 记录固定参数、工作目录、退出码与耗时，逐步重跑也可单独使用 `prepare`、`test`、`validate`、`render`、`verify`、`report`。

旁白边界来自分别生成的实际 PCM 短语活动范围，只有 phrase 级对齐。代码、静态 fixture、真实音频、渲染 MP4、关键帧、QA JSON 和命令日志构成可复核输入与输出。G1 不启动 LLM、云 TTS、图片/视频模型或 G2 工作流。
