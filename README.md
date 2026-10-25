# 研映 · Research2Video

从资料调研、研究报告和文稿生成，到配音、画面、字幕、视频制作及发布管理的本地工作流。

## 项目结构

| 目录 | 职责 |
| --- | --- |
| `AI_research/Base_CodingCLi/` | 研究任务、信息源、周期调度、文稿配方、视频工作流和管理控制台。 |
| `autovideo/` | 配音、视觉规划、Remotion 渲染、字幕、片头片尾和成片交付。 |

两个模块共用一个 Git 仓库，仍独立安装依赖、运行服务和执行测试。原仓库的提交历史保留在本仓库中。

## 仓库与历史

项目地址：https://github.com/liamamilin/Research2Video

本仓库从两个模块的已提交版本合并：研究控制台 `3469322`，AutoVideo `f64c64f`。通过带父提交的目录导入保留各自历史，源码和运行目录保持原样；后续在根目录统一提交。

## 本地运行

先按两个模块的 README 安装各自依赖，并将供应商密钥写入各自忽略的 `.env` 文件。

启动视频生成服务：

```bash
cd autovideo
python3 -m uvicorn g3.app:app --host 127.0.0.1 --port 8011
```

在另一终端启动研究控制台：

```bash
cd AI_research/Base_CodingCLi
python run_web.py serve
```

控制台端口与网关由 `AI_research/Base_CodingCLi/config/web.yaml` 配置，当前本地入口为 `http://127.0.0.1:8775`。视频服务入口为 `http://127.0.0.1:8011`。

## 开发与验证

研究控制台的检查入口为 `AI_research/Base_CodingCLi/scripts/check.sh`；前端在对应的 `ui/` 目录运行 `npm run dev`。

AutoVideo 的测试、前端构建和真实视频验收说明见其 `AGENTS.md`、`g3/README.md` 与 `RELEASE_STATUS.md`。两个模块的联动改动应一起提交，并分别验证。

## 数据与版本管理

Git 管理源码、配方、任务配置、测试和文档。密钥、虚拟环境、运行数据库、缓存、新生成的视频及临时任务保留在本地；已有历史验收资料保留。

贡献指南见根目录及各模块的 `AGENTS.md`。日常 Git 操作统一在本目录执行。
