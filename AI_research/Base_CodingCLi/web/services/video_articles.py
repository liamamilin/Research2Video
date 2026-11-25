"""Article inputs join the existing version, review, approval and render workflow."""
from __future__ import annotations

from core import telemetry
from core.video import article_writer, articles, columns, store
from core.video.adapt import review
from web.settings import get_settings


def source_of(script: dict) -> dict:
    return articles.read(get_settings().paths.state_dir, script["topic"]["article"]["source"])


def write(script: dict, *, started_by: str, llm_config=None) -> str:
    from dataclasses import replace
    from core.config import load_system_config
    from core.video import content_columns, manuscript_workflow, production
    from web.services import video_executor, video_render, video_workflow

    paths = get_settings().paths
    column = columns.get(paths.config_dir, script["column_key"])
    article = script["topic"]["article"]
    source = source_of(script)
    config, review_config = video_workflow._llm_configs(llm_config, column)

    video_cfg = (load_system_config(paths.config_dir).get("video") or {})
    workflow_mode = video_cfg.get("manuscript_workflow") if article["mode"] == "adapt" else None
    headline_polish = bool(video_cfg.get("headline_polish", True)) if article["mode"] == "adapt" else False

    ctx = dict(script.get("content_context") or {})
    if not ctx and script["topic"].get("article_production"):
        prod = production.get(paths.config_dir, paths.jobs_dir, script["topic"]["article_production"])
        if prod and prod.usable:
            ctx = content_columns.snapshot(paths.config_dir, prod.key, prod.job)

    profile = None
    effective_column = column
    if article["mode"] == "adapt":
        script_for_profile = {**script, "content_context": ctx}
        profile = manuscript_workflow.profile_for(script_for_profile, paths.config_dir, paths.jobs_dir, video_cfg)
        effective_ctx = {**ctx, "notes": profile.draft} if (profile and profile.draft and ctx.get("column")) else ctx
        effective_column = content_columns.apply_script_context(column, effective_ctx)
        if profile and profile.draft and not effective_ctx.get("column"):
            effective_column = replace(effective_column, script_style=effective_column.script_style + "\n" + profile.draft)
    else:
        effective_ctx = ctx

    store.update_script(script["id"], status="WRITING", stage="整理原文" if article["mode"] == "read" else "文章改编与精修", error=None)
    with telemetry.track("write"):
        written = article_writer.draft(
            source, mode=article["mode"], kind=article["kind"], topic=script["topic"],
            column=effective_column, config=config,
            content_context=effective_ctx or None,
            workflow_mode=workflow_mode,
            profile=profile,
            headline_polish=headline_polish,
        )
    local = article_writer.local_issues(written.sections, source, article["mode"], article["kind"])
    checks = review.assemble(local, None, None, None, written.sections)
    checks.update(
        scope="article_fidelity",
        fidelity_checked=True,
        external_facts_checked=False,
        model_ran=False,
        audits_complete=True,
        rounds=1,
        deletions=[],
        initial=[],
        article_summary="已完成口播改编与独立读者听感精修，未做外部事实核验" if article["mode"] == "adapt"
        else "已按原文分段整理，未做外部事实核验",
        content_sha256=article_writer.script_digest(written.title, written.sections),
        source_sha256=source["sha256"],
    )
    checks["summary"] = review.summarize(checks, len(written.sections))
    meta = {**written.meta, "input_kind": "article", "article": article,
            "cell": columns.provenance(paths.config_dir, column.key), "checks": checks}
    version_num = store.add_version(script["id"], title=written.title, sections=written.sections,
                                    length_check=written.length_check, edit_type="GENERATED", meta=meta, created_by=started_by)
    store.update_script(script["id"], status="DRAFT", stage=None, error=None)

    if article.get("auto_render"):
        video_executor.approve(script["id"], version_num, started_by)
        render = video_render.create(store.get_script(script["id"]))
        video_render.start(render["id"])
        return "APPROVED"
    return "DRAFT"


def audit(script: dict, *, review_config=None) -> str:
    from web.services import video_workflow
    version = store.get_version(script["id"], script["current_version"])
    article = version["meta"]["article"]
    if review_config is None:
        column = columns.get(get_settings().paths.config_dir, script["column_key"])
        config, review_config = video_workflow._llm_configs(None, column)
        review_config = review_config or config
    source = source_of(script)
    checks = article_writer.check(source, title=version["title"], sections=version["sections"], mode=article["mode"], kind=article["kind"],
                                  config=review_config)
    store.update_version_audit(script["id"], version["version"], sections=version["sections"],
                               meta={**version["meta"], "checks": checks})
    store.update_script(script["id"], status="DRAFT", stage=None, error=None)
    return "DRAFT"
