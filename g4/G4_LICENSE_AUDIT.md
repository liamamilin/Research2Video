# G4 许可证与服务条款核对

**核对日期：2026-10-13。** 本文件记录当前代码和本机验证用依赖的工程级来源核对，不构成法律意见；在向第三方分发产品或开展商业服务前，按实际二进制、组织人数、使用地区和部署方式复核。

| 项目 | 核对结果 | 对 G4 的处理 |
|---|---|---|
| Remotion 4.0.535 | Remotion 官方说明个人及不超过 3 人的组织可使用 Free License，含自动化；4 人以上组织的自动视频产品需看 Company / Automators 许可，官方标价按成功 render 计费且有月最低额。使用方式和组织人数改变时必须复核。 | 当前研发机单人本地验证，尚未建立商业多用户服务。G4 单机可以运行；产品对外提供自动生成前需确认授权。官方 [定价](https://www.remotion.dev/docs/license/pricing) 与 [FAQ](https://convert.remotion.dev/docs/license/faq)。 |
| FFmpeg 7.0 macOS ARM64 | 本机 `ffmpeg -buildconf` 显示 `--enable-gpl`、`--enable-libx264`、`--enable-libx265`、`--enable-libvidstab`。FFmpeg 官方说明启用 GPL 部分会使构建受 GPLv2+ 约束；x264/x265 等外部库也会触发 GPL 构建。仓库不提交该二进制，但 README 让用户自行下载。 | 随产品提供该二进制时须提供相应许可文本、来源/构建信息和 GPL 合规材料；不要把它当成 LGPL-only FFmpeg。官方 [FFmpeg LICENSE](https://github.com/FFmpeg/FFmpeg/blob/master/LICENSE.md)。 |
| eSpeak NG / `espeakng-loader==0.2.4` | 新建 G0 环境中的 wheel 含 `libespeak-ng` 动态库和 `espeak-ng-data` 语音数据。PyPI wheel metadata 未声明许可证/附带许可证文件；上游 loader 仓库标为 MIT，但其打包的 eSpeak NG 及数据为 GPLv3。它由 G0 锁定依赖带入。 | wheel 来源可追溯，但依赖包未在安装环境保留 GPL 通知。分发包含该依赖的应用或环境前，需补齐上游通知/源码获取方式并请法务判断整体分发义务。上游 [eSpeak NG](https://github.com/espeak-ng/espeak-ng) 和 [loader 源码](https://github.com/thewh1teagle/espeakng-loader)。 |
| Kokoro ONNX runtime | `kokoro-onnx==0.6.1` 上游项目许可为 MIT；本轮中文语音使用的 Kokoro v1.0 模型/voices 文件另受上游 Apache 2.0 声明约束，应随资产保留其许可和出处。 | 仓库不提交模型权重；下载、归档或再分发时保存对应许可及来源。上游 [runtime 许可](https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE) 和 [项目说明](https://github.com/thewh1teagle/kokoro-onnx)。 |
| DeepSeek API | G4 将源文发给 DeepSeek Open Platform。条款要求提交者有权提供输入；条款说明输入权益仍归提交者、输出权益按条款转让给用户，同时对发布内容要求核验并清楚披露 AI 生成。 | G3 界面已说明源文出站、生成内容可能错误/遗漏、需人工核验及用户须有权提交。生产使用前还应按实际数据类型、地区及 DeepSeek API 适用的隐私条款复核。官方 [Open Platform Terms](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)。 |
| H.264 / AAC | 交付 MP4 使用 H.264/AAC。软件许可证与标准必要专利许可是不同问题；具体责任受用途、分发方式和地区影响。 | 本轮只验证技术输出，不确认专利许可已满足。产品发布前请按目标市场另行确认。FFmpeg 的 [许可说明](https://github.com/FFmpeg/FFmpeg/blob/master/LICENSE.md) 也提示使用者负责适用法律遵循。 |

## 已知缺口

- 当前锁定依赖的核对是高风险组件级检查，不是完整、自动生成的 SPDX/CycloneDX SBOM；npm 包中 Remotion 平台适配包的元数据也有若干未声明 `license` 字段。
- 当前仓库安装的是开发依赖，未创建可签名/可向用户分发的安装器或服务镜像，也没有把 FFmpeg、模型权重、音色文件或 `.env` 放进发布包。
- `espeakng-loader` wheel 虽有 MIT 上游项目标记，但 wheel metadata 未含许可证信息，且 wheel 实际携带 GPLv3 eSpeak 运行库和数据。必须把这两层分别纳入通知和分发审查。
- DeepSeek 生成结果的权利归属受其现行服务条款限制，不能据此保证输出独占或不与他人相似；输入也不应包含无权外发的内容。

结论：本轮研发/单机样片的依赖来源已记录；商业分发授权与专利判断未完成。该事项列为产品发布前置问题，不改变三条 G4 技术样片的 QC 结果。
