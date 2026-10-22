# LLM providers and the free-first fallback

状态：已实现（2026-10-19）。背景：Open Design（amr-link）账户配额和余额同时用完
（`insufficient_balance`，`coding_plan_exhausted`），10-24 才重置，临时切到了
opencode Go。这件事暴露了两个一直存在的问题：换一个服务商要手填三个值，填错一个
就打到别的厂商；换服务商会把上一个服务商的密钥覆盖掉，想切回去还得重新去拿。

## 服务商预设（`core/providers.py`）

一个预设 = `base_url` + 密钥存在哪个环境变量（`api_key_env`）+ 必需的请求头
（`extra_headers`）+ 已知能跑的模型（`models`，其中免费的列进 `free_models`）。
设置页「服务商」下拉选一个，三项一起填，不会出现「地址是 A 家、密钥是 B 家」
的错配。

**每个厂商有自己的密钥变量**（`OPENDESIGN_API_KEY`、`OPENCODE_API_KEY`、
`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`），不再共用一个 `LLM_API_KEY`。几家的密钥
可以同时存在 `.env` 里，切服务商只是换读哪一个变量，不用重新填密钥，前提是
那把密钥曾经存过一次。`LLM_API_KEY` 仍然认，是旧配置的兼容名。

## 免费优先、付费兜底（`core/llm.py`）

`ai.model` 填一个免费模型；`ai.fallback_models` 是一串付费模型，按顺序只在
`model` 返回**配额或余额错误**时才会用——不是任何失败都换模型，普通的网络抖动
仍然重试同一个模型。`review_ai` 同样支持这两个字段。

```yaml
ai:
  model: "longcat-2.5-preview-free"
  fallback_models:
    - "minimax-m3"
    - "deepseek-v4.1-flash"
```

一次 `LLMClient` 换到某个付费模型后，这次任务剩下的调用都用它，不会每次都先
试一遍已知用不了的免费模型。换模型换到的那次请求，不计入原模型的重试次数，
重新给一份完整的重试配额。全部模型都报配额错误时，报错里会列出试过的每一个
模型。

设置页「AI 模型」卡片上，选中一个有免费模型的服务商后，模型 ID 下面会出现
免费模型的按钮，点一下就填进模型 ID；「付费兜底模型」是一个逗号分隔的列表。

## 审校模型（`review_ai`）

审校（`spec/video-adaptation-audit.md`）可以配一个独立的模型，不配就和 `ai`
用同一个。两者结构完全一样（同一份 `_LLM_SECTION` 校验规则），设置页上是单独
一张可选卡片，没配置时折叠提示「视频文稿审校用上面的 AI 模型」，配置后可以
删除整节、改回跟随 `ai`。

2026-10-19 的现实情况：opencode Go 没有 amr-link 那种「唯一一个贵、强」的模型，
`review_ai` 暂时和 `ai` 走一样的免费优先策略，审校质量会比 10-24 恢复 Open
Design、换回 `deepseek-v4-pro` 之前低一些。

## 切回 Open Design（10-24 之后）

设置页「服务商」选回 **Open Design（amr-link）**，地址和密钥变量名会自动填好；
如果 `OPENDESIGN_API_KEY` 还没存过（这次切换前就没存过），在「API 密钥」卡片
粘贴一次。`ai` 和 `review_ai` 都要切。

## 已知的限制

- **没有实时价格接口**：`free_models` 是人工维护的提示，不是厂商 API 查出来的，
  服务商新增/下线免费模型时需要手动改 `core/providers.py`。
- **成本统计会在切换到付费兜底时失真**：`ai.pricing` 只按配置里写的那一个模型
  算钱，实际回答的模型换了之后，预算页的估算还是按免费模型的单价算（通常是 0），
  会低估花费。
