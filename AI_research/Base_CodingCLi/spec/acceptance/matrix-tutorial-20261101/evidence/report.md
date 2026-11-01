# MarkItDown 实操：把项目周报转成 Markdown

## 本期任务
这是一次实际完成的本地工具操作，原稿和转换结果是本期确定输入。
为 AI 开源发现栏目制作一条清楚的工具教程，围绕同一份项目周报按准备输入、执行转换、查看结果三个阶段讲解。
三个阶段各自展开成内容章节，保留一个简短开场和收尾。具体任务足够讲清即可，不延长成项目百科。
展示原文中的实际命令、文件名和输出，让观众知道每步做什么、结果是什么；不念网页地址或代码标点。

## 工具与环境
本次实际使用 Microsoft MarkItDown 0.1.9，将本机 HTML 转成 Markdown。工具已安装在临时 Python 环境；HTML 转换没有调用大模型。
项目和命令用法：https://github.com/microsoft/markitdown

## 准备输入：weekly.html
在本地准备一份项目周报网页，打开它可以看到标题、已完成列表、下一步任务表。
标题是“项目周报”，已完成项是“整理项目资料”和“完成脚本联调”。下一步表格有任务和状态两列：转换文档进行中，整理结果待开始。
以下是本次实际输入文件，供作者理解内容；无需逐字朗读 HTML：

```html
<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>项目周报</title><style>
body{margin:56px;color:#20372d;background:#fff;font:32px/1.5 "Hiragino Sans GB",sans-serif}
h1{font-size:62px;margin:0 0 24px;color:#17784b}h2{font-size:38px;margin:20px 0 12px}
ul{margin:0 0 28px;padding-left:42px}table{border-collapse:collapse;width:100%}
th,td{border:2px solid #cbd8ce;padding:14px 24px;text-align:left}th{background:#edf4ee}
</style><body><header id="summary"><h1>项目周报</h1><h2>已完成</h2>
<ul><li>整理项目资料</li><li>完成脚本联调</li></ul></header>
<section id="tasks"><h2>下一步</h2><table><thead><tr><th>任务</th><th>状态</th></tr></thead>
<tbody><tr><td>转换文档</td><td>进行中</td></tr><tr><td>整理结果</td><td>待开始</td></tr></tbody></table></section>
</body></html>
```

## 执行转换
先安装工具，展示实际命令：`pip install markitdown`。
然后在 weekly.html 所在文件夹执行：`markitdown weekly.html -o weekly.md`。
weekly.html 是输入文件，-o 后的 weekly.md 指定输出文件。执行后，输出保存在同一文件夹。
安装与转换是连续两步，展示转换命令时保留安装命令的上下文。

## 查看结果：weekly.md
下面是本次命令实际生成的输出，保留原文，展示标题、列表与表格的对应变化：

```markdown
# 项目周报

## 已完成

* 整理项目资料
* 完成脚本联调

## 下一步

| 任务 | 状态 |
| --- | --- |
| 转换文档 | 进行中 |
| 整理结果 | 待开始 |
```

网页一级标题变成 Markdown 的一级标题，已完成变成二级标题，列表仍是两项；下一步任务和状态保留为 Markdown 表格。
用同一份输入和输出对照说明结果。本次只演示了本地 HTML 文件；不扩展其他格式或复杂依赖安装。

## 收尾
回到准备 weekly.html、运行转换命令、得到 weekly.md 的同一任务。现在有了一份可以继续编辑、放进笔记或交给后续文本流程的 Markdown 文件。

## 参考来源
1. MarkItDown 官方项目与命令用法 | 一手 | 2026-11-01 | https://github.com/microsoft/markitdown
