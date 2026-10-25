"""Article inputs join the existing version, review, approval and render workflow."""
from __future__ import annotations

from core.video import article_writer, articles, columns, store
from core.video.adapt import review
from web.settings import get_settings


def source_of(script: dict) -> dict:
    return articles.read(get_settings().paths.state_dir, script["topic"]["article"]["source"])


def write(script: dict, *, started_by: str, llm_config=None) -> str:
    from web.services import video_workflow
    column = columns.get(get_settings().paths.config_dir, script["column_key"])
    article = script["topic"]["article"]
    source = source_of(script)
    config, review_config = video_workflow._llm_configs(llm_config, column)
    store.update_script(script["id"], status="WRITING", stage="整理原文" if article["mode"] == "read" else "文章改编", error=None)
    written = article_writer.draft(source, mode=article["mode"], kind=article["kind"], topic=script["topic"],
                                   column=column, config=config)
    initial = review.assemble([], None, None, None, written.sections)
    initial.update(scope="article_fidelity", external_facts_checked=False)
    meta = {**written.meta, "input_kind": "article", "article": article,
            "cell": columns.provenance(get_settings().paths.config_dir, column.key), "checks": initial}
    store.add_version(script["id"], title=written.title, sections=written.sections,
                      length_check=written.length_check, edit_type="GENERATED", meta=meta, created_by=started_by)
    store.update_script(script["id"], status="DRAFT", stage="原文忠实度审校")
    return audit(store.get_script(script["id"]), review_config=review_config or config)


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
