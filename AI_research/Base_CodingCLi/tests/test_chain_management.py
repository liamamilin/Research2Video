"""Exercise production routes against isolated business configuration."""

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def chain_env(web_env, monkeypatch):
    root, _ = web_env
    source = Path(__file__).resolve().parents[1]
    for folder in ("config", "jobs"):
        for path in (source / folder).rglob("*.yaml"):
            if "backups" in path.parts:
                continue
            target = root / folder / path.relative_to(source / folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    from core.video import store
    from core import lock
    from web.services import managed_scheduler

    store.use_state_dir(str(root / "state"))
    store.init_db()
    monkeypatch.setattr(lock, "_LOCK_DIR", str(root / "state/locks"))
    monkeypatch.setattr(managed_scheduler, "ensure_online", lambda: {"online": True})
    (root / "config/chain_access.yaml").write_text(
        'mode: enabled\nallow_create: true\nwritable_chains: ["*"]\n'
    )
    yield root


def login(client, role="admin"):
    response = client.post(
        "/api/auth/login", json={"username": role, "password": role + "-pass-123"}
    )
    assert response.status_code == 200
    client.headers["x-csrf-token"] = client.cookies.get("ai_research_csrf", "")


BODY = {
    "key": "chain_acceptance",
    "name": "科研接入验收",
    "prompt": "研究AI推进科学的具体变化与应用",
    "sources": ["source:cg_openai_news"],
    "output_modes": ["individual", "episode"],
    "schedule": "",
}


def test_create_edit_shared_delete_keeps_existing_configuration(chain_env, client):
    login(client)
    baseline = {
        p: p.read_bytes()
        for folder in ("production", "matrices")
        for p in (chain_env / "config" / folder).glob("*.yaml")
    }
    response = client.post("/api/chains", json=BODY)
    assert response.status_code == 201, response.text
    first = response.json()
    assert len(first["outputs_detail"]) == 2
    assert all(not p["errors"] for p in first["outputs_detail"])
    shared = client.post(
        "/api/chains",
        json={**BODY, "key": "shared_acceptance", "existing_job": first["job"]},
    )
    assert shared.status_code == 201, shared.text
    first = client.get("/api/chains/" + first["key"]).json()
    failed = client.put(
        "/api/chains/" + first["key"],
        json={"revision": first["revision"], "prompt": "共享变更"},
    )
    assert failed.status_code == 409
    first = client.get("/api/chains/" + first["key"]).json()
    deleted = client.delete(
        "/api/chains/" + first["key"], params={"revision": first["revision"]}
    )
    assert deleted.status_code == 200, deleted.text
    assert (chain_env / "jobs" / (first["job"] + ".yaml")).exists()
    second = client.get("/api/chains/shared_acceptance").json()
    deleted = client.delete(
        "/api/chains/shared_acceptance", params={"revision": second["revision"]}
    )
    assert deleted.status_code == 200, deleted.text
    assert not (chain_env / "jobs" / (first["job"] + ".yaml")).exists()
    assert all(p.read_bytes() == raw for p, raw in baseline.items())
    assert not (chain_env / "config/matrices/chain_acceptance.yaml").exists()
    assert not (chain_env / "config/video_recipes/chain_chain_acceptance.yaml").exists()


def test_invalid_schedule_rolls_back_new_configuration(chain_env, client):
    login(client)
    before = {
        p: p.read_bytes()
        for folder in ("config", "jobs")
        for p in (chain_env / folder).rglob("*.yaml")
    }
    response = client.post("/api/chains", json={**BODY, "schedule": "invalid"})
    assert response.status_code == 422, response.text
    after = {
        p: p.read_bytes()
        for folder in ("config", "jobs")
        for p in (chain_env / folder).rglob("*.yaml")
    }
    assert after == before


def test_rollout_readonly_and_viewer_permissions(chain_env, client):
    login(client)
    (chain_env / "config/chain_access.yaml").write_text("mode: read_only\n")
    assert client.get("/api/chains").status_code == 200
    assert client.post("/api/chains", json=BODY).status_code == 409
    login(client, "viewer")
    assert client.post("/api/chains", json=BODY).status_code == 403


def test_imported_delete_archives_entry_without_stopping_production(chain_env, client):
    login(client)
    listing = client.get("/api/chains").json()
    flash = next(
        x for x in listing["items"] if x["job"] == "monitoring/ai_flash_discovery"
    )
    detail = client.get("/api/chains/" + flash["key"]).json()
    assert len(detail["outputs_detail"]) == 7
    before = {
        p: p.read_bytes() for p in (chain_env / "config/production").glob("*.yaml")
    }
    preview = client.get("/api/chains/" + flash["key"] + "/delete-preview").json()
    assert preview["archive_only"] is True
    response = client.delete(
        "/api/chains/" + flash["key"], params={"revision": detail["revision"]}
    )
    assert response.status_code == 200, response.text
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert (chain_env / "jobs/monitoring/ai_flash_discovery.yaml").exists()


def test_native_schedule_execution_appears_in_chain_history(chain_env, client):
    import os
    from web.services import chains

    login(client)
    response = client.post("/api/chains", json={**BODY, "output_modes": ["individual"]})
    assert response.status_code == 201, response.text
    chain = response.json()
    schedules = chains.schedule_store()
    plan = schedules.save(chain["job"], "0 10 * * *")
    due = plan["next_run_at"]
    schedules.enqueue_due(now=due)
    run = schedules.claim(now=due)
    assert run is not None
    schedules.start(run["id"], os.getpid(), now=due)
    active = client.get("/api/chains/" + chain["key"]).json()
    projected = next(
        row for row in active["runs"] if row.get("schedule_run_id") == run["id"]
    )
    assert projected["status"] == "RUNNING" and projected["native_schedule"]
    schedules.finish(
        run["id"],
        "success",
        output_path=str(chain_env / "output/research.md"),
        now=due + 1,
    )
    finished = client.get("/api/chains/runs/" + projected["id"])
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "DONE"


def test_whole_report_output_reuses_recipe_and_can_be_readded(chain_env, client):
    from core.video import production

    login(client)
    production.set_auto(str(chain_env / "config"), "economy_illustrated", 0)
    baseline = {
        p: p.read_bytes()
        for folder in ("production", "video_recipes", "matrices")
        for p in (chain_env / "config" / folder).glob("*.yaml")
    }
    job_path = chain_env / "jobs/economy/economy_illustrated.yaml"
    job_before = job_path.read_bytes()
    listing = client.get("/api/chains").json()
    chain = next(
        x for x in listing["items"] if x["job"] == "economy/economy_illustrated"
    )
    url = "/api/chains/" + chain["key"]
    detail = client.get(url).json()
    added = client.put(
        url,
        json={
            "revision": detail["revision"],
            "add_output": {"mode": "whole", "name": "经济插画新输出"},
        },
    )
    assert added.status_code == 200, added.text
    detail = added.json()
    output = next(
        x for x in detail["outputs_detail"] if x["key"] != "economy_illustrated"
    )
    assert output["base_recipe"] == "illustrated_explainer"
    assert output["split"] == "whole" and output["auto"] == 0 and not output["errors"]
    assert output["audience_key"] == "general"
    assert output["render"]["illustration_style"] == "geometric"
    edited = client.put(
        url,
        json={
            "revision": detail["revision"],
            "outputs": [
                {
                    "key": output["key"],
                    "auto": 0,
                    "render": {**output["render"], "video_speed": 1.25},
                }
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    removed = client.put(
        url,
        json={"revision": edited.json()["revision"], "remove_outputs": [output["key"]]},
    )
    assert removed.status_code == 200, removed.text
    assert all(p.read_bytes() == raw for p, raw in baseline.items())
    assert job_path.read_bytes() == job_before
    # Removing the last output must retain the template needed to add one back.
    removed = client.put(
        url,
        json={
            "revision": removed.json()["revision"],
            "remove_outputs": ["economy_illustrated"],
        },
    )
    assert removed.status_code == 200, removed.text
    readded = client.put(
        url,
        json={
            "revision": removed.json()["revision"],
            "add_output": {"mode": "whole", "name": "恢复整份报告输出"},
        },
    )
    assert readded.status_code == 200, readded.text
    assert readded.json()["outputs_detail"][0]["base_recipe"] == "illustrated_explainer"


def test_resume_automatic_outputs_keeps_manual_roundup_manual(chain_env, client):
    from core.video import production

    login(client)
    listing = client.get("/api/chains").json()
    chain = next(
        x for x in listing["items"] if x["job"] == "monitoring/ai_flash_discovery"
    )
    detail = client.get("/api/chains/" + chain["key"]).json()
    for pid in detail["outputs"]:
        production.set_auto(str(chain_env / "config"), pid, 0)
    detail = client.get("/api/chains/" + chain["key"]).json()
    response = client.post(
        "/api/chains/" + chain["key"] + "/toggle",
        json={"revision": detail["revision"], "scope": "outputs", "enabled": True},
    )
    assert response.status_code == 200, response.text
    outputs = response.json()["outputs_detail"]
    manual = [o for o in outputs if o["split"] == "some"]
    assert manual and all(o["auto"] == 0 for o in manual)
    assert all(not o["errors"] for o in outputs)
    assert any(o["auto"] for o in outputs)


def test_failed_native_slot_reruns_frozen_research_with_current_outputs(
    chain_env, client, monkeypatch
):
    import copy
    import os
    from types import SimpleNamespace
    from web.services import chains, chain_runner
    from core.config import load_job

    login(client)
    chain = client.post(
        "/api/chains", json={**BODY, "output_modes": ["individual"]}
    ).json()
    schedules = chains.schedule_store()
    plan = schedules.save(chain["job"], "0 10 * * *", max_retries=0)
    due = plan["next_run_at"]
    schedules.enqueue_due(now=due)
    run = schedules.claim(now=due)
    frozen = {
        "prepared": True,
        "issue_id": "original-slot",
        "research_window": {
            "start": "2026-11-17T10:00:00+08:00",
            "end": "2026-11-18T10:00:00+08:00",
        },
        "job": copy.deepcopy(load_job(str(chain_env / "jobs"), chain["job"])),
    }
    frozen = schedules.freeze_context(run["id"], lambda _: frozen)
    schedules.start(run["id"], os.getpid(), now=due)
    schedules.finish(run["id"], "failed", error="provider unavailable", now=due + 1)
    detail = client.get("/api/chains/" + chain["key"]).json()
    projected = next(x for x in detail["runs"] if x.get("schedule_run_id") == run["id"])
    assert projected["status"] == "FAILED"
    changed_job = client.put(
        "/api/chains/" + chain["key"],
        json={
            "revision": detail["revision"],
            "prompt": "新的未来研究目标，不应替换旧期次",
        },
    )
    assert changed_job.status_code == 200
    captured = {}

    def launch(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(task_id="frozen-retry-task")

    monkeypatch.setattr(chain_runner, "start_job", launch)
    response = client.post("/api/chains/runs/" + projected["id"] + "/retry")
    assert response.status_code == 200, response.text
    replay = response.json()
    assert replay["outputs"] == chain["outputs"]
    assert replay["retry_of"] == projected["id"]
    assert captured["run_context"]["job"] == frozen["job"]
    assert captured["run_context"]["research_window"] == frozen["research_window"]
    assert captured["run_context"]["chain_run_id"] == replay["id"]
    assert replay["snapshot"]["research_context"]["issue_id"] == frozen["issue_id"]
    assert schedules.run(run["id"])["status"] == "failed"


def test_legacy_tabs_keep_output_ownership_across_shared_chain_creation_and_removal(
    chain_env, client
):
    login(client)
    first = client.post(
        "/api/chains", json={**BODY, "output_modes": ["individual"]}
    ).json()
    second = client.post(
        "/api/chains",
        json={
            **BODY,
            "key": "shared_output_scope",
            "existing_job": first["job"],
            "output_modes": ["individual"],
        },
    ).json()
    original_second = second["outputs"][:]
    # The matrix's production editor sends its explicit chain context.
    made = client.post(
        "/api/production",
        json={
            "job": first["job"],
            "recipe": "chain_chain_acceptance",
            "reverify": False,
            "auto": 0,
            "chain_key": second["key"],
        },
    )
    assert made.status_code == 201, made.text
    pid = made.json()["key"]
    second = client.get("/api/chains/" + second["key"]).json()
    first = client.get("/api/chains/" + first["key"]).json()
    assert pid in second["outputs"] and pid not in first["outputs"]
    assert (
        pid in client.get("/api/chains/" + second["key"] + "/scope").json()["outputs"]
    )
    removed = client.delete("/api/production/" + pid)
    assert removed.status_code == 200, removed.text
    second = client.get("/api/chains/" + second["key"]).json()
    assert second["outputs"] == original_second
    assert pid in second["retired_outputs"]
    # Recreating through the legacy page must not reuse a retired identity.
    again = client.post(
        "/api/production",
        json={
            "job": first["job"],
            "recipe": "chain_chain_acceptance",
            "reverify": False,
            "auto": 0,
            "chain_key": second["key"],
        },
    )
    assert again.status_code == 201, again.text
    assert again.json()["key"] != pid
    bad = client.post(
        "/api/production",
        json={
            "job": "economy/economy_illustrated",
            "recipe": "illustrated_explainer",
            "audience": "general",
            "auto": 0,
            "chain_key": second["key"],
        },
    )
    assert bad.status_code == 422, bad.text


def test_persisted_imported_chain_adopts_outputs_added_by_global_legacy_tab(
    chain_env, client
):
    login(client)
    listing = client.get("/api/chains").json()
    imported = next(
        x for x in listing["items"] if x["job"] == "economy/economy_illustrated"
    )
    url = "/api/chains/" + imported["key"]
    detail = client.get(url).json()
    saved = client.put(
        url, json={"revision": detail["revision"], "name": "已管理的经济插画"}
    )
    assert saved.status_code == 200, saved.text
    created = client.post(
        "/api/production",
        json={
            "job": detail["job"],
            "recipe": "illustrated_explainer",
            "audience": "general",
            "auto": 0,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["key"] in client.get(url).json()["outputs"]
    removed = client.delete("/api/production/" + created.json()["key"])
    assert removed.status_code == 200, removed.text
    fresh = client.get(url).json()
    assert created.json()["key"] not in fresh["outputs"]
    summary = next(
        x
        for x in client.get("/api/chains").json()["items"]
        if x["key"] == imported["key"]
    )
    assert summary["output_count"] == len(fresh["outputs_detail"])


def test_history_list_and_detail_agree_on_held_and_skipped_items(chain_env, client):
    from core.video import autopilot_store as ap

    login(client)
    chain = client.post(
        "/api/chains", json={**BODY, "output_modes": ["individual"]}
    ).json()
    pid = chain["outputs"][0]
    batch = ap.create_run(
        {"picks": [{"production": pid}], "job": chain["job"]},
        [
            {"column_key": "fixture", "topic": {"id": "first"}, "production": pid},
            {"column_key": "fixture", "topic": {"id": "second"}, "production": pid},
        ],
    )
    ap.update_item(batch["items"][0]["id"], state="DONE")
    ap.update_item(batch["items"][1]["id"], state="HELD")
    ap.update_run(batch["id"], status="DONE")
    detail = client.get("/api/chains/" + chain["key"]).json()
    projected = next(r for r in detail["runs"] if r.get("batch_id") == batch["id"])
    assert projected["status"] == "HELD"
    assert client.get("/api/chains/runs/" + projected["id"]).json()["status"] == "HELD"
    ap.update_item(batch["items"][1]["id"], state="CANCELLED")
    detail = client.get("/api/chains/" + chain["key"]).json()
    projected = next(r for r in detail["runs"] if r.get("batch_id") == batch["id"])
    assert projected["status"] == "DONE"
    assert client.get("/api/chains/runs/" + projected["id"]).json()["status"] == "DONE"


def test_chain_start_and_retry_respect_existing_service_maintenance(
    chain_env, client, monkeypatch
):
    import json
    import os
    from web.services import chain_runner

    login(client)
    chain = client.post(
        "/api/chains", json={**BODY, "output_modes": ["individual"]}
    ).json()
    (chain_env / "state/assistant_maintenance.json").write_text(
        json.dumps({"pid": os.getpid(), "action": "restart", "status": "applying"})
    )
    monkeypatch.setattr(
        chain_runner,
        "start",
        lambda *_a, **_k: pytest.fail("maintenance must prevent new work"),
    )
    monkeypatch.setattr(
        chain_runner,
        "retry",
        lambda *_a, **_k: pytest.fail("maintenance must prevent retry"),
    )
    response = client.post("/api/chains/" + chain["key"] + "/run", json={})
    assert response.status_code == 409 and "服务维护" in response.text
    monkeypatch.setattr(chain_runner, "get", lambda _: {"chain_id": chain["key"]})
    response = client.post("/api/chains/runs/blocked-retry/retry")
    assert response.status_code == 409 and "服务维护" in response.text
