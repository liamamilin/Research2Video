"""Matrix ownership and version-aware next actions on the video board."""
from __future__ import annotations

import pytest

from core import matrices
from core.video import reports, store
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401
from tests.test_video_credibility import version
from web.routes.video import home_board, list_scripts


def make_script(job="", *, marks=("CITED",), status="APPROVED", model_ran=True, serious=0):
    topic = {"title": "一条视频"}
    if job:
        with store.connect(write=True) as conn:
            topic["report_id"] = reports._record(conn, f"output/{job}/board.md", job=job)
    script = store.create_script("topic_digest", topic)
    data = version(marks, model_ran=model_ran, serious=serious)
    store.add_version(script["id"], title="一条视频", sections=data["sections"], length_check=data["length_check"],
                      meta=data["meta"], edit_type="GENERATED")
    store.update_script(script["id"], status=status, approved_version=1 if status == "APPROVED" else None)
    return store.get_script(script["id"])


def render(script, *, state="COMPLETED", number=1):
    row = store.create_render(script["id"], number, title="成片", source_text="x", source_sha256="0" * 64, request={})
    store.update_render(row["id"], status=state)


def items():
    return {row["script_id"]: row for row in home_board(user={})["items"]}


def test_same_recipe_does_not_mix_jobs_from_different_matrices(prod_env):
    ai = make_script("research/loose")
    medical = make_script("medical/flash_discovery")
    manual = make_script()
    rows = items()
    assert rows[ai["id"]]["matrix"] == "ai"
    assert rows[medical["id"]]["matrix"] == "medical"
    assert rows[manual["id"]]["matrix"] == "" and rows[manual["id"]]["matrix_name"] == "独立视频"
    assert len({r["column_key"] for r in rows.values()}) == 1
    listed = {r["id"]: r for r in list_scripts(user={})}
    assert listed[ai["id"]]["matrix"] == "ai" and listed[medical["id"]]["matrix"] == "medical"


def test_a_cut_follows_its_archived_parents_origin(prod_env):
    parent = make_script("research/loose")
    store.update_script(parent["id"], archived=1)
    child = make_script()
    store.update_script(child["id"], topic={"title": "切一条", "parent_script": parent["id"]})
    assert items()[child["id"]]["matrix"] == "ai"


def test_archived_matrix_videos_are_not_counted_as_active_work(prod_env):
    script = make_script("research/loose")
    matrix = matrices.load_all(str(prod_env / "config"))["ai"]
    matrices.save(str(prod_env / "config"), "ai", name=matrix.name, domain="", keywords=[], members=matrix.members, archived=True)
    result = home_board(user={})
    assert script["id"] not in {r["script_id"] for r in result["items"]}
    assert "ai" not in {r["key"] for r in result["matrices"]}
    assert not list_scripts(user={})
    assert list_scripts(archived=True, user={})[0]["id"] == script["id"]


def test_finished_video_needing_fact_review_is_not_recommended_for_publication(video_env):
    script = make_script(marks=("CITED", "NEEDS_REVIEW"))
    render(script)
    row = items()[script["id"]]
    assert row["stage"] == "rendered" and row["has_video"]
    assert row["action"] == "review" and row["group"] == "todo"
    attention = home_board(user={})["attention"]
    assert [(a["script_id"], a["kind"]) for a in attention] == [(script["id"], "review")]
    assert store.get_script(script["id"])["status"] == "APPROVED"  # recommendations never mutate approval


def test_verified_current_video_is_ready_for_human_viewing_and_publication(video_env):
    script = make_script()
    render(script)
    row = items()[script["id"]]
    assert row["action"] == "publish" and row["group"] == "publish"


@pytest.mark.parametrize("publication_version", [None, 1])
def test_an_old_render_and_publication_do_not_complete_a_new_draft(video_env, publication_version):
    script = make_script()
    render(script)
    store.add_publication(script["id"], url="https://b23.tv/old", published_on="2026-10-20", version=publication_version)
    assert items()[script["id"]]["group"] == "done"
    data = version(["CITED"])
    store.add_version(script["id"], title="新版", sections=data["sections"], length_check={}, meta=data["meta"], edit_type="EDITED")
    store.update_script(script["id"], status="DRAFT")
    row = items()[script["id"]]
    assert row["version"] == 2 and row["has_previous_video"] and not row["has_video"]
    assert row["action"] == "approve" and row["stage"] == "draft"
    store.update_script(script["id"], status="APPROVED", approved_version=2)
    assert items()[script["id"]]["action"] == "render"
    listed = list_scripts(user={})[0]
    assert listed["current_version"] == 2 and not listed["published"] and listed["render"] is None
    assert listed["has_previous_video"]


def test_audits_in_flight_and_optional_style_advice_are_not_manual_fact_failures(video_env):
    active = make_script(status="DRAFT", model_ran=False)
    store.update_script(active["id"], stage="audit")
    assert items()[active["id"]]["group"] == "working"
    partial = make_script(marks=("CITED", "UNCITED", "UNCITED"))
    render(partial)
    assert items()[partial["id"]]["group"] == "publish"  # advice remains advisory


def test_render_failure_has_a_specific_next_action(video_env):
    script = make_script()
    render(script, state="FAILED")
    row = items()[script["id"]]
    assert row["action"] == "retry_render" and row["group"] == "todo"


def test_attention_does_not_silently_drop_everything_after_thirty_items(video_env):
    for _ in range(35):
        make_script(status="DRAFT")
    board = home_board(user={})
    assert len(board["items"]) == len(board["attention"]) == 35


def test_script_index_does_not_silently_stop_at_one_hundred_scripts(video_env):
    for n in range(105):
        store.create_script("flash", {"title": f"第 {n} 篇"})
    assert len(list_scripts(user={})) == 105


@pytest.mark.parametrize("publication_version", [None, 1])
def test_shipments_use_current_approval_video_and_publication(video_env, publication_version):
    from web.services.video_board import shipments
    script = make_script()
    render(script)
    store.add_publication(script["id"], url="https://b23.tv/old", published_on="2026-10-20", version=publication_version)
    data = version(["CITED"])
    store.add_version(script["id"], title="新版定稿", sections=data["sections"], length_check={}, meta=data["meta"], edit_type="EDITED")
    store.update_script(script["id"], status="APPROVED", approved_version=2)
    row = shipments({})[0]
    assert row["title"] == "新版定稿" and row["render"] is None and row["publications"] == []
    render(store.get_script(script["id"]), number=2)
    render(store.get_script(script["id"]), number=2, state="FAILED")
    assert shipments({})[0]["render"]["status"] == "COMPLETED"


def test_shipments_exclude_archived_matrix_videos(prod_env):
    from web.services.video_board import shipments
    make_script("research/loose")
    matrix = matrices.load_all(str(prod_env / "config"))["ai"]
    matrices.save(str(prod_env / "config"), "ai", name=matrix.name, domain="", keywords=[], members=matrix.members, archived=True)
    assert shipments({}, config_dir=str(prod_env / "config"), jobs_dir=str(prod_env / "jobs")) == []
