# G2 真实模型自主出片

`run_g2.py all` 使用 DeepSeek `deepseek-flash` 生成 NarrationPlan 和 StoryboardPlan，再用 Kokoro 中文 TTS、PCM 短语边界对齐、Video IR v0.2 validator 和 G1 固定组件渲染器制作 Case A。模型只能返回 JSON 并经过本地 schema/语义校验；renderer 只执行注册组件。提示词、源材料、每次请求/响应（不含 Authorization 或思考内容）、调用时长、token 和费用上界会随产物保存。`.env` 中配置 `DEEPSEEK_API_KEY`；key 不会写入日志或产物。

Case A 的 StoryboardPlan 固定为三场：开场、中间 flow_diagram、结尾。中间场以三条短语 Beat 展示场景显现、传输路径启动和信道受扰；目标 ID 按组件 props 校验，Beat 偏移限制为 ±1000ms。

运行环境沿用 G0/G1 本地依赖：

```console
g0/.venv/bin/python -u g2/run_g2.py all
```

媒体调用只向 `https://api.deepseek.com/chat/completions` 请求 `deepseek-flash`，thinking enabled、reasoning effort high。JSON mode 保证 JSON 字符串；最终数据还要通过项目 schema 与语义校验。请求上限为每次 8192 个 completion tokens、每个阶段最多 2 次修复重试、全视频最多 7 次 API 调用；按当前高峰未命中价计算的单视频最坏预算低于 0.09 美元。每次真实 TTS 若实测超出 10–20 秒，最多要求模型重写两次并重新实测。没有 key、认证失败或服务不可用时清晰失败，不回退为 mock。每个运行创建独立 `g2/runs/<UTC 时间>/` 目录保存真实请求、响应、费用上界、媒体、帧采样和命令日志。
