# 成片与发布动线修复（2026-10-30）

实现和隔离验收完成，正式加载待安全空闲窗口；当前不重启生产服务、不提交真实投稿。

[完整操作验收、截图与保留记录](/Users/milin/2026/软件开发/视频workflow/AI_research/Base_CodingCLi/state/validation/publish-workflow-20261030-91f0c7de/README.md) · [机器可读验证结果](/Users/milin/2026/软件开发/视频workflow/AI_research/Base_CodingCLi/state/validation/publish-workflow-20261030-91f0c7de/verification.json) · [正式加载边界](/Users/milin/2026/软件开发/视频workflow/AI_research/Base_CodingCLi/state/validation/publish-workflow-20261030-91f0c7de/source-status.json)

后端补丁 `backend.patch` 包含具体成片绑定、保留历史成片查询及对应回归测试。已在隔离 source 中通过 206 项发布相关测试，补丁对当前工作区 `git apply --check` 通过。不要用整份隔离源码覆盖正式目录。

正式合入前重新检查所有研究、渲染、自动生产、调度与投稿工作；使用既有安全维护方式加载。当前存在在途工作，尚未应用此补丁或替换正式静态产物。前端源码已完成；配套构建保存在上述验收目录 `ui-preview/`。

**10-31 合入**：后端由 429de0d 另行实现（同一契约：具体成片绑定、保留历史成片），本补丁中的成片文件缺失返回 404 和 `tests/test_publish_workflow.py` 已并入；`backend.patch` 留作记录，不需要再应用。前端源码与此次后端一同提交，控制台 10-31 11:14 已加载。
