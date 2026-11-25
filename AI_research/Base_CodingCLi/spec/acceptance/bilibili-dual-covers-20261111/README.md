# B站双比例封面接入

## 目标与入口

- 真实作品：《我还在优化》[BV1cNW26fEwZ](https://www.bilibili.com/video/BV1cNW26fEwZ/)。问题发生于投稿封面：此前只上传 16:9，4:3 使用平台自动裁切，未采用已独立构图的图片；不修改视频时间轴。
- 正常入口：本地视频导入 / B站发布页 → 现有原生投稿队列。输入为已完成视频配套的两张封面，不增加栏目、定时任务或素材系统。
- 结果：4:3 与 16:9 可独立登记、预览、上传；旧稿可经 workflow 换封面，不重新上传视频。全程未操控浏览器。
- 新图来源：用户已认可的作品配套文件 `cover-bilibili-4x3.png`（1440×1080）、`cover-bilibili-16x9.png`（1920×1080），本轮未生成或另行裁切。

## 已接入能力

- 导入入口增加 `covers`：`4x3` / `16x9` 各自对应一个本机绝对路径；已存在稿件时引导使用该任务的封面更新入口。
- 发布表单可各自选择 PNG/JPEG 并预览；旧投稿卡片新增“修改双比例封面”。图像按比例检查并登记内容哈希文件名，已排队任务的文件不因后续换图而改变。
- 16:9 → CLI `--cover`；4:3 → 图片上传接口 → CLI `--extra-fields {"cover43": "URL"}`，走现有去重、上传、审核状态和首评流程。
- 旧稿更新保存原稿快照，上传两图并编辑同一 BV，分别回读两种比例。请求不确定时保存回执，不误报成功；回读已对应则重复调用不再编辑。
- 当前后端已在任务空闲、持有既有维护锁时优雅重载，健康检查 `ok`，四个新接口已在实际服务 OpenAPI 中确认；日常网关 8775 健康。见 [deployment.json](deployment.json)。
- 音乐视频 skill 的投稿说明及封面说明已更新，移除浏览器补封面步骤，保留作品授权与已有队列。

接口用法见 [平台管理文档](../../platform-management.md#双比例封面2026-11-11)。

## 真实旧稿验证

原任务 `464dc86a9e634fd793558e5677bf54c3`，原 render `20d0f031c4254432b43d6c7b15c95846`。调用与 HTTP 路由共用的 `bilibili_covers.update_published` 服务函数，使用当前已连接账号；未制造登录会话、未通过浏览器编辑、未重投视频或发送评论。

[real-receipt.json](real-receipt.json) 状态为 `CONFIRMED`：

- 16:9 采用 `1d23e85fcdbf08e8b80e71173aeb4b37d8693d8f.png`。
- 4:3 采用 `34454b38b4f3d086447d9b1efe8661978199d01a.png`。
- 平台回读换了 CDN 主机，但两个文件的 URL 路径分别一致。

[metadata-check.json](metadata-check.json) 对比更新前快照：标题、简介、标签、分区、版权和转载设置、内容标注、动态等无变化；分 P 的 CID、filename、标题、简介未变；评论、字幕、水印设置未变。原视频仍“开放浏览”，项目任务仍 PUBLISHED，原首评仍 SENT / PINNED。

交付目录的 `bilibili-publication.json` 已同步封面完成状态，保留修改前记录及独立封面回执。

## 接口格式纠偏

首次直接回传创作中心展示对象报 21001；改为编辑字段映射后，仍因将 `creation_statement` 压成整数被拒绝。最终保持 `creation_statement` 对象，将 `human_type2.id`、`recreate.switch` 转为整数，水印保留 state，字幕 allow 转 open，编辑成功。此前失败均为参数明确拒绝，未产生新稿或替换视频。

源码依据：[biliup Studio/编辑实现](https://github.com/biliup/biliup/blob/master/crates/biliup/src/uploader/bilibili.rs)、[biliAPI Web 编辑与水印处理](https://github.com/renmu123/biliAPI/blob/master/src/platform/index.ts)。格式最终以本次真实接口成功及回读为依据；不在 skill 内重写这些协议。

## 验证与边界

- 最终相关后端回归：130 passed，包含本地导入到队列、双图独立文件、CLI 参数、封面失败不提交视频、旧稿元数据与 CID 保留、回读失败后的收据与重复调用。
- 前端全量：95 文件 / 749 tests passed；最后调整请求超时后，相关 17 tests 通过。TypeScript、ESLint、生产构建均通过。
- 配置校验、skill 校验、`git diff --check` 通过。
- 完整后端首轮：4353 passed / 22 failed / 8 errors / 3 skipped；失败为沙箱对本机端口、进程及系统编译操作的限制。在允许这些操作的环境重跑后：4384 passed / 2 skipped，耗时 488.81 秒；最终新增的回读失败用例包含在随后 130 项相关回归中。保留 `check.log`、`check-retry.log`、`focused-tests.log`。
- 新稿的双比例提交已通过隔离 API → 队列 → 原生 CLI 验证；没有为验收重复公开投稿。真实平台验证针对已有 MV 的两种封面上传、编辑与回读，不能把它写成一条全新公开投稿的验证。
- 本轮验证的是图片分别被平台采用及生产接口可用；未操作浏览器检查最终信息流展示，不声称点击率或视频观看效果提升。
