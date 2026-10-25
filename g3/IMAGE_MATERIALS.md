# 第一批：文稿与已有图片制作

2026-10-23 用户确认：上游以文稿和可取得图片为主，界面演示／录屏后置。本轮接入 F04 对象展示、F13 动态拼贴和无词配乐推荐／复用。完整分阶段顺序记录在 [项目计划](../.goal-anchor/PLAN.md)。

## 使用和边界

新建页的“配图素材”沿用 PNG/JPEG/WebP 导入、顺序调整、完整性检查和任务副本。可填写图片说明（最多80字）和“参考／整体／细节／对比”用途，也可留空。图片随原浏览器草稿保存，切换形式不清除。

- 对象展示：主图、相邻图片缩略栏、提交者提供的说明／用途和实际旁白要点。
- 动态拼贴：同时展示最多三张图片，以多层纸片版面、说明标签和逐句旁白卡组合。
- 两种形式均需1–12张已有图片，不隐式生成替代图片；只有一张图也可制作，但不产生虚构的细节或对比图。
- 不做像素内容识别。图片按提交顺序分配；完整连续的“第一张、第二张……”讲解优先对应图片。上游／用户须提供正确顺序和说明，不能声称自动完成语义匹配。

“按当前素材推荐”根据图片数量、用途标注、题材配方和音乐标签提供建议。纯文稿推荐原形式；普通图片推荐拼贴／轮播；有整体、细节或对比标注时增加对象展示。仅点击按钮才修改形式或开启配乐，后台刷新不覆盖手动选择。三份v5配方增加两种新形式入口，原默认形式和默认关闭配乐不变。

无词配乐建议从可用内置器乐素材、带instrumental生成条件记录的AI资产中选择。未标注导入音频不假定为无词，仍可手动选用。推荐不创建音乐任务；需要时沿用原AI生成入口。生成条件不代表人工听审，中文歌曲不纳入接入计划。

## 上游接口

上游先调用原 POST /api/creative-images（multipart file）登记已取得图片，再提交注册UUID。AutoVideo不下载任意远程图片网址。

POST /api/jobs 新增 video_form: object_showcase / dynamic_collage，支持素材模式及 input_mode:narration、内容／指定时长、原有配方及背景配乐。图片字段示例：

```json
{
  "video_form": "object_showcase",
  "image_materials": [
    {"image_id": "已登记UUID", "caption": "整体外观", "role": "overall"},
    {"image_id": "另一已登记UUID", "caption": "已有细节图片", "role": "detail"}
  ]
}
```

image_materials最多12项，编号唯一，role为reference／overall／detail／comparison。也可沿用slideshow_images仅传编号；同时提供时编号和顺序须完全一致。缺图、非法图片、快速卡片档位或自动背景图冲突在创建前拒绝。原图片轮播请求兼容。

POST /api/creative-recommendations接受source_text（可空）、可选production_recipe和image_materials，返回forms／music建议、解释及automatic_changes:false。只做推荐，不创建任务或调用模型。最终video_form和background_music仍须调用方明确提交。

## 保存、修订和验收

两种新形式复用最终讲稿、实测旁白时间线、包装和独立混音。图片文件／哈希、说明、用途和顺序随任务保存并绑定制作计划；旧计划结构不变。重试、原稿／讲稿、声音／倍速、配乐修订继承父任务图片副本，不依赖全局导入缓存。新形式通过讲稿修订调整文字，原自由重排范围不扩展。

可信组件object_showcase／material_collage使用独立扩展schema，检查图片引用、索引和旁白逐字一致性。冻结的原Video IR schema不修改。

真实MP4、完整解码、旁白／混音、素材哈希和浏览器证据见 [本轮验收](acceptance/image-materials-20261023/README.md)。音乐短句／转场、录屏、人物与故事类生成不属于本轮交付。
