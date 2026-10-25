# 文稿与图片第一批验收 · 2026-10-23

## 已完成

F04 对象展示、F13 动态拼贴接入原自动制作链路、创作资源库和v5三份题材配方。新增图片说明／用途、基于实际素材的形式建议和无词音乐复用建议。上游复用图片导入接口并提交注册图片，可直接用于生成，不要求录屏／出镜。默认形式、默认关闭配乐、手动设置、旧快照和历史版本保持兼容。中文歌曲不接入。

对象展示为主图＋缩略栏＋说明／旁白；拼贴为多层图片＋原文卡，当前讲解的图片进入前景。三张输入是本轮原创程序绘制的流程示意（fixtures/），明确不是产品截图／模型生成图片；两种主形式分别覆盖定稿讲稿和素材自动组织。另验收1.5倍速、复用AI音乐的换曲、单张图片、80字说明和三图焦点切换。图片按提交顺序或完整显式编号对应，不宣称像素识别或自动语义理解。

## 实际 MP4

| 任务 | 形式 | 操作 | 秒 | 全解码／媒体QA |
| --- | --- | --- | --- | --- |
| 1a43e8fa-3b3c-4559-aba4-fe48fee3b2d3 | object_showcase | generate | 23.033333 | PASS |
| ccbb91b0-808b-447c-8b44-171bdaff064d | dynamic_collage | generate | 60.3 | PASS |
| 268a3e75-c8ac-41d4-aae5-f8bfc1070977 | object_showcase | media_revision | 16.533333 | PASS |
| 3bf58b29-665e-4e7f-8c97-ac7e01dc34eb | dynamic_collage | music_revision | 60.3 | PASS |
| c9ffd874-b2b0-49c5-a8b6-571afe2287ba | dynamic_collage | generate | 9.941 | PASS |
| 4a17b183-c540-43f9-aed7-793d6b429314 | dynamic_collage | generate | 12.971 | PASS |

每条任务保留原始请求、输入副本、尝试日志和真实MP4，任务编号可在本机作品库查询。real-jobs.json记录请求／响应；media-verification.json记录视频哈希、图片／制作计划校验、媒体检查、混音来源及修订复用。

## 检查结果

- 六条真实视频全部通过完整解码和媒体QA；四条启用包装的视频也通过包装QA。504份清单条目哈希匹配。
- 三条混音视频保留清晰旁白，旁白与配乐能量差约24dB，恢复旁白相关系数均大于0.99995；混音视频流与参考未混音视频一致。
- 动态拼贴换曲复用原Video IR和未混音视频，TTS／渲染跳过；对象展示1.5倍速保留图片说明／用途及配方／配乐。
- 174条原制作记录的制品登记哈希全部一致，2份代表旧视频实际文件哈希一致。新增6条验收记录后为180条。
- 推荐没有创建任何音乐任务：原2条音乐任务保持2条，已有内置无词素材和AI生成电子配乐均完成复用。
- 前端构建、TypeScript和git diff --check通过。浏览器验证配方带入新形式、缺图阻止创建、纯文稿建议、建议不覆盖手动选择、无词音乐点击应用、刷新恢复和真实视频播放。未通过浏览器文件选择器上传个人文件；图片导入通过真实HTTP接口验证。

## 共享工作区的回归边界

本批功能曾在完整运行回归中达到617 passed、1 skipped。随后另一活跃会话同时将information_cards规划改为card_editorial，最新完整回归为615 passed、2 failed、1 skipped（regression-release.txt）：两项旧通用测试仍以pipeline=None调用新卡片规划器，报AttributeError。相关实现正在该会话修改，本轮保留其改动，不以回退或绕过来隐藏失败。

本批图片形式／配方／计划／API的独立兼容检查70 passed（focused-release.txt），明确排除上述information_cards两项；不将它称为完整回归通过。旧冻结设计包仍为AGENTS.md字节清单不一致（design-snapshot.txt），没有改写冻结清单。

## 页面证据和限制

object-browser.png：真实成片播放。object-frame.png、collage-frame.png：两种基本版式；single-caption-frame.png、collage-focus-frame.png：长说明和焦点前景。

模型生成、音乐性和题材匹配仍需人工观看／试听；本轮不新增音乐生成调用，不扩展歌曲、录屏、人物／连续故事、自由多轨或图片语义识别。
