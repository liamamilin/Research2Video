# G2 真实模型自主出片

`run_g2.py all` 使用 DeepSeek `deepseek-flash` 生成 NarrationPlan 和 StoryboardPlan，再用 Kokoro 中文 TTS、PCM 短语边界对齐、Video IR v0.2 validator 和 G1 固定组件渲染器生成视频。模型只返回 JSON 并经过本地 schema、来源引用和组件/动作校验；renderer 只执行注册组件。请求/响应（不含 Authorization 或思考内容）、耗时、token 和费用上界保存在每次运行的证据目录。`.env` 中配置 `DEEPSEEK_API_KEY`；key 不会写入日志或产物。

通信噪声 Case A 使用三场结构；中间场以 Beat 展示发送、传输路径启动和信道受扰。其它主题可用 comparison、bar_chart 等已注册组件；类别和数值必须来源于输入，并由 G4 Golden Case QC 检查。

运行环境沿用 G0/G1 本机依赖：

```console
g0/.venv/bin/python -u g2/run_g2.py all
```

真实调用只请求 `https://api.deepseek.com/chat/completions` 的 `deepseek-flash`，启用 thinking 和 high reasoning effort，以 JSON mode 返回。每次响应最多 8192 个 completion tokens，每视频最多 7 次实际 API 请求；HTTP 和网络失败也计数。按当前高峰未命中价计算的单视频最大费用上界约为 $0.089813。TTS 实测不在 10–20 秒时，最多改写并重测两次。无 key、认证失败或服务不可用时清晰失败，不回退为 mock。运行目录包含媒体、帧采样、成本、source、IR 和命令日志。

G4 三个真实案例的运行、独立 ffprobe/解码、抽帧、布局风险信号和案例语义验收见 [`../g4/README.md`](../g4/README.md)。
