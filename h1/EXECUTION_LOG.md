# H1 执行记录

日期：2026-10-13（Asia/Shanghai）

恢复点：`b111c09`

最终评测版本：v3；v1/v2 仅保留作 QA 历史，不纳入评分。

## 环境与安全探针

- 检查 Docker 可执行文件和 daemon：Docker 可执行文件存在，但 daemon 不可用。
- 检查 macOS `sandbox-exec`：系统版本将其标记为 deprecated。
- 执行内存限制探针 `ulimit -v 2097152`：失败，`setrlimit failed: invalid argument`。
- 结论：无法验证 A 路线要求的网络、密钥、文件访问与资源限制；跳过 A，没有执行自由/LLM 撰写的 JS、TS 或 shell。
- Remotion 固定渲染器使用冻结的本地 IR、旁白和素材；Chrome 在默认受限上下文中 SIGABRT，因此使用受控的本机固定渲染命令生成 C。未发起渲染期网络请求。

## 生成和渲染

- 内置 image_gen：6 个背景素材生成请求，0 次重试。工具没有暴露模型版本或账单金额，详见 `asset-manifest.json`。
- 渲染脚本：`python3 h1/render_c.py`。针对 case-a/b/c 生成最终 `C_image_motion_v3_full.mp4`；最终渲染耗时合计 125.380 秒。每例命令、输入哈希、输出哈希、流信息、耗时和解码状态已记录在 `runs/C_RENDER_SUMMARY_v3.json` 及案例日志中。
- 配对与编码：`python3 h1/extract_pairs.py`。产生六组、12 段 B/C 片段及路线密钥；六组音频 PCM 完全相同。
- 联系表：`python3 h1/create_contact_sheets.py`。生成内部 QA 表与不显示路线名称的盲编码表。

## 验证命令

在仓库根目录运行：

```sh
python3 -m py_compile h1/asset_adapter.py h1/render_c.py h1/extract_pairs.py h1/create_contact_sheets.py
g0/.venv/bin/python -m pytest h1/tests -q --disable-warnings
g0/.venv/bin/python -m pytest tests/test_design_contracts.py tests/test_roadmap_contracts.py -q --disable-warnings
```

结果分别为语法编译通过、4 passed、19 passed。媒体全流解码、B/C 音频 PCM 哈希/相关性、帧数/时长和文件哈希由 `extract_pairs.py` 与 `PAIR_MEDIA_MANIFEST_v3.json` 记录；所有 12 段通过。`git diff --check` 通过。

## 退出状态

技术样片已完成；A 路线未实施。用户于 2026-10-13 取消独立评议，并要求当前优先聚焦本地产品功能，不建设分发沙箱。研究性 H1 报告仍为 PARTIAL；不填写未收集的人工评分或工具未披露的成本。
