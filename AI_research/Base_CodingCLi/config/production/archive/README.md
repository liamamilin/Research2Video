# 已归档的生产配置

本目录**不会**被 `core/video/production.load_all` 加载（只扫 `config/production/*.yaml` 顶层）。

| 文件 | 原因 |
|------|------|
| `deep_dive-2/3/4.yaml` | 2026-10-25 助手验收一次性绑定；对应 Job 多已 `enabled: false`，日常主链不用 |

需要复用时移回上一级并跑相关测试。减负计划见 `spec/config-simplification.md`。
