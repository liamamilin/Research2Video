# 「成稿 → 视频」Phase 1–3 优化验收报告（2026-11-23）

## 验收范围与结论分级

| 范围 | 状态分级 | 依据 |
|---|---|---|
| `information_motion`（短快讯 + 整期节目）Phase 1–3 全链路 | **生产入口已验证**（同配置隔离实例端到端跑通，并已重启线上 `8011` 生效） | 隔离目录 `autovideo/g3/data/trials/phase1-3-e2e-20261123`（外接盘）原样提交 Research `state/video.db` 真实 `submission_request`，连续跑通调试基准 `a927d2d8` + 未参与调试的异构财经整期 `3e903a99` |
| `illustrated_explainer` 短字幕（1.2）与 `diagram` 构图（2.3） | **已接入但端到端未验证** | 真实产物 `704257f6` 字幕切分（最长 `79 → 27` 字，`>36c: 19 → 0`）与真实 Remotion 静帧对比通过；本次未拉起 Draw Things 重跑整片 |
| `hybrid_cards` 单要点灰字修复（2.3） | **已接入但端到端未验证** | 单元测试 `test_hybrid_cards.py` 全通过，待下次 `information_cards` 日常出片验证 |

- **人工介入清单**：端到端 2 条验证均为 **0 人工介入**（未改讲稿、未改分镜、未手工配图、未修改生产配置，直接使用线上 `llm-settings.json` / `deepseek-v4.1-flash` / `voxcpm_anchor` / `brands`）。
- **整片产物留存**：
  - 调试基准对照前：`autovideo/g3/data/jobs/a927d2d8-866a-4e5f-9cdb-68287622c436/attempts/1/g2-run/artifacts/package/video.mp4`
  - 调试基准对照后：`autovideo/g3/data/trials/phase1-3-e2e-20261123/jobs/2e8a3041-32f4-487c-bc1c-08c5c17920c0/attempts/1/g2-run/artifacts/package/video.mp4`
  - 未调试异构整期对照前：`autovideo/g3/data/jobs/3e903a99-8b9a-466e-a9d3-f3eadff5cfee/attempts/1/g2-run/artifacts/package/video.mp4`
  - 未调试异构整期对照后：`autovideo/g3/data/trials/phase1-3-e2e-20261123/jobs/6c0fc33a-d39a-4798-9e86-3cf780bd6933/attempts/1/g2-run/artifacts/package/video.mp4`
  - 汇总记录：`autovideo/g3/data/trials/phase1-3-e2e-20261123/e2e-comparison.json`

---

## 端到端实测对照（Before vs After）

### 1. 调试基准：`a927d2d8`（苹果 Siri 和解快讯，1063 字，162s）

| 指标 | Before (`a927d2d8`) | After (`2e8a3041`，首片冷启守护进程) | 结论 |
|---|---:|---:|---|
| `comparison` 占比 | **46.7%**（7 / 15） | **33.3%**（5 / 15） | 明显下降，分布更均衡 |
| 最短单页时长 | **1.73s**（`card_3_p1` 闪现页） | **3.97s** | 闪现页彻底消除 |
| `reveal_phrases` 覆盖 / 视觉节拍数 | 0 页 / 52 cues | **13 / 15 页 / 61 cues** | LLM 调用次数未增（`4 → 4`，0 重试） |
| 最长字幕 / `>36c` 句数 / 对齐退化 | 45 字 / 3 / 0 | **40 字 / 2 / 0** | 全部强制对齐 PASS |
| 成片实测响度（母轨 → 封装成片） | `-21.00 → -24.00 dBFS` | **`-18.07 → -21.06 dBFS`**（peak `0.6319`） | `+2.94 dB` 抬升，零削波 |
| `STEP compile-ir`（含字幕对齐） | 56.42s | **1.37s**（复用 `pre-aligned` 轨道） | **-55.05s（41×）** |
| `STEP package`（封装方式 / 音轨相关性） | 19.92s（重编码 / `0.99991`） | **12.22s（`direct_copy` / `0.99996`）** | **-7.70s**，零二次视频压缩 |
| 整片 `ffmpeg -xerror` 全帧解码 | PASS | **PASS** | 完整可播 |

### 2. 未参与调试的异构真实选题：`3e903a99`（美国房贷 7.28% 财经整期，`ep=True`，1731 字，275s）

| 指标 | Before (`3e903a99`) | After (`6c0fc33a`，次片命中常驻热启) | 结论 |
|---|---:|---:|---|
| 整期开场 `agenda` 遮罩 | **`static list`（0–810 帧 / 前 27s 盖死开场）** | **`none`（直接展示居中 `343` 月供主画面）** | 开场钩子视觉完整恢复 |
| `comparison` 占比 | 28.0%（7 / 25） | **20.0%**（5 / 25） | 多样性提升（`big_number:8, concept_reveal:5, comparison:5, flow_diagram:4, price_change:1, bar_chart:1, title_card:1`） |
| 最短单页时长 | — | **5.23s**（max 14.23s） | 节奏平稳，无闪现页 |
| `reveal_phrases` 覆盖 / 视觉节拍数 | 0 页 / 97 cues | **23 / 25 页 / 111 cues** | LLM 调用次数未增（`12 → 12`，0 重试） |
| VoxCPM2 模型冷加载 (`load_seconds`) | **90.42s** | **`0.0s`（`warm_daemon=True`）** | **-90.4s**；`tts-and-align` 由 `415.76s → 361.54s`（**-54.2s**） |
| Qwen3 Aligner 冷加载 (`load_seconds`) | ~48s | **`0.0s`** | **~48s** 冷启清零 |
| `STEP compile-ir`（含字幕对齐） | 78.54s | **3.02s**（复用 `pre-aligned` 轨道） | **-75.52s（26×）** |
| `STEP package`（封装方式 / 音轨相关性） | 31.21s（重编码 / `0.99992`） | **19.71s（`direct_copy` / `0.99996`）** | **-11.50s**，零二次视频压缩 |
| 非远程 LLM 本地阶段总耗时（配音+编译+渲染+验片+封装+品牌） | 1069.2s | **931.9s** | **-137.3s（-2.3 分钟）** |
| 最长字幕 / `>36c` 句数 / 对齐退化 | 42 字 / 3 / 0 | **37 字 / 1 / 0** | 全部强制对齐 PASS |
| 成片实测响度（母轨 → 封装成片） | `-21.01 → -24.14 dBFS` | **`-18.05 → -21.14 dBFS`**（peak `0.6461`） | `+3.00 dB` 抬升，零削波 |
| 整片 `ffmpeg -xerror` 全帧解码 | PASS | **PASS** | 完整可播 |

---

## 目视抽帧（Contact Sheet）核查

对比了 4 张整片 `contact-sheet.jpg`（前后各 2 张）：
1. **`3e903a99` 开场（`card_1_p1` / `card_1_p2`）**：修复前整张前 2 行被静止 10 条目白板目录遮死；修复后开场直接落到居中绿条大字 **`343`**（月供多出）与 `FSD 25倍` 卡片，重点一眼可见。
2. **`big_number` 版式（两片共 12 页）**：修复前全部偏左、右侧 60% 大片留白；修复后全部居中平衡，绿条分割与字级层次清晰。
3. **`comparison` 与 `bar_chart` 焦点层级**：修复后讲述侧明显抬升高亮、非讲述侧退暗（`opacity: 0.56` / `0.42`），对比栏与柱状图（`54%` vs `18%`）视线落点显著清晰。
4. **`a927d2d8` `card_3_p1`**：修复前 1.73s 闪现对比页已消失，替换为清晰可读的 `$25 每台基础金额` + 流程图。

---

## 回归测试与遗留记录

- 全量测试：`python3 -m pytest tests g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q` → **`1166 passed, 1 skipped, 0 failed`**（顺手修复了既有历史 mock `autovideo/g2/tests/test_voxcpm_reference.py:78` 缺少前期 `on_chapter_ready` 参数的问题，并在 `mlx_daemon._ensure_daemon` 补充了 `TimeoutError` 保护避免误删忙碌守护进程的 socket）；
- 局部说明：`storyboard-plan` 尾部耗时仍受远端 LLM API 响应波动影响（如 `3e903a99` 远端分镜由 `448s` 变为 `504s`，但未增加调用次数或重试）；Phase 3 已将本地模型冷启、字幕对齐与封装重编码瓶颈基本清除，后续进一步大幅提速需进入 Phase 4（渲染与短句配音深水区）。
