# 已归档的生产配置

本目录**不会**被 `core/video/production.load_all` 加载（只扫 `config/production/*.yaml` 顶层）。

| 文件 | 原因 |
|------|------|
| `deep_dive-2/3/4.yaml` | 2026-10-25 助手验收一次性绑定；对应 Job 多已 `enabled: false`，日常主链不用 |
| `flash.yaml`、`point_short.yaml`、`ai-developer-roundup-long.yaml`、`ai-non_tech-one_point-short.yaml`、`ai-non_tech-quick_take-short.yaml` | 2026-11-20 快讯扇出收紧：日常只留 `flash_daily` / `flash_each`；夹具测例经 `prod_env` 临时拷回 |
| `deep_dive.yaml` | 2026-11-20 与 `ai_media_deep_dive` 重复；日常只留后者（`auto: 1`） |
| `ai-non_tech-headline-short.yaml`、`ai-non_tech-deep_dive-long-2.yaml` | 2026-11-20 日报旁路旧键；主链已有 `tech_daily` / `economy_daily` |

需要复用时移回上一级并跑相关测试。减负计划见 `spec/config-simplification.md`。
