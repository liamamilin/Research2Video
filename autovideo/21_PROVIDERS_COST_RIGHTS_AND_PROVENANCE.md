# 21｜模型供应商、成本、隐私、素材授权与来源记录

## 1. 目标

让系统能在多个供应商和媒介之间选择，同时确保成本可估算、用户材料不意外泄露、生成资产可溯源、商用前明确授权限制。

## 2. 能力注册而非供应商名硬编码

每个 provider/adapter 记录以下能力（概念结构）：

- `modality`：LLM、TTS、image、video、music、sfx、alignment。
- `supported_inputs`：text/image/reference_audio/first_last_frame 等。
- `output`：帧率、分辨率、画幅、单次时长、是否有音轨、文件类型。
- `latency_policy`：任务型/同步，超时、轮询与取消行为。
- `pricing`：单位、最低计费、可能的额外费用、更新时间、不能估算的项目。
- `rights`：输出可用于哪些用途、模型/参考材料许可与署名要求。
- `privacy`：数据发送区域、保留期/训练用途（只记录经供应商确认的信息）。

仅在 H1/H2 需要相应媒介时新增 adapter；不为了“插件平台化”先做十几个空 provider。

## 3. 按镜头成本与预算决策

视频成本不只是 API 成本：还包括渲染耗时、重试、音频、图像、复检，以及生成失败留下的沉没成本。

```text
estimated_total = Σ(provider_charge_by_stage + expected_retry_charge)
                + local_render_cost_estimate
                + optional_QA_cost
```

- `estimated`、`actual` 和 `unknown` 三态分开记录，不把未知费用当作零。
- 任务启动前应能设置货币、预算上限及是否启用昂贵的视频模型。
- 仅当预估成本可接受、表现目标明确时才允许尝试视频模型；无法估算应询问明确授权或使用低成本后备路径。
- 对价格随时间变化的服务，注明采集日期与来源；**文档不写固定长期价格承诺**。

## 4. 资源与资产来源

资产记录至少包括：`origin`, `provider/model/version`, `prompt_version`, `hash`, `created_at`, `license/usage_rights`, `reference_assets`, `status`, `cost`。必须清楚区分：用户原文、用户上传资产、项目授权素材、AI 生成资产。

- 用户自己提供的素材仍可能不具备分发许可，不能默认商用可用。
- 模型输出是否可商用、是否需标识/署名，必须按当时的具体服务协议确认。
- 人声/声音克隆应有权限和使用证明，不擅自模仿现实人物声音。
- H2/H4 可研究 C2PA Content Credentials 等行业来源标准；但**内部 manifest 不等于已经生成签名的 C2PA 证书**。若真要宣称符合标准，必须使用相应工具链实测校验。

## 5. 许可审查清单

- Remotion 授权：使用主体/团队人数/商业模式变化时重新核对。参考：https://www.remotion.dev/docs/license/pricing
- 任何开源权重：单独核查代码授权、模型权重授权和商业用途限制；不能用“GitHub 公开”推断可自由商用。例：https://github.com/Lightricks/LTX-2
- 配乐与音效：确认视频发布平台及商业用途、是否必须署名、是否允许再分发素材文件。
- 字体：确认嵌入/成片使用授权，禁止打包分享无授权字体源文件。
- 来源规范参考：https://spec.c2pa.org/specifications/

## 6. 隐私和防护

- 默认本地保存敏感原文；外部调用需 UI 明示可能上传的内容类型。
- `prompt`, `model response`, `logs` 可能包含原文或个人数据；设置脱敏、删除策略和调试开关。
- 账户密钥服务端保管，不写视频、日志、manifest、前端响应。
- 模型生成的 URLs、脚本、HTML、媒体元信息均不可信；只通过受控拉取/解析/转码进入素材库。
- 本地优先不等于离线；需要明确标记哪些阶段会访问外部服务。

## 7. Go/No-Go

任何新供应商投入生产前，必须完成：能力核实、真实 API 样片、错误码映射、费用记录、素材权利审查、超时/取消/配额测试、失败后备策略和隐私告知。

**商用上线前单独法律/授权复核，不以本设计草案替代合同审查。**
