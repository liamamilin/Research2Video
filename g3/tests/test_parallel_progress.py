from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import threading
from uuid import uuid4

import pytest

from g3.config import Settings
from g3.store import JobStore
from g3.worker import G2ProcessRunner, read_progress_lines


def test_progress_waits_for_a_complete_line_and_preserves_split_utf8():
    stream = io.BytesIO(b"STEP tts-and-align: PA")
    lines, pending = read_progress_lines(stream, b"")
    assert lines == []
    stream.write(b"SS in 1.23s\nG2 FAIL: TTS_EMPTY_AUDIO: " + "缺失".encode()[:2])
    stream.seek(len(b"STEP tts-and-align: PA"))
    lines, pending = read_progress_lines(stream, pending)
    assert lines == ["STEP tts-and-align: PASS in 1.23s"]
    end = stream.tell()
    stream.write("缺失".encode()[2:])
    stream.seek(end)
    lines, pending = read_progress_lines(stream, pending, final=True)
    assert lines == ["G2 FAIL: TTS_EMPTY_AUDIO: 缺失"] and pending == b""
    assert read_progress_lines(stream, pending, final=True) == ([], b"")


def test_worker_stops_its_process_if_progress_monitoring_crashes(tmp_path, monkeypatch):
    store, jid, claim = make_job(tmp_path)
    fake = tmp_path / "held_g2.py"
    fake.write_text("import time\ntime.sleep(30)\n")
    stopped = []
    settings = replace(Settings(data_dir=store.data_dir), g2_python=Path(sys.executable), g2_script=fake)
    runner = G2ProcessRunner(settings, store)
    stop = runner._stop_process
    def record(process):
        stop(process)
        stopped.append(process.poll())
    monkeypatch.setattr(runner, "_stop_process", record)
    monkeypatch.setattr("g3.worker.read_progress_lines", lambda *a, **kw: (_ for _ in ()).throw(OSError("log unavailable")))
    runner.execute(claim, threading.Event())
    assert len(stopped) == 1 and stopped[0] is not None
    assert store.get_job(jid)["status"] == "FAILED"


def make_job(tmp_path):
    store = JobStore(tmp_path / "data")
    store.initialize()
    jid = str(uuid4())
    store.create_job(jid, {"title": "并行阶段验收", "source_text": "这是一段有足够字数的中文素材。" * 8,
        "target_duration_sec": 18, "theme": "minimal_dark", "voice_id": "zf_xiaoxiao",
        "packaging": {"enabled": False}})
    claim = store.claim_next()
    return store, jid, claim


def stages(store, jid):
    return {s["stage"]: s for s in store.get_job(jid)["attempts"][-1]["stages"]}


def test_summary_returns_to_voice_when_storyboard_finishes_first(tmp_path):
    store, jid, _ = make_job(tmp_path)
    store.update_stage(jid, 1, "PLAN", "SUCCEEDED", .1)
    store.update_stage(jid, 1, "VOICE_ALIGN", "RUNNING")
    store.update_stage(jid, 1, "STORYBOARD", "RUNNING")
    assert store.get_job(jid)["active_stage"] == "VOICE_ALIGN"
    before = stages(store, jid)["VOICE_ALIGN"]["started_at"]
    store.update_stage(jid, 1, "STORYBOARD", "SUCCEEDED", 5)
    assert store.get_job(jid)["active_stage"] == "VOICE_ALIGN"
    assert stages(store, jid)["VOICE_ALIGN"]["started_at"] == before
    store.update_stage(jid, 1, "VOICE_ALIGN", "SUCCEEDED", 8)
    assert stages(store, jid)["STORYBOARD"]["duration_seconds"] == 5


def test_aborting_parallel_work_interrupts_all_other_running_stages(tmp_path):
    store, jid, _ = make_job(tmp_path)
    store.update_stage(jid, 1, "PLAN", "SUCCEEDED", .1)
    store.update_stage(jid, 1, "VOICE_ALIGN", "RUNNING")
    store.update_stage(jid, 1, "STORYBOARD", "RUNNING")
    store.mark_failed(jid, 1, "VOICE_ALIGN", "GENERATION_TIMEOUT", "timeout", True)
    state = stages(store, jid)
    assert state["VOICE_ALIGN"]["status"] == "FAILED"
    assert state["STORYBOARD"]["status"] == "INTERRUPTED"
    assert not any(s["status"] == "RUNNING" for s in state.values())


@pytest.mark.parametrize("mode", ["storyboard-first", "voice-first", "voice-fails"])
def test_worker_understands_parallel_start_and_out_of_order_finish(tmp_path, monkeypatch, mode):
    store, jid, claim = make_job(tmp_path)
    fake = tmp_path / "parallel_g2.py"
    fake.write_text('''import os, sys
from pathlib import Path
art = Path(os.environ["AUTOVIDEO_G2_RUN_DIR"]) / "artifacts"
art.mkdir(parents=True)
print("STEP narration-plan: START", flush=True)
print("STEP narration-plan: PASS in 0.10s", flush=True)
print("STEP tts-and-align: START", flush=True)
print("STEP storyboard-plan: START", flush=True)
mode = os.environ["AUTOVIDEO_TEST_PARALLEL_MODE"]
if mode == "voice-fails":
    print("STEP tts-and-align: FAIL: TTS_EMPTY_AUDIO: empty", flush=True)
    print("STEP storyboard-plan: PASS in 0.30s", flush=True)
    print("G2 FAIL: TTS_EMPTY_AUDIO: empty", flush=True)
    sys.exit(1)
order = ["storyboard-plan", "tts-and-align"] if mode == "storyboard-first" else ["tts-and-align", "storyboard-plan"]
for step in order:
    print(f"STEP {step}: PASS in 0.20s", flush=True)
print("STEP draw-things-backgrounds: SKIPPED", flush=True)
for step in ["compile-ir", "render", "verify-media"]:
    print(f"STEP {step}: START", flush=True)
    print(f"STEP {step}: PASS in 0.10s", flush=True)
print("STEP package: SKIPPED", flush=True)
(art / "qa.json").write_text('{"status":"PASS"}')
(art / "G2_自动生成.mp4").write_bytes(b"video")
''')
    monkeypatch.setenv("AUTOVIDEO_TEST_PARALLEL_MODE", mode)
    observed = []
    update = store.update_stage

    def record(*args, **kwargs):
        update(*args, **kwargs)
        observed.append((args[2], args[3], store.get_job(jid)["active_stage"], stages(store, jid)))

    monkeypatch.setattr(store, "update_stage", record)
    settings = replace(Settings(data_dir=store.data_dir), g2_python=Path(sys.executable), g2_script=fake)
    G2ProcessRunner(settings, store).execute(claim, threading.Event())
    overlapping = next(state for stage, status, _, state in observed if stage == "STORYBOARD" and status == "RUNNING")
    assert overlapping["VOICE_ALIGN"]["status"] == overlapping["STORYBOARD"]["status"] == "RUNNING"
    job = store.get_job(jid)
    if mode == "voice-fails":
        assert job["status"] == "FAILED" and job["active_stage"] == "VOICE_ALIGN"
        assert job["error_code"] == "TTS_EMPTY_AUDIO"
        assert stages(store, jid)["STORYBOARD"]["status"] == "SUCCEEDED"
    else:
        assert job["status"] == "COMPLETED"
        assert stages(store, jid)["VOICE_ALIGN"]["status"] == stages(store, jid)["STORYBOARD"]["status"] == "SUCCEEDED"
        if mode == "storyboard-first":
            assert next(active for stage, status, active, _ in observed if stage == "STORYBOARD" and status == "SUCCEEDED") == "VOICE_ALIGN"
