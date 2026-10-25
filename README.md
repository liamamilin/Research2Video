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

Git 管理源码、配方、任务配置、测试、文档和经过检查的轻量验收资料。以下内容保留在本地，由根目录和模块的 `.gitignore` 共同排除：

- 真实 `.env`、密钥、Cookie／Token、平台凭证及浏览器登录态。`.env.example` 只保存占位值与环境变量说明。
- 运行数据库及 WAL／SHM 文件、配置写入锁、虚拟环境、依赖目录、缓存和模型权重。
- 生成的视频、配音、字幕烧录中间文件、运行目录和原始日志。验收说明、经过检查的截图及精简 QA 记录可以保留；含账号信息或原文的记录需先检查。

音视频默认不进入 Git。当前例外仅有 8 个小型、可复用的音频素材：G1 的完整重放音频及 5 个分段、片头提示音 `synth_sting.wav`、默认配音参考音 `voxcpm2_anchor.wav`。它们在根 `.gitignore` 中逐一列出；新增素材需要检查用途、来源和大小后再明确放行。

历史验收报告中引用的成片、日志和实验目录是本地归档路径，干净克隆不包含这些运行产物；按模块说明重新生成。移除 Git 跟踪不会删除工作区中的原文件，也不会清除旧提交里的副本，因此本次整理不会立即缩小完整历史的克隆体积。不要使用 `git add -f` 绕过这些规则加入真实凭证或本地运行产物。

贡献指南见根目录及各模块的 `AGENTS.md`。日常 Git 操作统一在本目录执行。
