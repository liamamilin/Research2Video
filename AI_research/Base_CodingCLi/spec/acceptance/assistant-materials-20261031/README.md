# 助手材料创作与采集覆盖验收

实现说明：[assistant-materials.md](../../assistant-materials.md)。现有助手接收链接、截图、长正文和文档，创建独立文稿并接入持久视频流程；文稿页继续管理产物。

## 验证

- `bash scripts/check.sh`：后端 **3966 passed, 2 skipped**；前端 **90 files / 718 tests passed**；配置校验、类型检查、Lint、构建通过。日志：`/tmp/assistant-materials-full-check.log`。
- 收尾变更（采用记录查询、画面名称与图片尺寸兼容）专项回归：**53 passed**。日志：`/tmp/material-mechanism-regression.log`。`git diff --check` 通过。
- 浏览器在独立临时账号和数据库中粘贴 **12250 字符**正文；正文自动成为材料，真实助手 `read_materials` 返回完整内容，刷新后附件与回执仍在。见 [入口截图](assistant-input.png)、[恢复截图](assistant-recovered.png)。未新增文稿页输入流程。
- 四个真实价格页通过同一个 `read_page` 路径成功取得内容：Claude 套餐 17 节、Claude API 35 节、Gemini API 80 节、Cursor 15 节。见 [读取记录](source-readers.json)。这是实际读取证据，不代表已有历史报告采用这些内容。
- 正式 Web 新实例的材料上传、附件请求、覆盖状态、采用记录接口已核验存在，见 [接口核验](live-interfaces.json)。本次重载等待曾因既有视频批次暂缓，随后已撤回该等待；未取消既有任务。

## 真实成片

使用用户提供的套餐截图和一段关于付款周期的完整旁白，在隔离状态目录创建文稿并交给原有 workflow。写稿、自动定稿、原图登记与段落配图、视频引擎制作、下载成片均完成；批次最终为 DONE。

- 脚本：`2619839baf3b4eb6b4098078e2ec3da9`；引擎任务：`19840092-7b67-4528-abfa-4c5ec8393f3e`。
- 成片：`state/acceptance/assistant-materials-20261031/video.mp4`（忽略的运行产物），19.833 秒，1920×1080，H.264 + AAC。
- 全片音视频解码通过，FFmpeg `-xerror` 无错误；引擎包装 QA 为 PASS。见 [媒体探测](media-probe.json)、[包装 QA](package-qa.json)、[段落配图](slideshow-images.json)、[状态记录](real-run.json)。
- [画面截图](video-frame.png) 为首轮成片；发现文件名进入画面标签后，配图工具已改为生成面向观众的画面名称，该收尾行为由专项回归覆盖。

本次未修改渲染器，也未改变既有报告、文稿或已完成视频。覆盖与采用状态从后续采集/研究开始累积，未补写历史基线或伪造历史采用记录。

## 三项机制的边界回归

第二轮按来源生命周期、版本化创作上下文、后台执行与失败隔离三个机制修复；没有增加按产品单独处理的逻辑。

| 原缺口 | 修复与边界证据 |
| --- | --- |
| 配图调用阻塞调度，坏结果静默缓存 | 配图在持久 worker 内执行；非法坐标不缓存；空匹配需要原因；失败进入有限重试；其他任务/批次继续 |
| 后续交付覆盖真正采用的报告链接 | 逐报告追加历史；采用 A 后向 B 交付或作背景仍保留 A；重复提交不增加历史次数 |
| 旧状态版本再次重供 | 实时观察即标记 superseded，不等待报告保存；完整读取后消失的条目、旧格式待用条目同样退出 |
| 旧缓存伪装实时读取成功 | 分别记录 live/cache；空结果和失败保留最后成功时间，覆盖显示缓存/不可用 |
| 未发送材料丢失或串到其他对话 | 按账号与对话暂存；恢复、切换、迟到响应、长文失败和文件重试均覆盖 |
| 补充研究遗漏完整要求 | 研究与写稿接收同一要求；变更范围重跑研究，换风格复用 |
| 原任务无法续改，无报告无法换风格 | 同文稿追加/替换材料与要求；修订冲突、账号隔离、旧版冻结；页面和助手均可换风格；回执指向新视频版本 |
| 已有来源不能补新目标 | 来源身份与分配分离；复用近期样本；分配并集；满池仍可更新原来源 |

后端边界用例在 `tests/test_material_mechanisms.py`（24 项），前端在 `ui/src/lib/assistant-draft.test.tsx`、`Ask.test.tsx`、`VideoScriptView.test.tsx`。另修正既有任务回执测试的后台线程隔离，避免用例结束后启动真实研究。边界与隔离专项合计 25 项通过。

2026-11-01 最终执行 `bash scripts/check.sh`：后端 **3997 passed, 2 skipped**；前端 **91 files / 728 tests passed**；编译、配置校验、类型检查、Lint、构建全部通过，检查进程正常退出（代码 0），`git diff --check` 通过。完整日志：`/tmp/material-mechanisms-complete-check.log`。见 [最终检查结果](mechanisms-checks.json)。前两轮受中断或遗留测试线程影响的记录不作为最终完整检查结果。

已通过现有受控维护流程重载正式 Web，健康状态正常，新 `PATCH /api/video/scripts/{script_id}/materials` 接口及必填修订号已核验生效。没有取消既有任务；遇到自动批次运行时首次尝试未执行重启，待批次结束后才交接。见 [上线接口核验](mechanisms-live-interfaces.json)。

真实浏览器另外验证了 IndexedDB 文件恢复：模拟上传返回 503，刷新后重试；回传文件与用户原图逐字节一致。未发送要求也保留，新对话没有旧附件，回到原对话恢复原草稿。使用临时账号、数据库与回环端口，未执行生产任务。见 [浏览器结果](mechanisms-draft-browser.json)、[恢复后的草稿](mechanisms-draft-browser.png)。

### 更新原任务后的真实成片

使用同一份用户套餐截图和短旁白，先生成 v1，再在原任务修改要求并交给视频 workflow。最终 v2 定稿、用户原图段落匹配、引擎制作、下载均完成，批次为 DONE；v1 的内容与材料修订保持不变。中途验收进程被打断后，沿用已有引擎任务接续回传，没有重复生成。

- 文稿：`d203f9760e3d4c33b606aba79a43670e`；引擎任务：`5f2f3854-95cb-4042-9147-ad9911d39904`。
- 视频：`state/acceptance/assistant-materials-20261031/mechanisms-video.mp4`（忽略的运行产物），15.3 秒、1920×1080、H.264 + AAC。
- 全片音视频解码退出码 0；包装 QA 为 PASS。原图出现在套餐段落，标签为“Claude 订阅套餐价格对比图”，没有使用上传文件名作为画面名称。
- 证据：[任务状态](mechanisms-real-run.json)、[媒体探测](mechanisms-media-probe.json)、[解码](mechanisms-decode.txt)、[包装 QA](mechanisms-package-qa.json)、[配图](mechanisms-slideshow-images.json)、[帧截图](mechanisms-video-frame.png)。
