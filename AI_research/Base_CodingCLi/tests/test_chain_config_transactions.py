"""Configuration failures must not rewind a production dispatcher's ledger."""

import json
import threading

import pytest

from core.config_transaction import (
    transaction,
    recover,
    before_write,
    remove,
    undo_change,
)
from core.fileio import atomic_write
from core.schedule_store import ScheduleStore


def test_failed_edit_rolls_back_only_its_files_and_schedule(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    state = tmp_path / "state"
    schedules = ScheduleStore(str(state))
    first = schedules.save("first", "0 10 * * *")
    other = schedules.save("other", "0 11 * * *")
    path = config / "first.yaml"
    path.write_text("name: original\n")
    finished = threading.Event()
    failures = []

    def dispatcher():
        try:
            with schedules.connect(True) as db:
                db.execute(
                    "INSERT INTO scheduler_meta VALUES('concurrent_dispatch','retained')"
                )
        except Exception as exc:
            failures.append(exc)
        finally:
            finished.set()

    with pytest.raises(ValueError):
        with transaction(str(state), [config], schedules):
            atomic_write(str(path), "name: changed\n")
            atomic_write(str(config / "new.yaml"), "new: true\n")
            schedules.toggle(first["id"], False)
            thread = threading.Thread(target=dispatcher)
            thread.start()
            assert not finished.wait(0.1)
            raise ValueError("invalid output setting")
    thread.join(15)
    assert finished.is_set() and not failures
    assert path.read_text() == "name: original\n"
    assert not (config / "new.yaml").exists()
    assert schedules.get(first["id"])["enabled"] == 1
    assert schedules.get(other["id"]) == other
    with schedules.connect() as db:
        assert (
            db.execute(
                "SELECT value FROM scheduler_meta WHERE key='concurrent_dispatch'"
            ).fetchone()[0]
            == "retained"
        )


def test_committed_change_is_recovered_without_reverting(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    state = tmp_path / "state"
    schedules = ScheduleStore(str(state))
    target = config / "new.yaml"
    with transaction(str(state), [config], schedules):
        atomic_write(str(target), "enabled: true\n")
    journal = next((state / "config_changes").glob("*.json"))
    row = json.loads(journal.read_text())
    row["status"] = "pending"  # crash after DB commit but before journal completion
    journal.write_text(json.dumps(row))
    recover(str(state), schedules)
    assert target.exists()
    assert json.loads(journal.read_text())["status"] == "committed"


def test_external_edit_is_not_overwritten_on_failure(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    target = config / "test.yaml"
    target.write_text("original")
    schedules = ScheduleStore(str(tmp_path / "state"))
    with pytest.raises(ValueError):
        with transaction(str(tmp_path / "state"), [config], schedules):
            atomic_write(str(target), "ours")
            target.write_text("external CLI edit")
            raise ValueError("failed")
    assert target.read_text() == "external CLI edit"
    with pytest.raises(RuntimeError, match="外部修改"):
        recover(str(tmp_path / "state"), schedules)


def test_remove_and_nested_edit_rollback(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    target = config / "test.yaml"
    target.write_text("original")
    schedules = ScheduleStore(str(tmp_path / "state"))
    with pytest.raises(ValueError):
        with transaction(str(tmp_path / "state"), [config], schedules):
            with transaction(str(tmp_path / "state"), [config], schedules):
                remove(target)
            raise ValueError("failed")
    assert target.read_text() == "original"


def test_failed_later_write_restores_an_intermediate_value(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    path = config / "job.yaml"
    path.write_text("original")
    schedules = ScheduleStore(str(tmp_path / "state"))
    with pytest.raises(OSError):
        with transaction(str(tmp_path / "state"), [config], schedules):
            atomic_write(str(path), "first change")
            before_write(str(path), "second change")
            raise OSError("disk write failed before replace")
    assert path.read_text() == "original"


def test_undo_keeps_execution_history_and_increments_plan_version(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    state = tmp_path / "state"
    path = config / "job.yaml"
    path.write_text("original")
    schedules = ScheduleStore(str(state))
    plan = schedules.save("research", "0 10 * * *")
    with transaction(str(state), [config], schedules):
        atomic_write(str(path), "changed")
        schedules.save(
            "research",
            "0 11 * * *",
            schedule_id=plan["id"],
            editing=True,
            expected_version=plan["version"],
        )
    identifier = next((state / "config_changes").glob("*.json")).stem
    with schedules.connect(True) as db:
        db.execute(
            "INSERT INTO scheduler_meta VALUES('completed-after-edit','preserved')"
        )
    updated = schedules.get(plan["id"])
    undo_change(str(state), identifier, [config], schedules)
    assert path.read_text() == "original"
    restored = schedules.get(plan["id"])
    assert restored["schedule"] == "0 10 * * *"
    assert restored["version"] > updated["version"]
    with schedules.connect() as db:
        assert (
            db.execute(
                "SELECT value FROM scheduler_meta WHERE key='completed-after-edit'"
            ).fetchone()[0]
            == "preserved"
        )


def test_undo_refuses_to_overwrite_a_newer_configuration(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    state = tmp_path / "state"
    schedules = ScheduleStore(str(state))
    path = config / "job.yaml"
    path.write_text("original")
    with transaction(str(state), [config], schedules):
        atomic_write(str(path), "changed")
    identifier = next((state / "config_changes").glob("*.json")).stem
    path.write_text("later edit")
    with pytest.raises(ValueError, match="后续修改"):
        undo_change(str(state), identifier, [config], schedules)
    assert path.read_text() == "later edit"


def test_undo_creation_retains_chain_configuration_when_runs_exist(tmp_path):
    import sqlite3

    config = tmp_path / "config"
    chains = config / "chains"
    chains.mkdir(parents=True)
    state = tmp_path / "state"
    schedules = ScheduleStore(str(state))
    target = chains / "science.yaml"
    with transaction(str(state), [config], schedules):
        atomic_write(str(target), "key: science\noutputs: [science_each]\n")
    identifier = next((state / "config_changes").glob("*.json")).stem
    with sqlite3.connect(state / "chains.db") as db:
        db.execute("CREATE TABLE runs(id TEXT,chain_id TEXT,data TEXT)")
        db.execute("INSERT INTO runs VALUES('finished','science','{}')")
    with pytest.raises(ValueError, match="已有运行或产物"):
        undo_change(str(state), identifier, [config], schedules)
    assert target.exists()
    with sqlite3.connect(state / "chains.db") as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_long_restart_wait_renews_gateway_marker_before_signal(tmp_path, monkeypatch):
    import json
    import time
    from scripts import service_maintenance

    state = tmp_path / 'state'
    state.mkdir()
    runtime = {'pid': 123456, 'instance_id': 'expected'}
    (state / 'app_runtime.json').write_text(json.dumps(runtime))
    spec = {'id': 'probe', 'action': 'shutdown', 'pid': 123456,
            'runtime': runtime, 'state_dir': str(state), 'status': 'applying',
            'created': time.time() - 3600, 'updated': time.time() - 3600}
    receipt = state / 'receipt.json'
    receipt.write_text(json.dumps(spec))
    (state / 'assistant_maintenance.json').write_text(json.dumps(spec))
    observed = []

    def signal(pid, value):
        marker = json.loads((state / 'assistant_maintenance.json').read_text())
        assert marker['status'] == 'applying' and time.time() - marker['updated'] < 5
        observed.append(pid)

    monkeypatch.setattr(service_maintenance.os, 'kill', signal)
    monkeypatch.setattr(service_maintenance.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(service_maintenance, 'process_exited', lambda *_: True)
    service_maintenance._perform_idle(receipt)
    assert observed == [123456]
    assert json.loads(receipt.read_text())['status'] == 'completed'


def test_handoff_retains_prepared_queue_and_holds_native_admission(tmp_path):
    import fcntl
    import sqlite3
    import pytest
    from core.maintenance_work import restart_gate, busy_work

    state = tmp_path / 'state'
    state.mkdir()
    with sqlite3.connect(state / 'video.db') as db:
        db.execute('CREATE TABLE autopilot_runs(id TEXT, status TEXT, stage TEXT)')
        db.execute("INSERT INTO autopilot_runs VALUES ('persisted','RUNNING','ITEMS')")
    assert busy_work(state)
    with restart_gate(state, timeout=1, handoff_waiting_video=True):
        with (state/'autopilot-tick.lock').open('a') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sqlite3.connect(state/'video.db') as db:
            assert db.execute('SELECT * FROM autopilot_runs').fetchall() == [('persisted','RUNNING','ITEMS')]
    with (state/'autopilot-tick.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_handoff_still_waits_for_real_render_and_topic_planner(tmp_path):
    import sqlite3
    import pytest
    from core.maintenance_work import restart_gate

    state = tmp_path / 'state'
    state.mkdir()
    with sqlite3.connect(state/'video.db') as db:
        db.execute('CREATE TABLE autopilot_runs(id TEXT, status TEXT, stage TEXT)')
        db.execute("INSERT INTO autopilot_runs VALUES ('planning','RUNNING','TOPICS')")
        db.execute('CREATE TABLE renders(status TEXT)')
    with pytest.raises(RuntimeError, match='视频规划仍未落库'):
        with restart_gate(state, timeout=.05, handoff_waiting_video=True):
            pytest.fail('cannot discard an in-memory planning result')
    with sqlite3.connect(state/'video.db') as db:
        db.execute("UPDATE autopilot_runs SET stage='ITEMS'")
        db.execute("INSERT INTO renders VALUES ('RUNNING')")
    with pytest.raises(RuntimeError, match='现有研究、制作或发布工作尚未结束'):
        with restart_gate(state, timeout=.05, handoff_waiting_video=True):
            pytest.fail('cannot stop a real render')


def test_handoff_propagates_body_timeout_without_second_handover(tmp_path):
    import pytest
    from core.maintenance_work import restart_gate

    with pytest.raises(TimeoutError, match='handover body'):
        with restart_gate(tmp_path, timeout=1, handoff_waiting_video=True):
            raise TimeoutError('handover body')


def test_handoff_defers_video_observer_but_not_chain_research_or_cancellation(tmp_path):
    import json
    import sqlite3
    from core.maintenance_work import busy_work

    with sqlite3.connect(tmp_path/'chains.db') as db:
        db.execute('CREATE TABLE runs(id TEXT, data TEXT)')
        db.execute('INSERT INTO runs VALUES (?,?)', ('observer', json.dumps({'status':'RUNNING','stage':'VIDEO','batch_id':'native-batch'})))
    assert busy_work(tmp_path)
    assert not busy_work(tmp_path, include_waiting_video=False)
    with sqlite3.connect(tmp_path/'chains.db') as db:
        db.execute('INSERT INTO runs VALUES (?,?)', ('research', json.dumps({'status':'RUNNING','stage':'RESEARCH'})))
    assert busy_work(tmp_path, include_waiting_video=False)[0]['count'] == 1
    with sqlite3.connect(tmp_path/'chains.db') as db:
        db.execute('UPDATE runs SET data=? WHERE id=?',(json.dumps({'status':'CANCELLING','stage':'VIDEO','batch_id':'native-batch'}),'observer'))
    assert busy_work(tmp_path, include_waiting_video=False)[0]['count'] == 2
