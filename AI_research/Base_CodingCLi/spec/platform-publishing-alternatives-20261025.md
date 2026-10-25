# 快手投稿替代方案源码调研

调研日期：2026-10-25。检查了项目 README、实际投稿代码与快手官方文档；本轮没有安装、执行候选项目，没有上传素材或发送发布请求。

## 当前任务的实际情况

- 快手旧任务 `110310c21d644efdb3041e5b727f4def` 仍为 UNKNOWN，没有作品 ID；状态更新时间没有变化，后续准备验证未重新执行这条任务。
- 小红书旧任务 `34b993b4567c4d1eb65da30ba9043df9` 也仍为 UNKNOWN，没有作品 ID。此前用户确认两个平台没有此作品；本轮截图和再次检查针对快手。
- 上轮快手准备验证完成了素材上传和表单填写，最终 1 次投稿请求被诊断保护截断，没有发送到快手。网页没有新增作品不能被当作一次“修复后正式投稿失败”的证据；正式投稿尚未执行。
- 旧任务的原始响应没有保存，无法补造它的具体平台拒绝原因。已修复的入口、控件和地址问题来自可重复的准备诊断。

验收证据：[准备诊断](acceptance/platform-expansion/kuaishou-upload-diagnostic-20261025.json)、[排查记录](acceptance/platform-expansion/pending-upload-investigation-20261025.json)。

## 候选比较

下列 push 日期来自调研时 GitHub 仓库元数据，代表仓库活动，不代表快手模块最近验证时间。候选均未在当前账号实机投稿，不能把 README 的支持标记视为已验收。

| 项目 | 路线与可复用部分 | 实际结果判定及缺口 | 评估 |
| --- | --- | --- | --- |
| [AiToEarn](https://github.com/yikart/AiToEarn) | TypeScript/Electron；直接调用创作中心接口；分片、封面、投稿、作品列表回查 | 投稿检查 `result`，再以本次封面键关联待发布列表；返回 `publishId`，分享链接为空。当前源码仍用旧 `/pc/submit`，须核验当前账号的新 `/pc/isolation/submit` | 第一优先的独立替代实现参考；有明确提交与回查步骤，需适配和实投验证 |
| [Fuploader](https://github.com/fishimei/Fuploader) | Go/Wails + Playwright；独立桌面工具，快手上传与表单操作 | 以跳转管理页或可见成功文案为成功，发布函数还有循环点击；没有在此函数返回公开作品 ID | 可参考上传定位，不能直接承担本系统的完整回执与防重职责 |
| [MultiPost Extension](https://github.com/leaperone/MultiPost-Extension) | 浏览器扩展；在已登录页面注入文件、正文、封面，可选自动点击发布 | 快手函数在点击后结束，没有该函数内部的结构化投稿回执与作品回查；开源部分不包含官方发行包的私有 Agent 模块 | 更适合人工可见的浏览器准备与发布入口；需自行补后台队列与核对 |
| [auto-upload](https://github.com/s840207702/auto-upload) | Python/Playwright；上传完成等待、话题、封面、定时交互 | 快手仍靠页面跳转判断；NOTICE 明确源于 social-auto-upload 的分支等工作 | 属于同一路线的改进版，适合复用局部交互，不能仅靠替换就获得可靠回执 |
| [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) | 小红书独立 Go/Rod + HTTP/MCP 服务；视频上传及平台专用逻辑 | 等待离开发布页确认流程结束；视频响应没有作品 ID。成功跳转也不能代替本系统的作品归属、审核和公开核对 | 适合小红书的独立候选；不解决快手问题，仍需接结构化回执 |

许可证和活动记录：AiToEarn 为 MIT，仓库最近 push 2026-10-20；Fuploader 为 MIT，2026-02-13；MultiPost 为 Apache-2.0，2026-10-24；auto-upload 为 Apache-2.0，2026-05-17；xiaohongshu-mcp 为 Apache-2.0，2026-10-19。这里只记录源码的许可证声明；实际复用时保留对应许可证和归属说明。

## 源码依据

- [AiToEarn 快手发布与回查](https://github.com/yikart/AiToEarn/blob/main/project/aitoearn-electron/electron/plat/Kwai/index.ts)：`pubVideo` 完成分片和封面后调用 `/rest/cp/works/v2/video/pc/submit`，检查 `result`，调用 `getWorks` 并关联封面键；该流程返回的是待发布记录标识。`getWorks` 可以作为只读回查的参考，不能把查询参数或状态枚举未经当前平台核验直接用于自动公开确认。
- [AiToEarn 数据类型](https://github.com/yikart/AiToEarn/blob/main/project/aitoearn-electron/electron/plat/Kwai/kwai.type.ts)：上传素材、待发布记录与公开作品是不同的数据结构。
- [Fuploader 发布函数](https://github.com/fishimei/Fuploader/blob/main/internal/platform/kuaishou/publish.go)：循环尝试点击，并按管理页 URL 或可见成功文案返回。分析：对于结果不明的非幂等发布，直接继承其重复点击逻辑会增加重复提交风险。
- [MultiPost 快手函数](https://github.com/leaperone/MultiPost-Extension/blob/main/src/sync/video/kuaishou.ts)：素材通过页面 File/Input 事件交给平台上传，最后根据 `isAutoPublish` 点击自定义发布控件。
- [MultiPost 开源范围](https://github.com/leaperone/MultiPost-Extension/blob/main/README.md)：开源仓库与包含私有 Agent 的官方扩展包范围不同。
- [auto-upload 快手上传器](https://github.com/s840207702/auto-upload/blob/main/uploader/ks_uploader/main.py)、[来源 NOTICE](https://github.com/s840207702/auto-upload/blob/main/NOTICE)。
- [小红书视频发布](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/publish_video.go)、[成功跳转判定](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/publish.go)、[服务响应结构](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/service.go)。

## 官方 API 是另一条独立路线

快手官方文档明确提供“发起上传 → 上传 → 发布”，发布结果包含作品标识；发布为异步过程，随后仍需查询作品确认完成。[创建视频](https://open.kuaishou.com/platformDocs/openAbility/contentManagement/createAVideo.html)

查询视频列表和单条视频需要 `user_video_info`；投稿需要 `user_video_publish`。接入需要开放平台网站应用、应用凭证、回调及用户 OAuth 授权，现有创作中心浏览器登录态不能代替。[查询视频](https://open.kuaishou.com/platformDocs/openAbility/contentManagement/searchForVideos.html)、[网站应用接入](https://open.kuaishou.com/platformDocs/develop/web-app)

个人主体准入补充：2026-03-06 更新的[开发者入驻说明](https://open.kuaishou.com/platformDocs/newGuide/entry)要求填写企业、法人及管理员信息；[应用注册说明](https://open.kuaishou.com/platformDocs/newGuide/login)要求完成入驻和应用审核后申请接口权限。虽然[开发者协议](https://open.kuaishou.com/platform/protocol)的定义包括个人，本轮未找到明确允许纯个人主体申请视频发布、查询权限的官方说明，因此不能按“有个人快手账号即可开通官方 API”规划。开放平台开发者主体准入与内容账号授权是不同层次；官方路线仍需先核实实际主体及能力准入。

评估：如果能够申请到这些能力，官方 API 最适合作为长期后端；如果暂时没有，则可先对 AiToEarn 的本地接口方案做独立适配和小范围实投，不必迁入其整套产品或云端服务。此项是方案判断，不是对当前账号权限或候选成功率的保证。

## 建议执行顺序

1. 保留现有文稿、账号隔离、平台任务和已发布记录。将投稿引擎与业务记录解耦，快手可验证另一实现，B 站和抖音继续原实现。
2. 先验证 AiToEarn 的提交和作品回查部分，按当前创作页核对接口差异；明确区分素材 ID、待发布记录 ID 与公开作品 ID。复用时删除包含凭证或完整请求体的上游日志。
3. 处理旧 UNKNOWN 任务时记录人工核对结论和原始尝试，不直接清空防重信息。用户已确认两边没有作品；本轮没有因此修改任务或重新投稿。
4. 正式验收以一次真实提交、持久化脱敏回执、准确作品 ID、当前账号归属、管理页作品出现为准；审核中与已公开分别展示。只有明确拒绝或人工确认未提交后才允许人工重试。
5. 有开放平台权限后增加官方 API 引擎，再逐步替换网页依赖。小红书可独立验证其 MCP 引擎，不强制所有平台使用同一上传工具。

本轮结论：有可复用的替代项目，但没有经过当前账号验证的即插即用替代品。优先验证 AiToEarn 的接口与作品回查；同时评估官方 API 接入条件。换成另一个点击脚本仍需要完成真实回执和作品核对。

## 实际投稿补充：机器人视频

用户随后通过现有入口发布了《机器人砍掉了小拇指：这周，技术落地开始寄账单》，保存的实际投稿响应为 HTTP 200、`result=1`、`data={}`。用户在快手确认作品出现，并提供准确作品链接，作品 ID 为 `3x432buhnb5ryma`。已恢复已接受回执并将链接补录到文稿 v1，来源保留为用户确认；没有再次投稿，也未把该记录标作平台自动公开核验。

因此现有快手上传链路已有实际投稿成功案例，当前直接问题是接受回执缺少作品 ID。先修复状态恢复及链接关联，再单独验收作品归属和公开状态回查；无需仅因旧 UNKNOWN 提示立即替换上传引擎。模型榜单的两条旧任务是不同文稿，仍保留原记录；小红书真实投稿仍待验收。详见 `acceptance/platform-expansion/kuaishou-robot-publication-20261025.json`。
