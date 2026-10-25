# 第一批：文稿与已有图片制作

2026-10-23 用户确认：上游以文稿和可取得图片为主，界面演示／录屏后置。本轮接入 F04 对象展示、F13 动态拼贴和无词配乐推荐／复用。完整分阶段顺序记录在 [项目计划](../.goal-anchor/PLAN.md)。

## 使用和边界

新建页的“配图素材”沿用 PNG/JPEG/WebP 导入、顺序调整、完整性检查和任务副本。可填写图片说明（最多80字）和“参考／整体／细节／对比”用途，也可留空。图片随原浏览器草稿保存，切换形式不清除。

- 对象展示：主图、相邻图片缩略栏、提交者提供的说明／用途和实际旁白要点。
- 动态拼贴：两张图片等宽并列对照，三张图片用多层纸片版面、说明标签和逐句旁白卡组合。
- 两种形式均需1–12张已有图片，不隐式生成替代图片；只有一张图也可制作，但不产生虚构的细节或对比图。
- 不做像素内容识别。未指定段落时按提交顺序分配；完整连续的“第一张、第二张……”讲解优先对应图片。
  分章讲稿现在可明确指定段落位置（见下文），上游／用户负责选择正确画面，不能声称自动完成语义匹配。

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

### 明确对应讲稿段落（2026-10-25）

`input_mode:narration` 可为每项提供非空 `placements`：

```json
{
  "image_id": "已登记UUID",
  "caption": "官方价格页，具体费用以官网当时说明为准",
  "role": "comparison",
  "placements": [{"chapter_index": 1, "paragraph_index": 0}]
}
```

章节和段落索引从0开始，同一张图可用于多个段落。只要有一张图使用位置，所有图片都必须提供位置；
不接受不存在或重复的位置，不允许用于会重写段落的素材模式。位置经原稿分句器确定到每条旁白，
只在对应段落显示该段图片。没有配图的段落使用已有可信 `kinetic_text` 组件。
这扩展运行时允许的组件组合，不改变冻结 Video IR schema，也不加入像素识别或模型语义匹配。

截图可额外携带 `source:{url,final_url,title,captured_at,sha256}`，其中哈希为上游原始截图哈希。
来源字段随制作计划保存，仅作追溯，AutoVideo 不访问这些网址。登记时规范化后的图片仍按引擎自己的哈希验证。
原来的无位置请求和已冻结任务保持原顺序分配行为。

### 页面聚焦与标记（2026-10-25）

每项 `image_materials` 可另带可选 `focus`，不改变原 UUID、来源和 `placements` 含义：

```json
{"focus":{"width":1440,"height":900,"regions":[
  {"x1":220,"y1":300,"x2":800,"y2":430,"label":"页面中的实际说明","style":"box"}
]}}
```

坐标相对**实际登记图片**归一化为0–1000，矩形宽高为正，最多3处，说明单行最多32字。
尺寸必须等于登记 PNG 的尺寸。`style` 允许 `box/circle/underline/highlight`；引擎不执行外部脚本或任意组件。
可信组件先展示截图整体，再平移／放大到目标，按帧进度绘制 SVG 标记，并显示提交的重点说明。
长场景可连续聚焦，短场景按每处至少约3秒减少焦点。双图并列共用同一套坐标变换，旧的无坐标素材保持原画面。
本轮沿用已安装的 Remotion 4.0.535，没有升级依赖或安装 `@remotion/rough-notation`。

研映研究侧新增来源定位、OCR 和显式配置的 Qwen3-VL 图片理解步骤；它决定是否使用画面并提交明确位置。
引擎本身仍只执行调用方选择，不声称它的旧顺序分配具有语义理解。MP4 输入镜头仍属后续独立路线。

带位置任务的讲稿／原稿修订返回 `IMAGE_MAPPING_REQUIRES_REVIEW`，请上游在新文稿版本重新确认位置并创建新任务；
声音、倍速和配乐修订可继续复用已冻结图片与画面。研究侧完整流程见
[网页截图与段落配图](../../AI_research/Base_CodingCLi/spec/video-screenshots.md)。

POST /api/creative-recommendations接受source_text（可空）、可选production_recipe和image_materials，返回forms／music建议、解释及automatic_changes:false。只做推荐，不创建任务或调用模型。最终video_form和background_music仍须调用方明确提交。

## 保存、修订和验收

两种新形式复用最终讲稿、实测旁白时间线、包装和独立混音。图片文件／哈希、说明、用途和顺序随任务保存并绑定制作计划；旧计划结构不变。重试、原稿／讲稿、声音／倍速、配乐修订继承父任务图片副本，不依赖全局导入缓存。新形式通过讲稿修订调整文字，原自由重排范围不扩展。

可信组件object_showcase／material_collage使用独立扩展schema，检查图片引用、索引和旁白逐字一致性。冻结的原Video IR schema不修改。

真实MP4、完整解码、旁白／混音、素材哈希和浏览器证据见 [本轮验收](acceptance/image-materials-20261023/README.md)。音乐短句／转场、录屏、人物与故事类生成不属于本轮交付。
